"""
Server de prețuri live din MetaTrader 5 (FBS) -> browser, prin WebSocket.

Terminalul MT5 trebuie să fie pornit și logat. Pornire:
    python server.py            -> http://127.0.0.1:8000  (și de pe telefon, din aceeași rețea)
"""
# =====================================================================================
#  CE FACE ACEST FIȘIER (pe scurt)
# -------------------------------------------------------------------------------------
#  1. Se conectează la terminalul MetaTrader 5 deschis pe PC (contul FBS).
#  2. Un fir de execuție separat („thread”) întreabă terminalul la fiecare 10 ms dacă au
#     apărut ticuri noi (un „tic” = o nouă cotație bid/ask trimisă de broker).
#  3. Ticurile noi sunt trimise instant către toate paginile deschise în browser, printr-o
#     conexiune WebSocket (o conexiune permanentă, în ambele sensuri, între pagină și server).
#  4. Tot aici sunt definite adresele API (de ex. /api/alerts) folosite de pagină pentru
#     alarme de preț, e-mail de test și notificări push.
#
#  Biblioteci folosite:
#   - MetaTrader5 : pachetul oficial Python care „vorbește” cu terminalul MT5.
#   - numpy       : calcule rapide pe tablouri mari de numere (construirea lumânărilor).
#   - FastAPI     : framework web – definește adresele (rutele) serverului.
#   - uvicorn     : serverul web propriu-zis, care rulează aplicația FastAPI.
#   - dotenv      : citește setările din fișierul .env (parole SMTP etc.).
#   - pydantic    : validează automat datele JSON primite de la pagină.
# =====================================================================================

# --- Biblioteci standard Python ---
# asyncio   : programare asincronă (mii de conexiuni deservite fără blocare).
# json      : transformă dicționare Python în text JSON și invers.
# os        : citirea variabilelor de mediu (setările din .env).
# re        : expresii regulate (căutare/înlocuire de text după tipare).
# socket    : folosit doar ca să aflăm IP-ul PC-ului în rețeaua locală.
# sys       : acces la consola (stdout/stderr).
# threading : fire de execuție paralele și „lacăte” (Lock) pentru acces sigur la date comune.
# time      : pauze (sleep) între interogări.
import asyncio
import json
import os
import re
import socket
import sys
import threading
import time
# asynccontextmanager: permite scrierea codului de pornire/oprire a serverului într-o singură funcție.
from contextlib import asynccontextmanager
# Path: lucrul comod cu căi de fișiere (ROOT / "static" etc.).
from pathlib import Path

# --- Biblioteci externe (instalate cu pip, vezi requirements.txt) ---
import MetaTrader5 as mt5
import numpy as np
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Consola Windows poate fi cp1252: fără asta, diacriticele din mesaje ar opri serverul.
# (cp1252 nu conține litere precum „ă”, „ț”; un print() cu ele ar arunca o eroare.
#  reconfigure() trece consola pe UTF-8, iar errors="replace" înlocuiește orice caracter
#  imposibil de afișat cu „?” în loc să oprească programul.)
for _stream in (sys.stdout, sys.stderr):
    _stream.reconfigure(encoding="utf-8", errors="replace")

# ROOT = folderul în care se află acest fișier (folderul proiectului).
ROOT = Path(__file__).parent
# Încărcăm setările din .env în variabilele de mediu ÎNAINTE de a importa modulele de mai jos,
# pentru că acestea citesc setările (SMTP_HOST etc.) chiar în momentul în care sunt create.
load_dotenv(ROOT / ".env")

# Modulele proiectului: alarmele + e-mailul (alerts.py) și notificările push (push.py).
from alerts import AlertStore, Mailer, valid_email  # noqa: E402 - după load_dotenv
from push import PushService  # noqa: E402

# --- Constante de configurare ---
POLL_INTERVAL = 0.010   # cât de des interogăm terminalul pentru ticuri noi (secunde)
HISTORY_BARS = 600      # câte lumânări trimitem la conectare

