"""
Server de prețuri live -> browser, prin WebSocket. Sursa prețurilor se alege cu PRICE_SOURCE în .env:
  - ctrader : cTrader Open API, prin internet, fără niciun program deschis
              (configurare o singură dată: python ctrader_setup.py);
  - mt5     : terminalul MetaTrader 5 (ex. FBS), care trebuie să fie pornit și logat pe PC.
Pornire:
    python server.py            -> http://127.0.0.1:8000  (și de pe telefon, din aceeași rețea)
"""
# =====================================================================================
#  CE FACE ACEST FIȘIER (pe scurt)
# -------------------------------------------------------------------------------------
#  1. Se conectează la sursa de prețuri: cTrader prin internet (vezi ctrader.py) sau terminalul
#     MT5 de pe PC (vezi mt5feed.py). Ambele oferă aceleași funcții, deci restul codului nu
#     depinde de sursă.
#  2. Urmărește prețurile simbolurilor deschise în pagini și ale celor cu alarme active.
#     Fiecare cotație nouă (un „tic” = o nouă cotație bid/ask) ajunge imediat la server.
#  3. Ticurile noi sunt trimise instant către toate paginile deschise în browser, printr-o
#     conexiune WebSocket (o conexiune permanentă, în ambele sensuri, între pagină și server).
#  4. Tot aici sunt definite adresele API (de ex. /api/alerts) folosite de pagină pentru
#     alarme de preț, e-mail de test și notificări push.
#
#  Biblioteci folosite:
#   - websockets  : conexiunea cu cTrader (în ctrader.py).
#   - MetaTrader5 : legătura cu terminalul MT5 (în mt5feed.py, doar cu PRICE_SOURCE=mt5).
#   - FastAPI     : framework web – definește adresele (rutele) serverului.
#   - uvicorn     : serverul web propriu-zis, care rulează aplicația FastAPI.
#   - dotenv      : citește setările din fișierul .env (parole SMTP, date cTrader etc.).
#   - pydantic    : validează automat datele JSON primite de la pagină.
# =====================================================================================

# --- Biblioteci standard Python ---
# asyncio   : programare asincronă (mii de conexiuni deservite fără blocare).
# json      : transformă dicționare Python în text JSON și invers.
# os        : citirea variabilelor de mediu (setările din .env).
# re        : expresii regulate (căutare/înlocuire de text după tipare).
# socket    : folosit doar ca să aflăm IP-ul PC-ului în rețeaua locală.
# sys       : acces la consola (stdout/stderr).
# time      : ora curentă, în milisecunde.
import asyncio
import csv
import json
import os
import re
import socket
import sys
import time
import uuid
# asynccontextmanager: permite scrierea codului de pornire/oprire a serverului într-o singură funcție.
from contextlib import asynccontextmanager
# datetime: formatarea orelor (UTC) din fișierele CSV exportate.
from datetime import datetime, timezone
# Path: lucrul comod cu căi de fișiere (ROOT / "static" etc.).
from pathlib import Path

# --- Biblioteci externe (instalate cu pip, vezi requirements.txt) ---
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

# Modulele proiectului: alarmele + e-mailul (alerts.py), notificările push (push.py)
# și conexiunea cu cTrader (ctrader.py). mt5feed.py se încarcă doar dacă e folosit (mai jos).
from alerts import AlertStore, Mailer, valid_email  # noqa: E402 - după load_dotenv
from ctrader import PERIODS, CTraderError, CTraderFeed, TokenStore  # noqa: E402
from push import PushService  # noqa: E402

# --- Constante de configurare ---
HISTORY_BARS = 600      # câte lumânări trimitem la conectare pentru timeframe-urile sub un minut
# De la M1 în sus trimitem la conectare cel puțin un an de lumânări (an calendaristic întreg),
# dar cel mult MAX_INITIAL_BARS; restul istoricului se încarcă la derularea chartului spre stânga,
# câte OLDER_BARS lumânări odată (vezi /api/bars).
YEAR_S = 366 * 86400
MAX_INITIAL_BARS = 10_000
OLDER_BARS = 5000
RECENT_TICKS = 600      # câte ticuri trimitem pentru chartul de ticuri
# Pentru lumânările sub un minut cerem ticuri în bucăți (vezi CTraderFeed.ticks); cel mult
# atâtea bucăți, ca deschiderea chartului să nu dureze prea mult.
MAX_TICK_PAGES = 12

# Timeframe-uri suportate (secunde). Sub 60s lumânările se construiesc din ticuri; de la M1 în
# sus le primim gata calculate de la sursa de prețuri (vezi PERIODS în ctrader.py).
SUB_MINUTE = (1, 5, 15, 30)
TIMEFRAMES = set(SUB_MINUTE) | set(PERIODS)
# Numele timeframe-urilor, folosite în numele fișierelor CSV (0 = ticuri brute bid/ask).
TF_LABELS = {0: "ticuri", 1: "1s", 5: "5s", 15: "15s", 30: "30s", 60: "M1", 300: "M5", 900: "M15",
             1800: "M30", 3600: "H1", 14400: "H4", 86400: "D1", 604800: "W1"}
