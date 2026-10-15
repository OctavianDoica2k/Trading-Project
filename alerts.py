"""
Alarme de preț: salvate în alerts.json, verificate pe fiecare tic, notificare pe e-mail (SMTP).

Setările SMTP se citesc din fișierul .env (vezi .env.example).
"""
# =====================================================================================
#  CE FACE ACEST FIȘIER (pe scurt)
# -------------------------------------------------------------------------------------
#  - Mailer     : trimite e-mailuri printr-un server SMTP (de ex. Gmail), cu datele din .env.
#  - AlertStore : ține lista alarmelor de preț, o salvează în alerts.json, verifică la fiecare
#                 tic dacă vreo alarmă a fost atinsă și, dacă da, anunță (pagină, push, e-mail).
#  - valid_email: verificare simplă a formatului unei adrese de e-mail.
#
#  O alarmă e un dicționar Python, de exemplu:
#    {"id": "a1b2c3", "symbol": "EURUSD", "price": 1.12050, "side": "bid",
#     "direction": "above", "status": "active", "email": "...", ...}
#  „direction” spune dacă așteptăm ca prețul să URCE până la țintă ("above") sau să COBOARE
#  până la ea ("below"); se stabilește automat la creare, comparând ținta cu prețul curent.
# =====================================================================================

# html      : escape() – transformă caractere speciale (<, >, &) ca să fie afișate sigur în HTML.
# json      : citirea/scrierea fișierului alerts.json.
# os        : citirea setărilor SMTP din variabilele de mediu (încărcate din .env).
# re        : expresia regulată pentru validarea e-mailului.
# smtplib   : clientul SMTP din Python (protocolul de trimitere e-mail).
# ssl       : criptarea conexiunii cu serverul de e-mail.
# threading : lacăt pentru acces sigur la listă + fire separate pentru trimiterea e-mailurilor.
# time      : pauză între reîncercări la trimitere.
# uuid      : generarea unui identificator unic pentru fiecare alarmă.
import html
import json
import os
import re
import smtplib
import ssl
import threading
import time
import uuid
from datetime import datetime
# EmailMessage: construiește un e-mail (antete + conținut) în formatul standard.
from email.message import EmailMessage
# formataddr: „Nume <adresa>”; formatdate: data în formatul cerut de e-mail;
# make_msgid: identificator unic al mesajului (Message-ID).
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path

# Tipar simplu pentru e-mail: „ceva@ceva.ceva”, fără spații. Nu prinde toate cazurile
# teoretice, dar e suficient pentru a respinge greșelile evidente de tastare.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# Transformă un moment în milisecunde (cum vine de la cTrader) în text „2026-10-14 10:45:00.123”.
def fmt_tick_time(msc: int) -> str:
    # Timpul cTrader e UTC real; fromtimestamp() fără fus orar îl afișează în ora locală a PC-ului.
    # ([:-3] taie ultimele 3 cifre din microsecunde, rămânând milisecundele.)
    return datetime.fromtimestamp(msc / 1000).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