# Timeframe-uri suportate (secunde). Sub 60s lumânările se construiesc din ticuri.
# Cheia = durata unei lumânări în secunde (cum o trimite pagina).
# Valoarea = constanta MT5 corespunzătoare; None înseamnă că MT5 nu are acel timeframe
# (1s, 5s, 15s, 30s) și construim noi lumânările din ticurile brute.
TIMEFRAMES = {
    1: None, 5: None, 15: None, 30: None,
    60: mt5.TIMEFRAME_M1, 300: mt5.TIMEFRAME_M5, 900: mt5.TIMEFRAME_M15,
    3600: mt5.TIMEFRAME_H1, 14400: mt5.TIMEFRAME_H4, 86400: mt5.TIMEFRAME_D1,
}

# Folderul cu fișierele paginii web (HTML, CSS, JavaScript).
STATIC = ROOT / "static"
# Adresa și portul pe care ascultă serverul (pot fi schimbate din .env).
HOST = os.getenv("HOST", "0.0.0.0")   # 0.0.0.0 = accesibil și de pe telefon din rețeaua locală
PORT = int(os.getenv("PORT", "8000"))

# Pachetul MetaTrader5 nu e garantat thread-safe: toate apelurile trec prin acest lock.
# Un „lock” (lacăt) asigură că un singur fir de execuție vorbește cu MT5 la un moment dat:
# cine vrea să apeleze mt5.* scrie `with mt5_lock:` și așteaptă dacă lacătul e ocupat.
mt5_lock = threading.Lock()


# Pornește (sau repornește) legătura cu terminalul MT5.
# mt5.initialize() se atașează la terminalul deja deschis și logat; nu are nevoie de parolă.
# Întoarce True dacă a reușit, False altfel (și afișează motivul).
def mt5_connect() -> bool:
    with mt5_lock:
        if mt5.initialize():
            return True
        print(f"[MT5] initialize() a eșuat: {mt5.last_error()}")
        return False