# Folderul în care se salvează exporturile CSV (câte un subfolder pentru fiecare simbol).
EXPORT_DIR = ROOT / "exporturi_csv"
# Baza de date locală cu lumânări, completată automat la fiecare simbol / timeframe deschis:
# datastore/<sursă>/<SIMBOL>/<TIMEFRAME>/<SIMBOL>_<TIMEFRAME>.csv (vezi „Datastore local”).
DATASTORE_DIR = ROOT / "datastore"

# Folderul cu fișierele paginii web (HTML, CSS, JavaScript).
STATIC = ROOT / "static"
# Adresa și portul pe care ascultă serverul (pot fi schimbate din .env).
HOST = os.getenv("HOST", "0.0.0.0")   # 0.0.0.0 = accesibil și de pe telefon din rețeaua locală
PORT = int(os.getenv("PORT", "8000"))
# Sursa prețurilor: "ctrader" sau "mt5".
PRICE_SOURCE = os.getenv("PRICE_SOURCE", "ctrader").strip().lower()
SOURCE_NAME = "MT5" if PRICE_SOURCE == "mt5" else "cTrader"


# =====================================================================================
#  TickHub – „centrala” de ticuri
# -------------------------------------------------------------------------------------
#  Problema: pot fi deschise mai multe pagini (PC, telefon, alt utilizator), fiecare pe alt
#  simbol. Nu vrem ca fiecare pagină să ceară separat prețurile de la sursă.
#  Soluția: sursa urmărește fiecare simbol O SINGURĂ dată; fiecare tic primit e pus în
#  „cozile” (Queue) paginilor abonate la acel simbol. Fiecare pagină conectată are propria
#  coadă; funcția ws_endpoint (mai jos) scoate mesajele din coadă și le trimite prin WebSocket.
# =====================================================================================
class TickHub:
    """Distribuie ticurile primite de la sursa de prețuri paginilor abonate."""

    def __init__(self):
        # Bucla asyncio a serverului web; e setată la pornire (în lifespan). Avem nevoie de ea
        # ca să putem pune mesaje în cozi din alte fire (ex. firul care trimite e-mailul).
        self.loop: asyncio.AbstractEventLoop | None = None
        # subs: pentru fiecare simbol, mulțimea cozilor paginilor care îl urmăresc.
        # Exemplu: {"EURUSD": {coada_pagina1, coada_pagina2}, "XAUUSD": {coada_pagina3}}
        self.subs: dict[str, set[asyncio.Queue]] = {}
        self.all_queues: set[asyncio.Queue] = set()   # pentru mesaje către toți clienții
        self.watched = lambda: set()                  # simboluri urmărite și fără clienți (alarme)
        self.on_ticks = None                          # callback(symbol, ticks), înaintea paginilor
        self.on_wanted = None                         # callback(simboluri) – lista de abonări dorită

    # O pagină nouă vrea ticurile unui simbol: îi creăm o coadă și o înregistrăm.
    # maxsize=5000: limită ca o pagină blocată să nu umple memoria la nesfârșit.
    def subscribe(self, symbol: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=5000)
        # setdefault creează mulțimea goală pentru simbol dacă nu există încă.
        self.subs.setdefault(symbol, set()).add(q)
        self.all_queues.add(q)
        self.sync()
        return q

    # Pagina s-a închis: îi ștergem coada. Dacă nimeni nu mai urmărește simbolul,
    # îl scoatem cu totul, ca sursa să nu-i mai urmărească prețurile degeaba.
    def unsubscribe(self, symbol: str, q: asyncio.Queue):
        self.all_queues.discard(q)
        qs = self.subs.get(symbol)
        if qs:
            qs.discard(q)
            if not qs:
                del self.subs[symbol]
        self.sync()

    # Spune sursei de prețuri ce simboluri trebuie urmărite: cele deschise în pagini plus cele
    # cu alarme active (trebuie urmărite chiar dacă nicio pagină nu e deschisă).
    def sync(self):
        if self.on_wanted:
            self.on_wanted(set(self.subs) | self.watched())

    # Pune un mesaj în cozile paginilor. symbol=None înseamnă „către toate paginile”
    # (folosit pentru evenimente de alarmă, care interesează pe toată lumea).
    # Rulează doar în bucla asyncio (cozile asyncio nu pot fi atinse din alte fire).
    def _push(self, symbol: str | None, msg: str):
        queues = list(self.all_queues if symbol is None else self.subs.get(symbol, ()))
        for q in queues:
            if q.full():          # client lent: aruncăm mesajul cel mai vechi
                q.get_nowait()
            q.put_nowait(msg)

    # Ticuri noi de la sursa de prețuri (apelată în bucla asyncio). data = [[timp_ms, bid, ask], ...]
    def ticks(self, symbol: str, data: list[list]):
        # Le dăm întâi modulului de alarme (care verifică dacă vreun preț țintă a fost atins).
        if self.on_ticks:
            self.on_ticks(symbol, data)
        # Apoi le trimitem paginilor.
        self._push(symbol, json.dumps({"type": "ticks", "d": data}))

    # Trimite un mesaj (dicționar) către TOATE paginile deschise. Folosit pentru alarme.
    def broadcast(self, msg: dict):
        """Apelabil din orice thread."""
        # call_soon_threadsafe = „execută _push în bucla asyncio, cât de curând” – singurul mod
        # sigur de a atinge cozile asyncio din alt fir (ex. firul care trimite e-mailul).
        self.loop.call_soon_threadsafe(self._push, None, json.dumps(msg))