# =====================================================================================
#  Mailer – trimiterea e-mailurilor
# =====================================================================================
class Mailer:
    # Citește setările din variabilele de mediu (fișierul .env). Valorile implicite sunt
    # cele de după virgulă (ex. portul 587 dacă SMTP_PORT lipsește).
    def __init__(self):
        self.host = os.getenv("SMTP_HOST", "").strip()
        self.port = int(os.getenv("SMTP_PORT", "587") or 587)
        self.user = os.getenv("SMTP_USER", "").strip()
        self.password = os.getenv("SMTP_PASSWORD", "")
        # Adresa expeditorului; dacă nu e dată separat, e chiar contul cu care ne logăm.
        self.sender = os.getenv("SMTP_FROM", "").strip() or self.user
        self.security = os.getenv("SMTP_SECURITY", "starttls").strip().lower()  # starttls | ssl | none
        # Numele afișat la „De la” (ex. „Nexus Alerts <adresa@gmail.com>”).
        self.sender_name = os.getenv("SMTP_FROM_NAME", "Nexus Alerts").strip()

    # @property = se folosește ca un atribut (mailer.configured), dar e calculat la fiecare citire.
    # E-mailul e considerat configurat dacă avem cel puțin serverul și adresa expeditorului.
    @property
    def configured(self) -> bool:
        return bool(self.host and self.sender)

    # Trimite un e-mail: `to` = destinatarul, `subject` = subiectul, `body` = textul simplu.
    # Aruncă o excepție dacă trimiterea eșuează (cine o apelează decide ce face cu eroarea).
    def send(self, to: str, subject: str, body: str):
        if not self.configured:
            raise RuntimeError("SMTP neconfigurat (completează .env)")
        # Antetele complete (nume expeditor, Date, Message-ID) și varianta HTML alături de text
        # reduc șansa ca mesajul să fie tratat drept spam.
        msg = EmailMessage()
        msg["From"] = formataddr((self.sender_name, self.sender))
        msg["To"] = to
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        # Message-ID se generează cu domeniul expeditorului (ex. ...@gmail.com).
        msg["Message-ID"] = make_msgid(domain=self.sender.rsplit("@", 1)[-1])
        msg["Reply-To"] = self.sender
        # Două variante ale aceluiași conținut: text simplu + HTML. Programul de e-mail o alege
        # pe cea mai bună pe care o poate afișa.
        msg.set_content(body)
        msg.add_alternative(self._html(subject, body), subtype="html")
        # Context SSL standard: verifică certificatul serverului (că vorbim chiar cu Gmail).
        ctx = ssl.create_default_context()
        # Două moduri de criptare:
        #  - "ssl"     : conexiunea e criptată de la început (de obicei portul 465);
        #  - "starttls": conexiunea pornește necriptată și trece apoi pe criptare (portul 587).
        if self.security == "ssl":
            server = smtplib.SMTP_SSL(self.host, self.port, context=ctx, timeout=20)
        else:
            server = smtplib.SMTP(self.host, self.port, timeout=20)
        # `with` închide automat conexiunea la final, chiar dacă apare o eroare.
        with server:
            if self.security == "starttls":
                server.starttls(context=ctx)
            if self.user:
                self._login(server)
            server.send_message(msg)

    # Construiește varianta HTML a e-mailului din textul simplu.
    # Liniile de forma „Cheie: valoare” devin rânduri de tabel; restul devin paragrafe.
    # @staticmethod = funcție care nu folosește datele obiectului (self).
    @staticmethod
    def _html(subject: str, body: str) -> str:
        rows = []
        for line in body.strip().splitlines():
            # E o pereche „cheie: valoare” dacă are „:”, partea dinainte nu e goală și linia
            # nu începe cu spațiu.
            if ":" in line and line.split(":", 1)[0].strip() and line[0] != " ":
                # split(":", 1) taie doar la PRIMUL „:” (ora „10:45:00” rămâne întreagă în valoare).
                k, v = line.split(":", 1)
                rows.append(f'<tr><td style="padding:4px 16px 4px 0;color:#666">{html.escape(k.strip())}</td>'
                            f'<td style="padding:4px 0;font-weight:600">{html.escape(v.strip())}</td></tr>')
            elif line.strip():
                rows.append(f'<tr><td colspan="2" style="padding:0 0 10px">{html.escape(line.strip())}</td></tr>')
        # Stilurile sunt scrise direct pe elemente („inline”), pentru că multe programe de
        # e-mail ignoră foile de stil separate.
        return (
            '<!doctype html><html><body style="margin:0;padding:24px;background:#f5f6f8;'
            'font-family:Segoe UI,Arial,sans-serif;color:#222">'
            '<div style="max-width:520px;margin:auto;background:#fff;border-radius:8px;padding:20px 24px">'
            f'<h2 style="margin:0 0 14px;font-size:18px">{html.escape(subject)}</h2>'
            f'<table style="border-collapse:collapse;font-size:14px">{"".join(rows)}</table>'
            '<p style="margin:18px 0 0;font-size:12px;color:#888">Trimis automat de aplicația Nexus Live Chart '
            'pentru o alarmă de preț setată de tine.</p>'
            '</div></body></html>'
        )

    # Autentificarea la serverul de e-mail.
    def _login(self, server: smtplib.SMTP):
        # login() încearcă pe rând mai multe metode, iar Gmail închide conexiunea după primul
        # refuz, ascunzând eroarea reală. Cu o singură metodă primim răspunsul serverului.
        server.ehlo_or_helo_if_needed()
        # Metodele de autentificare anunțate de server (ex. "LOGIN PLAIN XOAUTH2").
        methods = server.esmtp_features.get("auth", "").upper().split()
        # auth_plain citește utilizatorul și parola din aceste atribute ale conexiunii.
        server.user, server.password = self.user, self.password
        try:
            if "PLAIN" in methods:
                server.auth("PLAIN", server.auth_plain)
            else:
                server.login(self.user, self.password)
        except smtplib.SMTPAuthenticationError as e:
            # Transformăm eroarea tehnică într-un mesaj clar, în română, afișat în pagină.
            raise RuntimeError(
                f"Serverul de e-mail a refuzat utilizatorul/parola ({e.smtp_code}). "
                "Pentru Gmail: SMTP_PASSWORD trebuie să fie o Parolă pentru aplicații creată "
                "în contul din SMTP_USER, cu verificarea în 2 pași activă."
            ) from e