# =====================================================================================
#  TickHub – „centrala” de ticuri
# -------------------------------------------------------------------------------------
#  Problema: pot fi deschise mai multe pagini (PC, telefon, alt utilizator), fiecare pe alt
#  simbol. Nu vrem ca fiecare pagină să întrebe singură MT5-ul.
#  Soluția: un SINGUR fir de execuție (metoda run) citește ticurile pentru toate simbolurile
#  necesare și le pune în „cozile” (Queue) paginilor abonate la acel simbol.
#  Fiecare pagină conectată are propria coadă; funcția ws_endpoint (mai jos) scoate mesajele
#  din coadă și le trimite prin WebSocket.
# =====================================================================================
class TickHub:
    """Un singur thread citește ticurile din MT5 și le distribuie clienților abonați."""

    def __init__(self):
        # Bucla asyncio a serverului web; e setată la pornire (în lifespan). Avem nevoie de ea
        # ca să putem pune mesaje în cozi din firul MT5 în mod sigur (call_soon_threadsafe).
        self.loop: asyncio.AbstractEventLoop | None = None
        # subs: pentru fiecare simbol, mulțimea cozilor paginilor care îl urmăresc.
        # Exemplu: {"EURUSD": {coada_pagina1, coada_pagina2}, "XAUUSD": {coada_pagina3}}
        self.subs: dict[str, set[asyncio.Queue]] = {}
        self.all_queues: set[asyncio.Queue] = set()   # pentru mesaje către toți clienții
        self.watched = lambda: set()                  # simboluri urmărite și fără clienți (alarme)
        self.on_ticks = None                          # callback(symbol, ticks) în thread-ul MT5
        # last_msc: pentru fiecare simbol, momentul (în milisecunde) ultimului tic deja trimis.
        # Așa știm, la următoarea interogare, care ticuri sunt noi.
        self.last_msc: dict[str, int] = {}
        # Lacăt pentru subs/all_queues (sunt modificate din firul web și citite din firul MT5).
        self.subs_lock = threading.Lock()
        # Semnal de oprire: când e „setat”, bucla din run() se termină.
        self.stop = threading.Event()

    # O pagină nouă vrea ticurile unui simbol: îi creăm o coadă și o înregistrăm.
    # maxsize=5000: limită ca o pagină blocată să nu umple memoria la nesfârșit.
    def subscribe(self, symbol: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=5000)
        with self.subs_lock:
            # setdefault creează mulțimea goală pentru simbol dacă nu există încă.
            self.subs.setdefault(symbol, set()).add(q)
            self.all_queues.add(q)
        return q

    # Pagina s-a închis: îi ștergem coada. Dacă nimeni nu mai urmărește simbolul,
    # îl scoatem cu totul, ca firul MT5 să nu-l mai interogheze degeaba.
    def unsubscribe(self, symbol: str, q: asyncio.Queue):
        with self.subs_lock:
            self.all_queues.discard(q)
            qs = self.subs.get(symbol)
            if qs:
                qs.discard(q)
                if not qs:
                    del self.subs[symbol]

    # Pune un mesaj în cozile paginilor. symbol=None înseamnă „către toate paginile”
    # (folosit pentru evenimente de alarmă, care interesează pe toată lumea).
    # Rulează în firul web (e apelată prin call_soon_threadsafe), deci poate folosi cozile asyncio.
    def _push(self, symbol: str | None, msg: str):
        with self.subs_lock:
            # Facem o copie a listei ca să nu ținem lacătul ocupat cât punem mesajele.
            queues = list(self.all_queues if symbol is None else self.subs.get(symbol, ()))
        for q in queues:
            if q.full():          # client lent: aruncăm mesajul cel mai vechi
                q.get_nowait()
            q.put_nowait(msg)

    # Verifică dacă au apărut ticuri noi pentru UN simbol și le distribuie.
    # Rulează în firul MT5.
    def _poll_symbol(self, symbol: str):
        with mt5_lock:
            last = self.last_msc.get(symbol)
            # Prima dată când vedem simbolul: doar reținem momentul ultimului tic existent
            # (nu retrimitem istoricul – acesta e trimis separat, la conectare, în build_hello).
            if last is None:
                tick = mt5.symbol_info_tick(symbol)
                if tick is not None:
                    self.last_msc[symbol] = tick.time_msc
                return
            # Cerem toate ticurile din secunda ultimului tic încoace și păstrăm doar cele noi,
            # ca să nu pierdem ticuri când vin mai multe între două interogări.
            # (copy_ticks_from primește timpul în secunde, de aceea last // 1000; 2000 = maximul
            #  de ticuri cerute odată; COPY_TICKS_ALL = toate tipurile de ticuri.)
            ticks = mt5.copy_ticks_from(symbol, last // 1000, 2000, mt5.COPY_TICKS_ALL)
        if ticks is None or len(ticks) == 0:
            return
        # `ticks` e un tablou numpy; filtrăm doar ticurile cu timpul > ultimul trimis.
        new = ticks[ticks["time_msc"] > last]
        if len(new) == 0:
            return
        # Reținem momentul celui mai nou tic, pentru interogarea următoare.
        self.last_msc[symbol] = int(new["time_msc"][-1])
        # Transformăm ticurile într-o listă compactă: [timp_ms, bid, ask] pentru fiecare.
        data = [[int(t["time_msc"]), float(t["bid"]), float(t["ask"])] for t in new]
        # Le dăm întâi modulului de alarme (care verifică dacă vreun preț țintă a fost atins).
        if self.on_ticks:
            self.on_ticks(symbol, data)
        # Apoi le trimitem paginilor. call_soon_threadsafe = „execută _push în firul web,
        # cât de curând” – singurul mod sigur de a atinge cozile asyncio din alt fir.
        msg = json.dumps({"type": "ticks", "d": data})
        self.loop.call_soon_threadsafe(self._push, symbol, msg)

    # Trimite un mesaj (dicționar) către TOATE paginile deschise. Folosit pentru alarme.
    def broadcast(self, msg: dict):
        """Apelabil din orice thread."""
        self.loop.call_soon_threadsafe(self._push, None, json.dumps(msg))

    # Bucla principală a firului MT5: rulează până la oprirea serverului.
    def run(self):
        while not self.stop.is_set():
            # Simbolurile de interogat = cele urmărite de pagini ...
            with self.subs_lock:
                symbols = set(self.subs)
            # ... plus cele cu alarme active (trebuie urmărite chiar dacă nicio pagină nu e deschisă).
            symbols |= self.watched()
            # Uităm simbolurile care nu mai interesează pe nimeni (ca la revenire să pornim „curat”).
            for sym in list(self.last_msc):
                if sym not in symbols:
                    del self.last_msc[sym]
            for sym in symbols:
                try:
                    self._poll_symbol(sym)
                except Exception as e:  # noqa: BLE001 - thread-ul nu trebuie să moară
                    print(f"[MT5] eroare la {sym}: {e}")
            # Verificăm periodic că terminalul e încă conectat; dacă nu, încercăm reconectarea.
            if symbols:
                with mt5_lock:
                    ti = mt5.terminal_info()
                if ti is None:
                    print("[MT5] conexiune pierdută, reîncerc...")
                    time.sleep(1)
                    mt5_connect()
            # Pauză scurtă (10 ms) între runde, ca să nu încărcăm procesorul inutil.
            time.sleep(POLL_INTERVAL)


# --- Crearea obiectelor principale și legarea lor între ele ---
# hub     : distribuie ticurile.
# alerts  : alarmele de preț (fișierul alerts.json poate fi schimbat cu variabila ALERTS_FILE).
hub = TickHub()
alerts = AlertStore(Path(os.getenv("ALERTS_FILE", ROOT / "alerts.json")), Mailer())
# hub-ul întreabă alarmele ce simboluri trebuie urmărite permanent ...
hub.watched = alerts.active_symbols
# ... și le trimite fiecare lot de ticuri noi, ca să verifice prețurile țintă.
hub.on_ticks = alerts.check
# Când o alarmă se schimbă (declanșare, status e-mail), anunțăm toate paginile deschise.
alerts.on_change = lambda a: hub.broadcast({"type": "alert", "alert": a})
# Serviciul de notificări push; adresa expeditorului de e-mail e folosită ca adresă de contact.
push = PushService(ROOT, alerts.mailer.sender)
# La declanșarea unei alarme se trimite și notificarea push către dispozitivele abonate.
alerts.on_fired = push.notify_alert


# Află IP-ul PC-ului în rețeaua locală (ex. 192.168.0.60), ca să-l afișăm la pornire.
# Trucul: „conectăm” un socket UDP spre o adresă oarecare; sistemul alege interfața de rețea
# potrivită, iar getsockname() ne spune IP-ul ei. Nu se trimite efectiv niciun pachet.
def lan_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))   # nu trimite nimic, doar alege interfața
            return s.getsockname()[0]
    except OSError:
        return None


