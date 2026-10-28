"""
Notificări push (Web Push) pentru alarme: apar în centrul de notificări Windows / Android
chiar dacă pagina e închisă, cât timp browserul rulează.

Cheia de semnare (VAPID) se generează la prima pornire în vapid_private.pem.
Dispozitivele abonate se salvează în push_subscriptions.json.
"""
# =====================================================================================
#  CUM FUNCȚIONEAZĂ NOTIFICĂRILE PUSH (pe scurt)
# -------------------------------------------------------------------------------------
#  1. În pagină apeși „Activează notificări”. Browserul (Chrome/Edge) se înscrie la serviciul
#     lui de push (Google / Microsoft) și primește un „abonament”: o adresă unică (endpoint)
#     + două chei de criptare. Pagina ne trimite acest abonament, iar noi îl salvăm.
#  2. Când o alarmă se declanșează, serverul nostru trimite mesajul la acea adresă.
#     Mesajul e CRIPTAT cu cheile browserului (doar browserul îl poate citi) și SEMNAT cu
#     cheia noastră VAPID (ca serviciul de push să știe că vine de la noi).
#  3. Serviciul de push îl livrează browserului, care îl dă „service worker”-ului (sw.js),
#     iar acesta afișează notificarea în Windows – chiar dacă tab-ul e închis.
#
#  Biblioteci:
#   - cryptography : generarea și citirea cheii VAPID (criptografie pe curbe eliptice).
#   - pywebpush    : criptarea mesajului și trimiterea lui după standardul Web Push.
# =====================================================================================
# base64    : codificarea cheii publice ca text, în formatul cerut de browser.
# json      : salvarea abonamentelor și conținutul notificărilor.
# threading : lacăt pentru acces sigur + trimitere în fundal.
import base64
import json
import threading
from datetime import datetime
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pywebpush import WebPushException, webpush