# =====================================================================================
#  AlertStore – lista alarmelor
# -------------------------------------------------------------------------------------
#  E folosită din mai multe fire de execuție în același timp:
#   - firul web (când creezi / ștergi o alarmă din pagină);
#   - bucla asyncio a serverului (când verifică ticurile noi, în check());
#   - firul care trimite e-mailul (când salvează rezultatul trimiterii).
#  De aceea orice acces la self.alerts se face sub `with self.lock:`.
# =====================================================================================
class AlertStore:
    # path = fișierul în care se salvează alarmele; mailer = obiectul care trimite e-mailurile.
    def __init__(self, path: Path, mailer: Mailer):
        self.path = path
        self.mailer = mailer
        self.lock = threading.Lock()
        self.alerts: list[dict] = []
        # „Callback”-uri: funcții setate din exterior (în server.py) și apelate de noi la evenimente.
        self.on_change = None  # callback(alert) apelat când o alarmă se declanșează / se actualizează
        self.on_fired = None   # callback(alert) apelat o singură dată, la declanșare (ex: push)
        # Încărcăm alarmele salvate anterior, ca să supraviețuiască repornirii serverului.
        if path.exists():
            try:
                self.alerts = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                print(f"[ALERT] nu pot citi {path.name}: {e}")

    # Salvează lista pe disc. Scriem întâi într-un fișier temporar și apoi îl redenumim peste
    # cel vechi: așa, dacă programul se oprește brusc în timpul scrierii, alerts.json nu rămâne
    # pe jumătate scris (redenumirea e o operație „totul sau nimic”).
    # Trebuie apelată doar cu self.lock deja ocupat.
    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.alerts, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    # Toate alarmele, cele mai noi primele. Întoarcem COPII (dict(a)), ca cine le primește
    # să nu poată modifica din greșeală lista internă fără lacăt.
    def all(self) -> list[dict]:
        with self.lock:
            return [dict(a) for a in reversed(self.alerts)]

    # Simbolurile care au cel puțin o alarmă activă (prețurile lor sunt urmărite permanent).
    def active_symbols(self) -> set[str]:
        with self.lock:
            return {a["symbol"] for a in self.alerts if a["status"] == "active"}

    # Creează o alarmă nouă.
    # current = prețul curent (bid sau ask, după `side`); din el deducem direcția.
    # digits  = numărul de zecimale al simbolului, folosit la rotunjire și afișare.
    def add(self, symbol: str, price: float, side: str, current: float,
            email: str, note: str, digits: int) -> dict:
        alert = {
            # 12 caractere hexazecimale aleatoare – practic imposibil să se repete.
            "id": uuid.uuid4().hex[:12],
            "symbol": symbol,
            "price": round(price, digits),
            "digits": digits,
            "side": side,                                   # bid | ask
            # Ținta peste prețul curent => așteptăm o urcare; altfel o coborâre.
            "direction": "above" if price > current else "below",
            "email": email,
            "note": note,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "status": "active",
            # Câmpurile de mai jos se completează la declanșare.
            "triggered_at": None,
            "trigger_price": None,
            "trigger_tick": None,
            "email_status": None,
        }
        with self.lock:
            self.alerts.append(alert)
            self._save()
        return dict(alert)

    # Șterge alarma cu id-ul dat. Întoarce False dacă nu exista.
    def delete(self, alert_id: str) -> bool:
        with self.lock:
            n = len(self.alerts)
            # Păstrăm toate alarmele, mai puțin cea cu id-ul cerut.
            self.alerts = [a for a in self.alerts if a["id"] != alert_id]
            if len(self.alerts) == n:
                return False
            self._save()
            return True

    # Apelată de server cu fiecare lot de ticuri noi ale unui simbol.
    # Verifică FIECARE tic (nu doar ultimul), ca să nu ratăm o atingere scurtă a prețului
    # care a apărut și a dispărut între două interogări.
    def check(self, symbol: str, ticks: list[list]):
        """ticks = [[time_msc, bid, ask], ...] în ordine cronologică."""
        # Alarmele declanșate acum; le anunțăm DUPĂ ce eliberăm lacătul (anunțurile pot dura).
        fired = []
        with self.lock:
            for a in self.alerts:
                if a["status"] != "active" or a["symbol"] != symbol:
                    continue
                # Poziția prețului în tic: 1 = bid, 2 = ask.
                idx = 1 if a["side"] == "bid" else 2
                for t in ticks:
                    p = t[idx]
                    # Unele ticuri nu au preț pe una dintre laturi (valoare 0) – le sărim.
                    if p <= 0:
                        continue
                    # Condiția de declanșare: am ajuns la țintă sau am trecut de ea,
                    # în direcția așteptată.
                    if (a["direction"] == "above" and p >= a["price"]) or \
                       (a["direction"] == "below" and p <= a["price"]):
                        a["status"] = "triggered"
                        a["triggered_at"] = datetime.now().isoformat(timespec="seconds")
                        # Rotunjim ca să evităm valori ca 1.1191200000000001 (erori de virgulă mobilă).
                        a["trigger_price"] = round(p, a.get("digits", 8))
                        a["trigger_tick"] = t[0]
                        a["email_status"] = "se trimite..." if a["email"] else "fără e-mail"
                        fired.append(dict(a))
                        # O alarmă se declanșează o singură dată: trecem la următoarea.
                        break
            if fired:
                self._save()
        # Anunțăm fiecare alarmă declanșată pe toate căile.
        for a in fired:
            print(f"[ALERT] {a['symbol']} {a['side']} {a['trigger_price']} (țintă {a['price']})")
            # 1) Paginile deschise (prin WebSocket).
            self._notify(a)
            # 2) Notificarea push pe dispozitivele abonate.
            if self.on_fired:
                try:
                    self.on_fired(a)
                except Exception as e:  # noqa: BLE001
                    print(f"[ALERT] notificare push eșuată: {e}")
            # 3) E-mailul, într-un fir separat: trimiterea durează câteva secunde și nu vrem să
            #    blocăm serverul (care trebuie să primească în continuare ticurile).
            if a["email"]:
                threading.Thread(target=self._email, args=(a,), daemon=True).start()

    # Anunță o schimbare a unei alarme prin callback-ul setat de server (dacă există).
    def _notify(self, alert: dict):
        if self.on_change:
            self.on_change(alert)

    # Compune și trimite e-mailul pentru o alarmă declanșată (rulează în fir separat).
    def _email(self, alert: dict):
        # Formatăm prețurile cu numărul fix de zecimale al simbolului (ex. 1.12050, nu 1.1205).
        d = alert.get("digits", 5)
        price, hit = f"{alert['price']:.{d}f}", f"{alert['trigger_price']:.{d}f}"
        word = "a urcat la" if alert["direction"] == "above" else "a coborât la"
        subject = f"Alarmă Nexus: {alert['symbol']} {word} {price}"
        # Textul e aliniat pe coloane; în varianta HTML devine un tabel (vezi Mailer._html).
        body = (
            f"Alarma de preț s-a declanșat.\n\n"
            f"Simbol:        {alert['symbol']}\n"
            f"Țintă:         {price} ({alert['side']}, "
            f"{'peste' if alert['direction'] == 'above' else 'sub'})\n"
            f"Preț atins:    {hit}\n"
            f"Ora tic:       {fmt_tick_time(alert['trigger_tick'])}\n"
            f"Ora locală:    {alert['triggered_at'].replace('T', ' ')}\n"
        )
        if alert["note"]:
            body += f"Notă:          {alert['note']}\n"
        status = "trimis"
        # Până la 3 încercări, la 5 secunde distanță (ex. o întrerupere scurtă de internet).
        for attempt in range(3):
            try:
                self.mailer.send(alert["email"], subject, body)
                status = "trimis"
                break
            except Exception as e:  # noqa: BLE001
                status = f"eroare: {e}"
                print(f"[ALERT] e-mail eșuat (încercarea {attempt + 1}): {e}")
                # Fără SMTP configurat, reîncercarea n-are rost.
                if not self.mailer.configured:
                    break
                time.sleep(5)
        # Salvăm rezultatul trimiterii în alarmă, ca pagina să afișeze „trimis” sau eroarea.
        with self.lock:
            for a in self.alerts:
                if a["id"] == alert["id"]:
                    a["email_status"] = status
                    alert = dict(a)
            self._save()
        self._notify(alert)


# True dacă textul arată ca o adresă de e-mail validă.
def valid_email(s: str) -> bool:
    return bool(EMAIL_RE.match(s))