# „lifespan” = ce se întâmplă la pornirea și la oprirea serverului.
# Tot ce e înainte de `yield` rulează la pornire; tot ce e după, la oprire.
@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Fără MT5 aplicația nu are sens, așa că oprim pornirea cu un mesaj clar.
    if not mt5_connect():
        raise RuntimeError("Nu mă pot conecta la terminalul MT5. E pornit și logat?")
    with mt5_lock:
        acc = mt5.account_info()
    # Mesaje informative în consolă: contul conectat și adresele la care se deschide pagina.
    print(f"[MT5] conectat: {acc.server} / {acc.login}" if acc else "[MT5] conectat")
    print(f"[WEB] pe acest PC:  http://127.0.0.1:{PORT}")
    # (`:=` atribuie și testează în același timp: ip primește rezultatul lui lan_ip().)
    if HOST == "0.0.0.0" and (ip := lan_ip()):
        print(f"[WEB] de pe telefon: http://{ip}:{PORT}  (aceeași rețea Wi-Fi)")
    if not alerts.mailer.configured:
        print("[ALERT] SMTP neconfigurat: alarmele apar în pagină, dar nu se trimit e-mailuri (vezi .env.example)")
    # Reținem bucla asyncio și pornim firul MT5 (daemon=True: se oprește odată cu programul).
    hub.loop = asyncio.get_running_loop()
    threading.Thread(target=hub.run, daemon=True).start()
    yield
    # La oprire: semnalăm firului MT5 să se oprească și închidem legătura cu terminalul.
    hub.stop.set()
    with mt5_lock:
        mt5.shutdown()