# --- Crearea obiectelor principale și legarea lor între ele ---
# feed    : sursa de prețuri – terminalul MT5 sau cTrader (datele aplicației din .env, tokenul
#           din ctrader_token.json).
# hub     : distribuie ticurile.
# alerts  : alarmele de preț (fișierul alerts.json poate fi schimbat cu variabila ALERTS_FILE).
if PRICE_SOURCE == "mt5":
    from mt5feed import MT5Feed  # noqa: E402 - doar pe Windows, cu pachetul MetaTrader5
    feed = MT5Feed()
else:
    feed = CTraderFeed(os.getenv("CTRADER_CLIENT_ID", "").strip(),
                       os.getenv("CTRADER_CLIENT_SECRET", "").strip(),
                       TokenStore(Path(os.getenv("CTRADER_TOKEN_FILE", ROOT / "ctrader_token.json"))))
hub = TickHub()
alerts = AlertStore(Path(os.getenv("ALERTS_FILE", ROOT / "alerts.json")), Mailer())
# Fiecare tic primit de la sursa de prețuri trece prin hub.
feed.on_ticks = hub.ticks
# Paginile află când cade sau revine conexiunea cu sursa (la revenire își reîncarcă istoricul).
feed.on_status = lambda up: hub.broadcast({"type": "feed", "up": up})
# hub-ul îi spune sursei de prețuri ce simboluri să urmărească ...
hub.on_wanted = feed.want
# ... și întreabă alarmele ce simboluri trebuie urmărite permanent ...
hub.watched = alerts.active_symbols
# ... și le trimite fiecare tic nou, ca să verifice prețurile țintă.
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


# Reface periodic lista de abonări: o alarmă declanșată nu mai are nevoie de prețuri.
async def keep_synced():
    while True:
        await asyncio.sleep(5)
        hub.sync()


# „lifespan” = ce se întâmplă la pornirea și la oprirea serverului.
# Tot ce e înainte de `yield` rulează la pornire; tot ce e după, la oprire.
@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Fără datele cTrader aplicația nu are sens, așa că oprim pornirea cu un mesaj clar.
    if PRICE_SOURCE == "ctrader":
        if not feed.client_id or not feed.client_secret:
            raise RuntimeError("Lipsesc CTRADER_CLIENT_ID / CTRADER_CLIENT_SECRET din .env (vezi .env.example)")
        if not feed.tokens.configured:
            raise RuntimeError("Lipsește tokenul cTrader. Rulează întâi: python ctrader_setup.py "
                               "(sau pune PRICE_SOURCE=mt5 în .env ca să folosești terminalul MT5)")
    elif PRICE_SOURCE != "mt5":
        raise RuntimeError(f"PRICE_SOURCE necunoscut: {PRICE_SOURCE} (folosește ctrader sau mt5)")
    print(f"[WEB] sursa prețurilor: {SOURCE_NAME}")
    hub.loop = asyncio.get_running_loop()
    # Pornim conexiunea permanentă cu sursa și sincronizarea abonărilor, în fundal.
    tasks = [asyncio.create_task(feed.run()), asyncio.create_task(keep_synced())]
    hub.sync()
    # Așteptăm puțin prima conectare, ca mesajele de mai jos să apară după „conectat”.
    try:
        await asyncio.wait_for(feed.ready.wait(), 20)
    except asyncio.TimeoutError:
        print(f"[{SOURCE_NAME}] încă neconectat; serverul pornește oricum și reîncearcă în fundal")
    # Mesaje informative în consolă: adresele la care se deschide pagina.
    print(f"[WEB] pe acest PC:  http://127.0.0.1:{PORT}")
    # (`:=` atribuie și testează în același timp: ip primește rezultatul lui lan_ip().)
    if HOST == "0.0.0.0" and (ip := lan_ip()):
        print(f"[WEB] de pe telefon: http://{ip}:{PORT}  (aceeași rețea Wi-Fi)")
    if not alerts.mailer.configured:
        print("[ALERT] SMTP neconfigurat: alarmele apar în pagină, dar nu se trimit e-mailuri (vezi .env.example)")
    yield
    # La oprire: oprim conexiunea cu sursa de prețuri.
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


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