class PushService:
    # root = folderul proiectului (aici se salvează cheia și abonamentele).
    # contact_email = adresa noastră de contact, cerută de serviciile de push.
    def __init__(self, root: Path, contact_email: str):
        self.key_path = root / "vapid_private.pem"
        self.subs_path = root / "push_subscriptions.json"
        # Serviciile de push cer o adresă de contact pentru cel care trimite.
        self.claims = {"sub": f"mailto:{contact_email or 'admin@localhost'}"}
        self.lock = threading.Lock()

        # Prima pornire: generăm o pereche de chei VAPID (curba eliptică P-256, cerută de standard)
        # și salvăm cheia privată în vapid_private.pem. Cheia NU trebuie schimbată ulterior:
        # abonamentele existente sunt legate de ea și n-ar mai funcționa.
        if not self.key_path.exists():
            key = ec.generate_private_key(ec.SECP256R1())
            self.key_path.write_bytes(key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption()))
        # Citim cheia privată și calculăm din ea cheia publică, pe care o dăm browserelor.
        key = serialization.load_pem_private_key(self.key_path.read_bytes(), None)
        # Formatul „necomprimat” (65 de octeți) este cel așteptat de browser ...
        raw = key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        # ... codificat în base64 „sigur pentru adrese web” și fără caracterele „=” de la final.
        self.public_key = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

        # subs: pentru fiecare endpoint (adresa unică a unui dispozitiv), datele lui:
        #   {"subscription": {...}, "owner": "email@...", "device": "Chrome pe Windows", "created_at": ...}
        self.subs: dict[str, dict] = {}
        if self.subs_path.exists():
            try:
                self.subs = json.loads(self.subs_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                print(f"[PUSH] nu pot citi {self.subs_path.name}: {e}")

    # Salvare sigură pe disc (fișier temporar + redenumire, ca în alerts.py).
    def _save(self):
        tmp = self.subs_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.subs, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.subs_path)

    # Salvează sau actualizează abonamentul unui dispozitiv.
    # owner = e-mailul pentru care dispozitivul primește alarme (poate fi gol = toate alarmele).
    def subscribe(self, subscription: dict, owner: str, device: str):
        endpoint = subscription.get("endpoint")
        # Un abonament valid are obligatoriu adresa și cheile de criptare.
        if not endpoint or not subscription.get("keys"):
            raise ValueError("Abonament push invalid")
        with self.lock:
            self.subs[endpoint] = {
                "subscription": subscription,
                "owner": owner.strip().lower(),
                "device": device[:120],
                # La o actualizare păstrăm data inițială a abonării; altfel punem data de acum.
                "created_at": self.subs.get(endpoint, {}).get("created_at")
                or datetime.now().isoformat(timespec="seconds"),
            }
            self._save()

    # Șterge abonamentul. Întoarce False dacă nu exista.
    def unsubscribe(self, endpoint: str) -> bool:
        with self.lock:
            # pop(cheie, None) scoate elementul și îl întoarce, sau None dacă lipsea.
            if self.subs.pop(endpoint, None) is None:
                return False
            self._save()
            return True

    # Este dispozitivul cu acest endpoint abonat?
    def has(self, endpoint: str) -> bool:
        with self.lock:
            return endpoint in self.subs

    # Trimite o notificare unui singur dispozitiv. Întoarce True dacă serviciul de push a acceptat-o.
    # payload = conținutul notificării: {"title", "body", "tag", "url"} (citit apoi de sw.js).
    def send(self, endpoint: str, payload: dict) -> bool:
        with self.lock:
            entry = self.subs.get(endpoint)
        if not entry:
            return False
        try:
            # webpush() criptează payload-ul cu cheile browserului, îl semnează cu cheia noastră
            # VAPID și îl trimite prin HTTPS la adresa serviciului de push.
            webpush(
                subscription_info=entry["subscription"],
                data=json.dumps(payload, ensure_ascii=False),
                vapid_private_key=str(self.key_path),
                vapid_claims=dict(self.claims),
                ttl=3600,  # o alarmă mai veche de o oră nu mai e utilă
                timeout=15,
            )
            return True
        except WebPushException as e:
            # Codul de răspuns al serviciului de push (dacă a răspuns).
            status = e.response.status_code if e.response is not None else None
            if status in (404, 410):  # abonament expirat sau dezactivat din browser
                print(f"[PUSH] abonament expirat, îl șterg ({entry['device']})")
                self.unsubscribe(endpoint)
            else:
                print(f"[PUSH] trimitere eșuată ({status}): {e}")
            return False
        except Exception as e:  # noqa: BLE001
            # Orice altă problemă (ex. fără internet) – o afișăm, dar nu oprim serverul.
            print(f"[PUSH] trimitere eșuată: {e}")
            return False

    # Alege dispozitivele care trebuie să primească o alarmă cu e-mailul dat.
    # Regula: un dispozitiv primește alarma dacă (1) alarma nu are e-mail, sau (2) dispozitivul
    # nu are e-mail setat, sau (3) e-mailurile coincid. Așa, doi utilizatori ai aplicației își
    # primesc fiecare doar alarmele proprii.
    def targets(self, email: str) -> list[str]:
        """Dispozitivele care primesc o alarmă: cele ale aceluiași e-mail.
        O alarmă fără e-mail sau un dispozitiv fără e-mail primesc / trimit către toți."""
        email = (email or "").strip().lower()
        with self.lock:
            return [ep for ep, s in self.subs.items()
                    if not email or not s["owner"] or s["owner"] == email]

    # Apelată de AlertStore când o alarmă se declanșează: compune notificarea și o trimite.
    def notify_alert(self, alert: dict):
        d = alert.get("digits", 5)
        arrow = "↑" if alert["direction"] == "above" else "↓"
        payload = {
            "title": f"{alert['symbol']} {arrow} {alert['price']:.{d}f}",
            "body": f"Atins: {alert['trigger_price']:.{d}f} ({alert['side']})"
                    + (f" — {alert['note']}" if alert.get("note") else ""),
            # tag: notificările cu același tag se înlocuiesc (nu apare aceeași alarmă de două ori).
            "tag": f"alert-{alert['id']}",
            # La click pe notificare se deschide aplicația direct pe simbolul alarmei.
            "url": f"/?symbol={alert['symbol']}",
        }
        # Trimiterea (care așteaptă răspunsuri de la servere externe) se face într-un fir separat,
        # ca serverul, care ne-a apelat, să nu fie întârziat.
        threading.Thread(target=self._send_many, args=(self.targets(alert.get("email", "")), payload),
                         daemon=True).start()

    # Trimite aceeași notificare către mai multe dispozitive și afișează câte au primit-o.
    def _send_many(self, endpoints: list[str], payload: dict):
        # send() întoarce True/False; sum() le numără pe cele True.
        sent = sum(self.send(ep, payload) for ep in endpoints)
        print(f"[PUSH] {payload['title']} -> {sent}/{len(endpoints)} dispozitive")