# Aplicația web propriu-zisă.
app = FastAPI(lifespan=lifespan)
# Fișierele din folderul static/ sunt servite la adresa /static/... (ex. /static/app.js).
app.mount("/static", StaticFiles(directory=STATIC), name="static")


# „Middleware” = cod care rulează pentru FIECARE cerere, înainte/după ruta propriu-zisă.
@app.middleware("http")
async def no_stale_static(request, call_next):
    # Browserul revalidează mereu pagina și fișierele statice, ca modificările să apară imediat.
    # (Antetul Cache-Control: no-cache îi spune browserului „poți păstra fișierul, dar întreabă
    #  serverul dacă s-a schimbat înainte să-l folosești”.)
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


# Folosită de re.sub în index(): primește o potrivire de forma "/static/app.js" și o întoarce
# cu un număr de versiune adăugat.
def versioned(match: re.Match) -> str:
    # /static/app.js -> /static/app.js?v=<ora modificării>: la fiecare schimbare adresa e nouă,
    # deci browserele (inclusiv cele cu fișiere vechi în cache) descarcă versiunea curentă.
    name = match.group(1)
    path = STATIC / name
    # st_mtime = momentul ultimei modificări a fișierului (secunde); îl folosim ca versiune.
    v = int(path.stat().st_mtime) if path.exists() else 0
    return f'"/static/{name}?v={v}"'


# Service worker-ul (scriptul care primește notificările push) trebuie servit de la
# rădăcina site-ului (/sw.js), nu din /static/, altfel ar „vedea” doar paginile din /static/.
@app.get("/sw.js")
async def service_worker():
    # Servit de la rădăcină, ca service worker-ul să acopere tot site-ul.
    return FileResponse(STATIC / "sw.js", media_type="text/javascript",
                        headers={"Cache-Control": "no-cache"})


# Pagina principală. Citim index.html și adăugăm versiunea la fiecare fișier .js/.css
# (vezi versioned) înainte de a-l trimite browserului.
@app.get("/")
async def index():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    # Tiparul caută texte ca "/static/app.js" sau "/static/style.css" (între ghilimele).
    html = re.sub(r'"/static/([\w.-]+\.(?:js|css))"', versioned, html)
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


# Lista simbolurilor din contul MT5, pentru sugestiile din câmpul de simbol al paginii.
# "visible" = cele din Market Watch, "all" = toate simbolurile oferite de broker.
@app.get("/api/symbols")
async def symbols():
    with mt5_lock:
        syms = mt5.symbols_get() or ()
    visible = [s.name for s in syms if s.visible]
    return JSONResponse({"visible": visible, "all": [s.name for s in syms]})


# ---------- Alarme ----------
# Clasele BaseModel descriu forma datelor JSON primite. FastAPI le validează automat:
# dacă lipsește un câmp obligatoriu sau are tip greșit, răspunde singur cu eroare 422.

# Datele trimise de pagină când creezi o alarmă.
class AlertIn(BaseModel):
    symbol: str
    price: float
    side: str = "bid"
    email: str = ""
    note: str = ""


# Datele pentru butonul „Trimite e-mail de test”.
class TestEmailIn(BaseModel):
    email: str


# Lista tuturor alarmelor + dacă e-mailul e configurat (pagina afișează un avertisment dacă nu).
@app.get("/api/alerts")
async def list_alerts():
    return {"alerts": alerts.all(), "smtp": alerts.mailer.configured}