# Lista simbolurilor din cont, pentru sugestiile din câmpul de simbol al paginii.
# "visible" = cele afișate/active (Market Watch la MT5), "all" = toate simbolurile brokerului.
@app.get("/api/symbols")
async def symbols():
    visible, everything = feed.symbol_names()
    return JSONResponse({"visible": visible, "all": everything})


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
    # Normalizăm: latura doar "bid" sau "ask", e-mail fără spații.
    side = a.side if a.side in ("bid", "ask") else "bid"
    email = a.email.strip()
    # HTTPException(400, ...) = răspuns de eroare cu mesajul afișat apoi în pagină.
    if email and not valid_email(email):
        raise HTTPException(400, "Adresă de e-mail invalidă")
    if a.price <= 0:
        raise HTTPException(400, "Preț invalid")
    # Numele exact al simbolului la broker (ex. „eurusd” -> „EURUSD”).
    symbol = feed.resolve(a.symbol.strip())
    if symbol is None:
        raise HTTPException(400, f"Simbol necunoscut: {a.symbol.strip().upper()}")
    try:
        info = await feed.info(symbol)
        tick = await feed.quote(symbol)
    except (CTraderError, ConnectionError, asyncio.TimeoutError) as e:
        raise HTTPException(503, f"{SOURCE_NAME} indisponibil: {e}") from e
    if tick is None:
        raise HTTPException(400, f"Nu am niciun preț pentru {symbol}")
    # Prețul curent pe latura aleasă; din el se deduce direcția alarmei (peste / sub).
    current = tick[1] if side == "bid" else tick[2]
    # Comparăm rotunjit la numărul de zecimale al simbolului (ex. 5 la EURUSD).
    if round(a.price, info["digits"]) == round(current, info["digits"]):
        raise HTTPException(400, "Prețul e chiar prețul curent")
    # Nota e limitată la 200 de caractere.
    alert = alerts.add(symbol, a.price, side, current, email, a.note.strip()[:200], info["digits"])
    # Simbolul alarmei trebuie urmărit de acum înainte, chiar fără pagini deschise.
    hub.sync()
    return alert


# Șterge o alarmă după id (butonul ✕ din listă).
@app.delete("/api/alerts/{alert_id}")
async def delete_alert(alert_id: str):
    if not alerts.delete(alert_id):
        raise HTTPException(404, "Alarma nu există")
    hub.sync()
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
# Construiește lumânări de 1s/5s/15s/30s din ticurile bid [(timp_ms, preț), ...] (cronologic).
# O lumânare = Open (primul preț din interval), High (maximul), Low (minimul), Close (ultimul).
# volume = numărul de ticuri din lumânare. limit = câte lumânări (cele mai noi) întoarcem.
def candles_from_ticks(ticks: list[tuple[int, float]], tf: int, limit: int | None = HISTORY_BARS) -> list[dict]:
    bars: list[dict] = []
    for msc, p in ticks:
        # Începutul intervalului din care face parte ticul (în secunde).
        # Ex. tf=5: ticul de la 10:00:07.300 aparține lumânării care începe la 10:00:05.
        t = msc // 1000 // tf * tf
        b = bars[-1] if bars else None
        if b and b["time"] == t:
            b["high"] = max(b["high"], p)
            b["low"] = min(b["low"], p)
            b["close"] = p
            b["volume"] += 1
        else:
            bars.append({"time": t, "open": p, "high": p, "low": p, "close": p, "volume": 1})
    return bars[-limit:] if limit else bars


# Combină ticurile bid și ask (vin separat de la cTrader) în [timp_ms, bid, ask]: la fiecare
# moment folosim ultimul bid și ultimul ask cunoscute. Întoarce ultimele `count` ticuri.
def merge_ticks(bid: list[tuple[int, float]], ask: list[tuple[int, float]], count: int) -> list[list]:
    events = sorted([(t, 0, p) for t, p in bid] + [(t, 1, p) for t, p in ask])
    b = a = None
    out: list[list] = []
    for t, side, p in events:
        if side == 0:
            b = p
        else:
            a = p
        if b is None or a is None:
            continue
        # Bid și ask schimbate în aceeași milisecundă = un singur tic.
        if out and out[-1][0] == t:
            out[-1] = [t, b, a]
        else:
            out.append([t, b, a])
    return out[-count:]