# Creează o alarmă nouă, după ce verifică datele primite.
@app.post("/api/alerts")
async def create_alert(a: AlertIn):
    # Normalizăm: simbol cu majuscule, latura doar "bid" sau "ask", e-mail fără spații.
    symbol = a.symbol.strip().upper()
    side = a.side if a.side in ("bid", "ask") else "bid"
    email = a.email.strip()
    # HTTPException(400, ...) = răspuns de eroare cu mesajul afișat apoi în pagină.
    if email and not valid_email(email):
        raise HTTPException(400, "Adresă de e-mail invalidă")
    if a.price <= 0:
        raise HTTPException(400, "Preț invalid")
    with mt5_lock:
        # symbol_select îl adaugă în Market Watch (altfel MT5 nu dă cotații pentru el).
        mt5.symbol_select(symbol, True)
        tick = mt5.symbol_info_tick(symbol)
        info = mt5.symbol_info(symbol)
    if tick is None or info is None:
        raise HTTPException(400, f"Simbol necunoscut: {symbol}")
    # Prețul curent pe latura aleasă; din el se deduce direcția alarmei (peste / sub).
    current = tick.bid if side == "bid" else tick.ask
    # Comparăm rotunjit la numărul de zecimale al simbolului (ex. 5 la EURUSD).
    if round(a.price, info.digits) == round(current, info.digits):
        raise HTTPException(400, "Prețul e chiar prețul curent")
    # Nota e limitată la 200 de caractere.
    return alerts.add(symbol, a.price, side, current, email, a.note.strip()[:200], info.digits)


# Șterge o alarmă după id (butonul ✕ din listă).
@app.delete("/api/alerts/{alert_id}")
async def delete_alert(alert_id: str):
    if not alerts.delete(alert_id):
        raise HTTPException(404, "Alarma nu există")
    return {"ok": True}


# ---------- Notificări push ----------
# Abonamentul trimis de browser: `subscription` e obiectul generat de browser (adresa
# serviciului de push + cheile de criptare), `owner` = e-mailul utilizatorului (pentru a-i
# trimite doar alarmele lui), `device` = o descriere scurtă (ex. „Chrome pe Windows”).
class PushSubIn(BaseModel):
    subscription: dict
    owner: str = ""
    device: str = ""


# Doar adresa unică a abonamentului unui dispozitiv (identifică dispozitivul).
class PushEndpointIn(BaseModel):
    endpoint: str


# Cheia publică VAPID: browserul are nevoie de ea ca să se aboneze la notificările noastre.
@app.get("/api/push/key")
async def push_key():
    return {"publicKey": push.public_key}


# Salvează (sau actualizează) abonamentul unui dispozitiv.
@app.post("/api/push/subscribe")
async def push_subscribe(p: PushSubIn):
    owner = p.owner.strip()
    if owner and not valid_email(owner):
        raise HTTPException(400, "Adresă de e-mail invalidă")
    try:
        push.subscribe(p.subscription, owner, p.device)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}


# Șterge abonamentul unui dispozitiv (butonul „Dezactivează”).
@app.post("/api/push/unsubscribe")
async def push_unsubscribe(p: PushEndpointIn):
    push.unsubscribe(p.endpoint)
    return {"ok": True}


# Întreabă dacă serverul cunoaște abonamentul acestui dispozitiv
# (pagina îl retrimite dacă serverul l-a „uitat”).
@app.post("/api/push/status")
async def push_status(p: PushEndpointIn):
    return {"subscribed": push.has(p.endpoint)}


# Trimite o notificare de test doar către dispozitivul care a cerut-o.
@app.post("/api/push/test")
async def push_test(p: PushEndpointIn):
    if not push.has(p.endpoint):
        raise HTTPException(404, "Dispozitivul nu e abonat")
    # asyncio.to_thread rulează trimiterea (care așteaptă răspunsul unui server extern) într-un
    # fir separat, ca serverul web să rămână liber pentru celelalte cereri între timp.
    ok = await asyncio.to_thread(push.send, p.endpoint, {
        "title": "🔔 Nexus: notificare de test",
        "body": "Notificările push funcționează pe acest dispozitiv.",
        "tag": "nexus-test", "url": "/",
    })
    if not ok:
        raise HTTPException(502, "Serviciul de push al browserului a refuzat notificarea (vezi consola serverului)")
    return {"ok": True}


# Trimite un e-mail de test la adresa dată (butonul „Trimite e-mail de test”).
@app.post("/api/alerts/test-email")
async def test_email(t: TestEmailIn):
    if not valid_email(t.email.strip()):
        raise HTTPException(400, "Adresă de e-mail invalidă")
    try:
        # Trimiterea prin SMTP poate dura câteva secunde, deci o facem într-un fir separat.
        await asyncio.to_thread(
            alerts.mailer.send, t.email.strip(), "Nexus: e-mail de test pentru alarme",
            "Acesta este un e-mail de test trimis din aplicația Nexus Live Chart.\n\n"
            "Dacă îl primești, notificările pentru alarmele de preț funcționează.\n"
            "Când o alarmă se declanșează, vei primi un mesaj cu simbolul, prețul țintă, "
            "prețul atins și ora exactă.\n")
    except Exception as e:  # noqa: BLE001
        # Orice eroare (parolă greșită, server indisponibil) ajunge ca mesaj în pagină.
        raise HTTPException(500, str(e)) from e
    return {"ok": True}


# ---------- Date de chart ----------
# Construiește lumânări de 1s/5s/15s/30s din ticurile ultimelor HISTORY_BARS intervale.
# O lumânare = Open (primul preț din interval), High (maximul), Low (minimul), Close (ultimul).
def candles_from_ticks(symbol: str, tf: int) -> list[dict]:
    """Lumânări sub-minut construite din istoricul de ticuri (pe bid, ca în MetaTrader)."""
    with mt5_lock:
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return []
        # Intervalul cerut: de la (ultimul tic − tf × 600 secunde) până la ultimul tic.
        end = tick.time + 1
        ticks = mt5.copy_ticks_range(symbol, end - tf * HISTORY_BARS, end, mt5.COPY_TICKS_ALL)
    if ticks is None or len(ticks) == 0:
        return []
    # Ignorăm ticurile fără bid (unele ticuri conțin doar ask).
    ticks = ticks[ticks["bid"] > 0]
    if len(ticks) == 0:
        return []
    bid = ticks["bid"]
    # Pentru fiecare tic calculăm începutul intervalului din care face parte (în secunde).
    # Ex. tf=5: ticul de la 10:00:07.300 aparține lumânării care începe la 10:00:05.
    buckets = (ticks["time_msc"] // 1000 // tf) * tf
    # Pozițiile unde începe o lumânare nouă: primul tic + fiecare loc unde intervalul se schimbă.
    starts = np.flatnonzero(np.r_[True, buckets[1:] != buckets[:-1]])
    # Ultimul tic al fiecărei lumânări = cu unu înainte de începutul următoarei.
    ends = np.r_[starts[1:] - 1, len(bid) - 1]
    # reduceat calculează maximul/minimul pe fiecare grup [starts[i], starts[i+1]) dintr-o dată.
    highs = np.maximum.reduceat(bid, starts)
    lows = np.minimum.reduceat(bid, starts)
    # Formatul așteptat de biblioteca de chart din pagină.
    return [
        {"time": int(buckets[s]), "open": float(bid[s]), "high": float(h),
         "low": float(l), "close": float(bid[e])}
        for s, e, h, l in zip(starts, ends, highs, lows)
    ]


# Pentru M1 și mai mari, MT5 are deja lumânările gata calculate („rates”); doar le citim.
def candles_from_rates(symbol: str, timeframe: int) -> list[dict]:
    with mt5_lock:
        # Ultimele HISTORY_BARS lumânări, începând de la cea curentă (poziția 0).
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, HISTORY_BARS)
    if rates is None:
        return []
    return [
        {"time": int(r["time"]), "open": float(r["open"]), "high": float(r["high"]),
         "low": float(r["low"]), "close": float(r["close"])}
        for r in rates
    ]