# Pregătește primul mesaj („hello”) trimis unei pagini proaspăt conectate:
# informații despre simbol + istoricul de lumânări + ultimele ticuri.
async def build_hello(symbol: str, tf: int) -> dict:
    info = await feed.info(symbol)
    last = await feed.quote(symbol)
    # Istoricul se termină la ultimul tic (în weekend, la închiderea pieței de vineri).
    end = (last[0] if last else int(time.time() * 1000)) + 1
    if tf in PERIODS:
        # M1 și peste: lumânările vin gata calculate de la sursa de prețuri (cel puțin un an).
        count = min(-(-YEAR_S // tf), MAX_INITIAL_BARS)
        bars = await feed.trendbars(symbol, tf, count, end)
        bid = await feed.ticks(symbol, "BID", end - 3600_000, end, max_pages=1)
    else:
        # Sub un minut: le construim din ticurile bid ale ultimelor HISTORY_BARS intervale.
        bid = await feed.ticks(symbol, "BID", end - tf * HISTORY_BARS * 1000, end, max_pages=MAX_TICK_PAGES)
        bars = candles_from_ticks(bid, tf)
    ask = await feed.ticks(symbol, "ASK", end - 3600_000, end, max_pages=1)
    return {
        "type": "hello",
        "symbol": symbol,
        "tf": tf,
        # digits = câte zecimale are prețul (5 la EURUSD, 2 la XAUUSD).
        "digits": info["digits"],
        # point = cea mai mică variație de preț (0.00001 la EURUSD); spread-ul se afișează în puncte.
        "point": info["point"],
        "description": info["description"],
        "last": last,
        "recent": merge_ticks(bid, ask, RECENT_TICKS),
        "bars": bars,
    }


# Lumânări mai vechi decât `before` (secunde UTC), cerute de pagină când chartul e derulat spre
# stânga. O listă goală înseamnă că sursa nu mai are istoric.
@app.get("/api/bars")
async def older_bars(symbol: str, tf: int, before: int):
    name = feed.resolve(symbol)
    if name is None or tf not in TIMEFRAMES:
        raise HTTPException(400, "Simbol sau timeframe necunoscut")
    end = before * 1000
    try:
        if tf in PERIODS:
            bars = await feed.trendbars(name, tf, OLDER_BARS, end)
        else:
            # Sub un minut: aflăm din ultima lumânare M1 unde se termină istoricul (în weekend
            # sau de sărbători nu există ticuri), apoi construim lumânările din ticurile de dinainte.
            m1 = await feed.trendbars(name, 60, 1, end)
            if not m1:
                return {"bars": []}
            end = min(end, (m1[-1]["time"] + 60) * 1000)
            bid = await feed.ticks(name, "BID", end - tf * HISTORY_BARS * 1000, end, max_pages=MAX_TICK_PAGES)
            bars = [b for b in candles_from_ticks(bid, tf) if b["time"] < before]
    except (CTraderError, ConnectionError, asyncio.TimeoutError) as e:
        raise HTTPException(503, f"{SOURCE_NAME} indisponibil: {e}") from e
    return {"bars": bars}


# ---------- Linii de trend (analysis.py) ----------
# Butonul „📈 Trend” trimite lumânările vizibile pe chart. Rulăm pe ele partea a 2-a din
# analysis.py (după linia #----#), exact cum e scrisă: ea citește `df` și umple listele maxim,
# minim, dr_max_valid și dr_min_valid. Fișierul e citit la fiecare cerere, deci o modificare în
# analysis.py se vede la următoarea apăsare, fără repornirea serverului.
ANALYSIS = Path(__file__).with_name("analysis.py")
# Algoritmul verifică fiecare pereche de pivoturi pe toate lumânările de după ea, deci durata
# crește foarte repede: ~6 s la 300 de lumânări, ~30 s la 500. Peste limită luăm ultimele 300.
TREND_MAX_BARS = 300


class TrendBarsIn(BaseModel):
    bars: list[dict]   # lumânările de pe chart: {time, open, high, low, close}


def run_trend_analysis(bars: list[dict]) -> dict:
    import pandas as pd  # doar aici, ca restul serverului să nu depindă de pandas

    src = ANALYSIS.read_text(encoding="utf-8")
    parts = re.split(r"^#-{5,}#\s*$", src, maxsplit=1, flags=re.M)
    if len(parts) < 2:
        raise ValueError("Nu găsesc linia #----# care separă partea a 2-a din analysis.py")
    df = pd.DataFrame(bars, columns=["time", "open", "high", "low", "close"])
    ns = {"df": df, "pd": pd}
    exec(compile(parts[1], str(ANALYSIS), "exec"), ns)

    # Fiecare dreaptă validă e o pereche (i, j) de poziții din maxim / minim; un punct e
    # (indexul lumânării în df, prețul). O desenăm de la primul punct până la ultima lumânare.
    last = len(df) - 1

    def lines(points, pairs):
        out = []
        for i, j in pairs:
            (x1, y1), (x2, y2) = points[i], points[j]
            y_end = y1 + (y2 - y1) / (x2 - x1) * (last - x1)
            out.append({"from": {"time": bars[x1]["time"], "value": float(y1)},
                        "via": {"time": bars[x2]["time"], "value": float(y2)},
                        "to": {"time": bars[last]["time"], "value": float(y_end)}})
        return out

    return {"max": lines(ns["maxim"], ns["dr_max_valid"]),
            "min": lines(ns["minim"], ns["dr_min_valid"]),
            "pivots": {"max": len(ns["maxim"]), "min": len(ns["minim"])},
            "bars": len(df)}


@app.post("/api/trendlines")
async def trendlines(t: TrendBarsIn):
    bars = t.bars[-TREND_MAX_BARS:]
    if len(bars) < 7:
        raise HTTPException(400, "Sunt prea puține lumânări pe ecran (minimum 7)")
    try:
        # Calculul e lent și blocant: îl rulăm pe un fir separat, ca prețurile live să nu se oprească.
        result = await asyncio.to_thread(run_trend_analysis, bars)
    except Exception as e:
        raise HTTPException(500, f"Eroare în analysis.py: {type(e).__name__}: {e}") from e
    result["truncated"] = len(t.bars) > TREND_MAX_BARS
    return result


# ---------- Export CSV ----------
# Un export poate dura (un an de M1 sau de ticuri), deci rulează în fundal: pagina pornește
# exportul, apoi întreabă periodic cât s-a făcut. Aici sunt exporturile din această sesiune.
EXPORTS: dict[str, dict] = {}


class ExportIn(BaseModel):
    symbol: str
    tf: int          # secunde; 0 = ticuri brute (bid / ask)
    start: str       # datele alese (AAAA-LL-ZZ), folosite în numele fișierului
    end: str
    start_ms: int    # începutul zilei de start și sfârșitul zilei de final, în ora utilizatorului
    end_ms: int


# Ora UTC din CSV: „2025-10-15 21:00:00” (cu milisecunde pentru ticuri).
def utc_text(ms: int, millis: bool = False) -> str:
    text = datetime.fromtimestamp(ms // 1000, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return f"{text}.{ms % 1000:03d}" if millis else text


# Rândurile CSV ale unor lumânări: time_utc, open, high, low, close, volume.
def bars_to_rows(bars: list[dict], digits: int) -> list[list]:
    def price(p: float) -> str:
        return f"{p:.{digits}f}"
    return [[utc_text(b["time"] * 1000), price(b["open"]), price(b["high"]), price(b["low"]),
             price(b["close"]), b.get("volume", "")] for b in bars]


# Pornește un export în fundal și întoarce starea lui (cu id-ul după care pagina îl urmărește).
@app.post("/api/export")
async def start_export(e: ExportIn):
    if e.tf not in TF_LABELS:
        raise HTTPException(400, "Timeframe necunoscut")
    # Datele ajung în numele fișierului, deci acceptăm doar forma AAAA-LL-ZZ.
    if not all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) for d in (e.start, e.end)):
        raise HTTPException(400, "Dată invalidă")
    # Începutul e aliniat la minut (lumânările sub un minut se construiesc pe bucăți aliniate);
    # sfârșitul nu trece de momentul de acum.
    start = e.start_ms // 60_000 * 60_000
    end = min(e.end_ms, int(time.time() * 1000) + 1)
    if end <= start:
        raise HTTPException(400, "Data de start trebuie să fie înaintea datei de final")
    name = feed.resolve(e.symbol.strip())
    if name is None:
        raise HTTPException(400, f"Simbol necunoscut: {e.symbol.strip().upper()}")
    try:
        info = await feed.info(name)
    except (CTraderError, ConnectionError, asyncio.TimeoutError) as err:
        raise HTTPException(503, f"{SOURCE_NAME} indisponibil: {err}") from err
    # Ex.: exporturi_csv/EURUSD/EURUSD_M1_2025-10-15_2026-10-15.csv
    safe = re.sub(r"[^\w.-]", "_", name)
    path = EXPORT_DIR / safe / f"{safe}_{TF_LABELS[e.tf]}_{e.start}_{e.end}.csv"
    job = {"id": uuid.uuid4().hex[:12], "status": "running", "progress": 0.0, "rows": 0,
           "file": path.relative_to(ROOT).as_posix(), "name": path.name, "error": ""}
    EXPORTS[job["id"]] = job
    asyncio.create_task(run_export(job, path, name, e.tf, start, end, info["digits"]))
    return job


# Starea unui export: progres (0..1), rânduri scrise, eventuala eroare.
@app.get("/api/export/{job_id}")
async def export_status(job_id: str):
    job = EXPORTS.get(job_id)
    if job is None:
        raise HTTPException(404, "Exportul nu există")
    return job


# Descarcă fișierul unui export terminat (util mai ales de pe telefon).
@app.get("/api/export/{job_id}/file")
async def export_file(job_id: str):
    job = EXPORTS.get(job_id)
    if job is None or job["status"] != "done":
        raise HTTPException(404, "Exportul nu există sau nu e gata")
    return FileResponse(ROOT / job["file"], media_type="text/csv", filename=job["name"])


# Scrie CSV-ul bucată cu bucată, ca un an de date să nu stea tot în memorie. Fișierul se scrie
# întâi cu extensia .tmp și primește numele final doar la sfârșit (nu rămân fișiere incomplete).
async def run_export(job: dict, path: Path, name: str, tf: int, start: int, end: int, digits: int):
    def price(p: float) -> str:
        return f"{p:.{digits}f}"

    def bar_rows(bars: list[dict]) -> list[list]:
        return bars_to_rows(bars, digits)

    tmp = path.with_suffix(".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tmp.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if tf == 0:
                w.writerow(["time_utc", "bid", "ask"])
            else:
                w.writerow(["time_utc", "open", "high", "low", "close", "volume"])
            # Lumânările gata calculate se cer în bucăți mari; ticurile (și lumânările sub un minut,
            # construite din ticuri) câte o oră, ca prețurile live să nu fie blocate mult timp.
            step = max(tf * 1000 * 20_000, 86400_000) if tf in PERIODS else 3600_000
            last = [None, None]   # ultimele bid / ask, păstrate de la o bucată la alta (ticuri)
            t = start
            while t < end:
                to = min(t + step, end)
                if tf in PERIODS:
                    rows = bar_rows(await feed.bars_range(name, tf, t, to))
                else:
                    bid = await feed.ticks(name, "BID", t, to - 1, max_pages=100_000)
                    bid = [x for x in bid if t <= x[0] < to]
                    if tf == 0:
                        ask = await feed.ticks(name, "ASK", t, to - 1, max_pages=100_000)
                        ask = [x for x in ask if t <= x[0] < to]
                        rows = await asyncio.to_thread(tick_rows, bid, ask, last, price)
                    else:
                        rows = bar_rows(await asyncio.to_thread(candles_from_ticks, bid, tf, None))
                await asyncio.to_thread(w.writerows, rows)
                job["rows"] += len(rows)
                t = to
                job["progress"] = (t - start) / (end - start)
        tmp.replace(path)
        job["status"] = "done"
        print(f"[EXPORT] {job['file']}: {job['rows']} rânduri")
    except Exception as err:  # noqa: BLE001 - orice eroare ajunge ca mesaj în pagină
        job["status"] = "error"
        job["error"] = str(err) or type(err).__name__
        print(f"[EXPORT] {job['file']}: eroare: {job['error']}")
        tmp.unlink(missing_ok=True)


# Combină ticurile bid și ask ale unei bucăți în rânduri CSV [timp, bid, ask], ca merge_ticks,
# dar pornind de la ultimele prețuri ale bucății anterioare (păstrate în `last`).
def tick_rows(bid: list[tuple[int, float]], ask: list[tuple[int, float]], last: list, price) -> list[list]:
    events = sorted([(t, 0, p) for t, p in bid] + [(t, 1, p) for t, p in ask])
    out: list[list] = []
    prev = None
    for t, side, p in events:
        last[side] = p
        if last[0] is None or last[1] is None:
            continue
        row = [utc_text(t, millis=True), price(last[0]), price(last[1])]
        # Bid și ask schimbate în aceeași milisecundă = un singur tic.
        if prev == t:
            out[-1] = row
        else:
            out.append(row)
        prev = t
    return out


# ---------- Datastore local ----------
# De fiecare dată când o pagină deschide un simbol / timeframe (M1 și peste), lumânările primite
# pentru chart se salvează și pe disc. Structura folderelor spune ce conține fiecare fișier:
#
#   datastore/
#     ctrader/                     <- sursa prețurilor (cTrader și MT5 au prețuri / ore diferite)
#       EURUSD/                    <- simbolul
#         D1/                      <- timeframe-ul
#           EURUSD_D1.csv          <- lumânările: time_utc,open,high,low,close,volume (UTC)
#           info.json              <- descrierea fișierului: perioada, numărul de lumânări etc.
#
# Prima deschidere creează fișierul cu istoricul trimis chartului (cel puțin un an, cel mult
# 10.000 de lumânări). La următoarele se adaugă doar lumânările noi, până la zi; dacă între
# ultima lumânare salvată și istoricul chartului e o pauză, o cerem separat de la sursă.
# Se salvează doar lumânările închise: cea în curs se schimbă la fiecare tic.
# Sub un minut (1s–30s) nu salvăm: lumânările se construiesc din ticuri și acoperă doar câteva minute.
STORE_LOCKS: dict[tuple[str, int], asyncio.Lock] = {}
STORE_TASKS: set[asyncio.Task] = set()   # referințe la salvările în curs (altfel pot fi oprite)
CSV_HEADER = ["time_utc", "open", "high", "low", "close", "volume"]


def store_path(name: str, tf: int) -> Path:
    safe = re.sub(r"[^\w.-]", "_", name)
    label = TF_LABELS[tf]
    return DATASTORE_DIR / PRICE_SOURCE / safe / label / f"{safe}_{label}.csv"


# Momentul (secunde UTC) ultimei lumânări din fișier, citind doar sfârșitul lui.
def last_stored_time(path: Path) -> int | None:
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        f.seek(max(0, f.tell() - 4096))
        lines = f.read().decode("utf-8").strip().splitlines()
    if not lines or lines[-1].startswith("time_utc"):
        return None
    text = lines[-1].split(",", 1)[0]
    return int(datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp())


def write_store_info(path: Path, name: str, tf: int, digits: int, added: int):
    info_path = path.with_name("info.json")
    try:
        old = json.loads(info_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = {}
    with path.open(encoding="utf-8") as f:
        rows = sum(1 for _ in f) - 1
        f.seek(0)
        next(f, None)
        first = (next(f, "") or "").split(",", 1)[0]
    info = {
        "source": PRICE_SOURCE, "symbol": name, "timeframe": TF_LABELS[tf], "seconds": tf,
        "digits": digits, "columns": CSV_HEADER, "timezone": "UTC",
        "first_bar": first, "last_bar": utc_text(last_stored_time(path) * 1000) if rows else "",
        "bars": rows, "created": old.get("created") or utc_text(int(time.time() * 1000)),
        "updated": utc_text(int(time.time() * 1000)), "last_added": added,
    }
    tmp = info_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(info_path)


async def save_to_store(name: str, tf: int, bars: list[dict], digits: int):
    if tf not in PERIODS:
        return
    path = store_path(name, tf)
    rel = path.parent.relative_to(ROOT).as_posix()
    lock = STORE_LOCKS.setdefault((name, tf), asyncio.Lock())
    async with lock:
        try:
            now = time.time()
            closed = [b for b in bars if b["time"] + tf <= now]
            if path.exists():
                last = await asyncio.to_thread(last_stored_time, path)
                if last is not None:
                    # Pauză între fișier și istoricul chartului: cerem lumânările lipsă de la sursă.
                    first = closed[0]["time"] if closed else int(now)
                    if first > last + tf:
                        gap = await feed.bars_range(name, tf, (last + tf) * 1000, first * 1000)
                        closed = gap + closed
                    closed = [b for b in closed if b["time"] > last and b["time"] + tf <= now]
                rows = bars_to_rows(closed, digits)
                if rows:
                    def append():
                        with path.open("a", newline="", encoding="utf-8") as f:
                            csv.writer(f).writerows(rows)
                    await asyncio.to_thread(append)
            else:
                rows = bars_to_rows(closed, digits)
                if not rows:
                    return
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")

                def create():
                    with tmp.open("w", newline="", encoding="utf-8") as f:
                        w = csv.writer(f)
                        w.writerow(CSV_HEADER)
                        w.writerows(rows)
                    tmp.replace(path)
                await asyncio.to_thread(create)
            await asyncio.to_thread(write_store_info, path, name, tf, digits, len(rows))
            print(f"[DATASTORE] {rel}: +{len(rows)} lumânări")
        except Exception as e:  # noqa: BLE001 - salvarea nu trebuie să afecteze chartul
            print(f"[DATASTORE] {rel}: eroare: {type(e).__name__}: {e}")


# Conexiunea WebSocket a unei pagini. Adresa e de forma /ws?symbol=EURUSD&tf=1
# (FastAPI preia automat symbol și tf din adresă).
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket, symbol: str = "EURUSD", tf: int = 1):
    await ws.accept()
    if tf not in TIMEFRAMES:
        tf = 1

    # Trimite o eroare paginii și închide. retry=True: pagina reîncearcă singură mai târziu
    # (ex. sursa de prețuri temporar indisponibilă); fără retry: eroare definitivă (simbol inexistent).
    async def fail(msg: str, retry: bool):
        await ws.send_text(json.dumps({"type": "error", "msg": msg, "retry": retry}))
        await ws.close()

    try:
        await asyncio.wait_for(feed.ready.wait(), 10)
    except asyncio.TimeoutError:
        await fail(f"{SOURCE_NAME} indisponibil", retry=True)
        return
    name = feed.resolve(symbol)
    if name is None:
        await fail(f"Simbol necunoscut: {symbol}", retry=False)
        return

    # Ne abonăm la ticurile simbolului ÎNAINTE de a cere istoricul, ca să nu pierdem ticurile
    # sosite între timp; primim o coadă proprie.
    q = hub.subscribe(name)
    try:
        try:
            hello = await build_hello(name, tf)
        except (CTraderError, ConnectionError, asyncio.TimeoutError) as e:
            print(f"[WEB] istoric {name}: {e}")
            await fail(f"{SOURCE_NAME} indisponibil", retry=True)
            return
        await ws.send_text(json.dumps(hello))
        # Salvăm lumânările și în datastore, în fundal (chartul nu așteaptă după disc).
        task = asyncio.create_task(save_to_store(name, tf, hello["bars"], hello["digits"]))
        STORE_TASKS.add(task)
        task.add_done_callback(STORE_TASKS.discard)
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
        hub.unsubscribe(name, q)


# Punctul de pornire când rulezi `python server.py`.
# log_level="warning": nu afișăm fiecare cerere HTTP, doar avertismentele și erorile.
if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