# Ultimele `count` ticuri din ultima oră, pentru chartul de ticuri din partea de jos a paginii.
def recent_ticks(symbol: str, count: int = 600) -> list[list]:
    """Ultimele ticuri, ca chartul de ticuri să nu pornească gol."""
    with mt5_lock:
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return []
        ticks = mt5.copy_ticks_range(symbol, tick.time - 3600, tick.time + 1, mt5.COPY_TICKS_ALL)
    if ticks is None:
        return []
    # [-count:] = ultimele `count` elemente.
    ticks = ticks[-count:]
    return [[int(t["time_msc"]), float(t["bid"]), float(t["ask"])] for t in ticks]


# Pregătește primul mesaj („hello”) trimis unei pagini proaspăt conectate:
# informații despre simbol + istoricul de lumânări + ultimele ticuri.
# Întoarce None dacă simbolul nu există.
def build_hello(symbol: str, tf: int) -> dict | None:
    with mt5_lock:
        if not mt5.symbol_select(symbol, True):
            return None
        info = mt5.symbol_info(symbol)
        tick = mt5.symbol_info_tick(symbol)
    if info is None:
        return None
    # Alegem sursa lumânărilor: din ticuri (sub un minut) sau din MT5 (M1 și peste).
    mt_tf = TIMEFRAMES[tf]
    bars = candles_from_ticks(symbol, tf) if mt_tf is None else candles_from_rates(symbol, mt_tf)
    return {
        "type": "hello",
        "symbol": symbol,
        "tf": tf,
        # digits = câte zecimale are prețul (5 la EURUSD, 2 la XAUUSD).
        "digits": info.digits,
        # point = cea mai mică variație de preț (0.00001 la EURUSD); spread-ul se afișează în puncte.
        "point": info.point,
        "description": info.description,
        "last": [tick.time_msc, tick.bid, tick.ask] if tick else None,
        "recent": recent_ticks(symbol),
        "bars": bars,
    }


# Conexiunea WebSocket a unei pagini. Adresa e de forma /ws?symbol=EURUSD&tf=1
# (FastAPI preia automat symbol și tf din adresă).
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket, symbol: str = "EURUSD", tf: int = 1):
    await ws.accept()
    if tf not in TIMEFRAMES:
        tf = 1
    # build_hello face multe apeluri MT5 (posibil lente), deci rulează într-un fir separat.
    hello = await asyncio.to_thread(build_hello, symbol, tf)
    if hello is None:
        await ws.send_text(json.dumps({"type": "error", "msg": f"Simbol necunoscut: {symbol}"}))
        await ws.close()
        return

    # Ne abonăm la ticurile simbolului; primim o coadă proprie.
    q = hub.subscribe(symbol)
    try:
        await ws.send_text(json.dumps(hello))
        # Pagina nu ne trimite date, dar trebuie să „ascultăm” ca să aflăm când se închide.
        receiver = asyncio.create_task(ws.receive_text())  # detectează închiderea de către client
        while True:
            # Așteptăm, în paralel, fie un mesaj nou în coadă, fie un semnal de la pagină.
            getter = asyncio.create_task(q.get())
            done, _ = await asyncio.wait({getter, receiver}, return_when=asyncio.FIRST_COMPLETED)
            if getter in done:
                # A venit un mesaj (ticuri sau alarmă): îl trimitem paginii.
                await ws.send_text(getter.result())
            else:
                getter.cancel()
            if receiver in done:
                receiver.result()  # ridică WebSocketDisconnect dacă s-a închis
                receiver = asyncio.create_task(ws.receive_text())
    except (WebSocketDisconnect, RuntimeError):
        # Pagina s-a închis sau conexiunea a căzut – situație normală, nu o eroare.
        pass
    finally:
        # Indiferent cum s-a terminat, eliberăm coada.
        hub.unsubscribe(symbol, q)


# Punctul de pornire când rulezi `python server.py`.
# log_level="warning": nu afișăm fiecare cerere HTTP, doar avertismentele și erorile.
if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
