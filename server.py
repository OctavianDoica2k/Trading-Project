"""
Server de prețuri live -> browser, prin WebSocket. Serverul pornește pe sursa din PRICE_SOURCE (.env,
implicit cTrader); în timpul rulării sursa se poate schimba din pagină (lista din bara de sus):
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
#   - MetaTrader5 : legătura cu terminalul MT5 (în mt5feed.py, doar când sursa e MT5).
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
# csv       : scrierea fișierelor CSV exportate.
# uuid      : identificatori unici pentru exporturi.
import asyncio
import csv
import json
import os
import re
import socket
import sys
import time
import uuid
# ProcessPoolExecutor: procese separate pentru calculul indicatorilor; BrokenProcessPool = eroarea
# primită când unul dintre aceste procese a murit.
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
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
import indicators as calc  # noqa: E402 - calculul indicatorilor (în procese separate)
import mtf  # noqa: E402 - zilele din datastore pentru „Trend multi-timeframe”
from alerts import AlertStore, Mailer, valid_email  # noqa: E402 - după load_dotenv
from ctrader import PERIODS, CTraderError, CTraderFeed, TokenStore  # noqa: E402
from datastore import Datastore  # noqa: E402 - baza de date locală cu lumânări
from heikin_aishi import heikin_ashi  # noqa: E402 - lumânările Heikin-Ashi de pe chart
import fastframe  # noqa: E402 - rulează heikin_ashi() repede (vezi fastframe.py)
from push import PushService  # noqa: E402

# --- Constante de configurare ---
HISTORY_BARS = 600      # câte lumânări trimitem la conectare pentru timeframe-urile sub un minut
# De la M1 în sus trimitem la conectare ultimele MAX_INITIAL_BARS lumânări închise din datastore
# (+ lumânarea în curs, de la sursă); restul istoricului se încarcă la derularea chartului spre
# stânga, câte OLDER_BARS lumânări odată (vezi /api/bars): întâi din datastore, apoi de la sursă.
MAX_INITIAL_BARS = 10_000
OLDER_BARS = 5000
RECENT_TICKS = 600      # câte ticuri trimitem pentru chartul de ticuri
# Pentru lumânările sub un minut cerem ticuri în bucăți (vezi CTraderFeed.ticks); cel mult
# atâtea bucăți, ca deschiderea chartului să nu dureze prea mult.
MAX_TICK_PAGES = 12
# Cât așteptăm istoricul pentru o pagină nouă înainte să-i spunem să reîncerce (secunde).
HELLO_TIMEOUT = 30

# Timeframe-uri suportate (secunde). Sub 60s lumânările se construiesc din ticuri; de la M1 în
# sus le primim gata calculate de la sursa de prețuri (vezi PERIODS în ctrader.py).
SUB_MINUTE = (1, 5, 15, 30)
TIMEFRAMES = set(SUB_MINUTE) | set(PERIODS)
# Numele timeframe-urilor, folosite în numele fișierelor CSV (0 = ticuri brute bid/ask).
TF_LABELS = {0: "ticuri", 1: "1s", 5: "5s", 15: "15s", 30: "30s", 60: "M1", 300: "M5", 900: "M15",
             1800: "M30", 3600: "H1", 14400: "H4", 86400: "D1", 604800: "W1"}
# Folderul în care se salvează exporturile CSV (câte un subfolder pentru fiecare simbol).
EXPORT_DIR = ROOT / "exporturi_csv"
# Baza de date locală cu lumânări, ținută la zi pentru simbolul / timeframe-ul deschis:
# datastore/<sursă>/<SIMBOL>/<TIMEFRAME>/<SIMBOL>_<TIMEFRAME>.csv (vezi datastore.py).
DATASTORE_DIR = ROOT / "datastore"

# Folderul cu fișierele paginii web (HTML, CSS, JavaScript).
STATIC = ROOT / "static"
# Adresa și portul pe care ascultă serverul (pot fi schimbate din .env).
HOST = os.getenv("HOST", "0.0.0.0")   # 0.0.0.0 = accesibil și de pe telefon din rețeaua locală
PORT = int(os.getenv("PORT", "8000"))
# Se schimbă la fiecare pornire a serverului: pagina îl primește în hello și, la prima deschidere
# după o repornire, pornește cu chartul fără indicatori (indicators.js).
BOOT_ID = f"{time.time_ns():x}"
# Sursele de prețuri: id (folosit în .env, în adrese și în datastore) -> nume afișat.
SOURCES = {"mt5": "MT5", "ctrader": "cTrader"}
# Sursa activă: la pornire cea din .env (implicit cTrader); se poate schimba din pagină, până la
# următoarea repornire (vezi use_source).
PRICE_SOURCE = os.getenv("PRICE_SOURCE", "ctrader").strip().lower()
SOURCE_NAME = SOURCES.get(PRICE_SOURCE, PRICE_SOURCE)
CTRADER_TOKEN_FILE = Path(os.getenv("CTRADER_TOKEN_FILE", ROOT / "ctrader_token.json"))


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


# =====================================================================================
#  Sursa de prețuri (MT5 sau cTrader), aleasă la pornire și schimbabilă din pagină
# -------------------------------------------------------------------------------------
#  `feed` e sursa activă; tot restul serverului o folosește prin această variabilă globală.
#  La schimbare (use_source) oprim conexiunea veche, creăm și pornim una nouă, apoi anunțăm
#  paginile, care își cer din nou istoricul de la noua sursă.
# =====================================================================================
# De ce nu se poate folosi o sursă acum (text pentru pagină), sau None dacă se poate.
def source_problem(source: str) -> str | None:
    if source == "mt5":
        try:
            import MetaTrader5  # noqa: F401 - verificăm doar că pachetul există
        except ImportError:
            return "lipsește pachetul MetaTrader5 (pip install MetaTrader5, doar pe Windows)"
        return None
    if source == "ctrader":
        if not os.getenv("CTRADER_CLIENT_ID", "").strip() or not os.getenv("CTRADER_CLIENT_SECRET", "").strip():
            return "lipsesc CTRADER_CLIENT_ID / CTRADER_CLIENT_SECRET din .env"
        if not TokenStore(CTRADER_TOKEN_FILE).configured:
            return "lipsește tokenul; rulează o dată: python ctrader_setup.py"
        return None
    return f"sursă necunoscută: {source}"


# O conexiune nouă cu sursa (de fiecare dată una nouă, ca să citească tokenul / setările curente).
def make_feed(source: str):
    if source == "mt5":
        from mt5feed import MT5Feed  # doar pe Windows, cu pachetul MetaTrader5
        f = MT5Feed()
    else:
        f = CTraderFeed(os.getenv("CTRADER_CLIENT_ID", "").strip(),
                        os.getenv("CTRADER_CLIENT_SECRET", "").strip(), TokenStore(CTRADER_TOKEN_FILE))
    # Fiecare tic primit de la sursa de prețuri trece prin hub.
    f.on_ticks = hub.ticks
    # Paginile află când cade sau revine conexiunea cu sursa (la revenire își reîncarcă istoricul).
    # Mesajele unei surse care nu mai e activă (oprită la schimbare) sunt ignorate.
    f.on_status = lambda up: f is feed and hub.broadcast({"type": "feed", "up": up})
    return f


feed = None                  # sursa activă (setată în lifespan, la pornire)
feed_task: asyncio.Task | None = None
source_lock = asyncio.Lock()   # o singură schimbare de sursă odată


# Oprește sursa activă (dacă există) și o pornește pe cea cerută. Alegerea nu se salvează:
# la repornire serverul pleacă din nou de la PRICE_SOURCE (implicit cTrader).
async def use_source(source: str):
    global feed, feed_task, PRICE_SOURCE, SOURCE_NAME
    async with source_lock:
        if feed_task:
            feed.on_status = None   # oprirea voită nu e o „deconectare” de anunțat paginilor
            feed_task.cancel()
            await asyncio.gather(feed_task, return_exceptions=True)
        feed = make_feed(source)
        PRICE_SOURCE, SOURCE_NAME = source, SOURCES[source]
        feed_task = asyncio.create_task(feed.run())
        hub.sync()
        print(f"[WEB] sursa prețurilor: {SOURCE_NAME}")


# --- Crearea obiectelor principale și legarea lor între ele ---
# hub     : distribuie ticurile.
# alerts  : alarmele de preț (fișierul alerts.json poate fi schimbat cu variabila ALERTS_FILE).
hub = TickHub()
alerts = AlertStore(Path(os.getenv("ALERTS_FILE", ROOT / "alerts.json")), Mailer())
# hub-ul îi spune sursei de prețuri ce simboluri să urmărească ...
hub.on_wanted = lambda names: feed and feed.want(names)
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
    # Sursa din .env; dacă nu se poate folosi (ex. cTrader fără token), trecem pe cealaltă.
    source = PRICE_SOURCE
    if source not in SOURCES:
        raise RuntimeError(f"PRICE_SOURCE necunoscut: {source} (folosește ctrader sau mt5)")
    if problem := source_problem(source):
        other = next(s for s in SOURCES if s != source)
        if source_problem(other):
            raise RuntimeError(f"{SOURCES[source]}: {problem}")
        print(f"[WEB] {SOURCES[source]} indisponibil ({problem}); folosesc {SOURCES[other]}")
        source = other
    hub.loop = asyncio.get_running_loop()
    # Procesele pentru calculul indicatorilor (pornesc acum, ca primul calcul să nu aștepte după ele).
    get_pool()
    # Pornim conexiunea permanentă cu sursa și sincronizarea abonărilor, în fundal.
    await use_source(source)
    tasks = [asyncio.create_task(keep_synced())]
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
    # La oprire: oprim conexiunea cu sursa de prețuri, actualizarea datastore-ului și calculele.
    for t in [*tasks, feed_task, *(p.keeper for p in PAIRS.values() if p.keeper)]:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    if pool:
        pool.shutdown(wait=False, cancel_futures=True)


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


# Lista simbolurilor sursei active. "visible" = cele afișate/active (Market Watch la MT5), "all" =
# toate simbolurile brokerului (sugestiile din fereastra de export); "tree" = simbolurile cu
# descrierea și categoria lor, pentru meniul de simboluri din bara de sus.
@app.get("/api/symbols")
async def symbols():
    visible, everything = feed.symbol_names()
    return JSONResponse({"source": PRICE_SOURCE, "visible": visible, "all": everything,
                         "tree": feed.symbol_tree()})


# ---------- Sursa prețurilor ----------
# Sursele disponibile, pentru lista din bara de sus: care e activă, dacă e conectată și, pentru
# cele care nu se pot folosi, de ce (ex. cTrader fără token).
@app.get("/api/sources")
async def list_sources():
    return {"active": PRICE_SOURCE, "connected": feed.ready.is_set(),
            "sources": [{"id": s, "name": n, "problem": source_problem(s)} for s, n in SOURCES.items()]}


# Datele trimise de pagină la schimbarea sursei: {"source": "mt5"} sau {"source": "ctrader"}.
class SourceIn(BaseModel):
    source: str


# Schimbă sursa prețurilor pentru tot serverul (toate paginile, alarmele, exporturile).
@app.post("/api/source")
async def set_source(s: SourceIn):
    if s.source not in SOURCES:
        raise HTTPException(400, f"Sursă necunoscută: {s.source}")
    if problem := source_problem(s.source):
        raise HTTPException(400, f"{SOURCES[s.source]}: {problem}")
    if s.source != PRICE_SOURCE:
        await use_source(s.source)
        # Paginile se reconectează și primesc istoricul de la noua sursă.
        hub.broadcast({"type": "source", "source": s.source, "name": SOURCE_NAME})
    # Așteptăm cel mult 15 s conectarea, ca răspunsul să spună dacă sursa e deja gata.
    try:
        await asyncio.wait_for(feed.ready.wait(), 15)
    except asyncio.TimeoutError:
        pass
    return {"active": PRICE_SOURCE, "connected": feed.ready.is_set()}


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
    # Toate schimbările de preț, în ordinea timpului: (timp, 0 = bid / 1 = ask, preț).
    events = sorted([(t, 0, p) for t, p in bid] + [(t, 1, p) for t, p in ask])
    b = a = None   # ultimul bid / ask cunoscut (None până la primul)
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
# informații despre simbol + istoricul de lumânări + ultimele ticuri, de la sursa `feed`.
# De la M1 în sus, lumânările închise vin din datastore-ul perechii `pair` (deja adus la zi), ca
# chartul și indicatorii să vadă exact aceleași date; doar lumânarea în curs vine de la sursă.
async def build_hello(feed, symbol: str, tf: int, pair: "Pair | None") -> dict:
    info = await feed.info(symbol)
    last = await feed.quote(symbol)
    # Istoricul se termină la ultimul tic (în weekend, la închiderea pieței de vineri).
    end = (last[0] if last else int(time.time() * 1000)) + 1
    if tf in PERIODS:
        # Lumânările închise din datastore + ce e mai nou de la sursă (lumânarea în curs).
        bars = await pair.store.load_bars(MAX_INITIAL_BARS)
        closed = bars[-1]["time"] if bars else 0
        bars += [b for b in await feed.trendbars(symbol, tf, 3, end) if b["time"] > closed]
        # Ticurile bid din ultima oră, pentru chartul de ticuri.
        bid = await feed.ticks(symbol, "BID", end - 3600_000, end, max_pages=1)
    else:
        # Sub un minut: le construim din ticurile bid ale ultimelor HISTORY_BARS intervale.
        bid = await feed.ticks(symbol, "BID", end - tf * HISTORY_BARS * 1000, end, max_pages=MAX_TICK_PAGES)
        bars = candles_from_ticks(bid, tf)
    # Ticurile ask din ultima oră, combinate apoi cu cele bid în [timp, bid, ask].
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
        "boot": BOOT_ID,
    }


# Lumânări mai vechi decât `before` (secunde UTC), cerute de pagină când chartul e derulat spre
# stânga: întâi din datastore (aceleași date ca indicatorii), iar dincolo de începutul lui, de la
# sursă. O listă goală înseamnă că sursa nu mai are istoric.
@app.get("/api/bars")
async def older_bars(symbol: str, tf: int, before: int):
    name = feed.resolve(symbol)
    if name is None or tf not in TIMEFRAMES:
        raise HTTPException(400, "Simbol sau timeframe necunoscut")
    end = before * 1000
    try:
        if tf in PERIODS:
            bars = await STORE.get(PRICE_SOURCE, name, tf).load_before(before, OLDER_BARS)
            # Am ajuns la începutul datastore-ului: continuăm direct de la sursă.
            if not bars:
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


# Lumânările Heikin-Ashi pentru lumânările trimise de pagină (afișarea „Heikin-Ashi” de pe chart),
# calculate cu heikin_ashi() din heikin_aishi.py. Pagina trimite toate lumânările încărcate, ca
# prima lumânare Heikin-Ashi să pornească mereu de la începutul istoricului de pe chart.
class BarsIn(BaseModel):
    bars: list[dict]   # [{time, open, high, low, close}, ...] în ordine cronologică


# Primește lumânările normale și întoarce lumânările Heikin-Ashi corespunzătoare.
@app.post("/api/heikin_ashi")
async def heikin_ashi_bars(b: BarsIn):
    if not b.bars:
        return {"bars": []}
    try:
        # fastframe rulează aceeași funcție pe un tabel rapid (sub o secundă pe 10.000 de
        # lumânări); într-un fir separat, ca ticurile să meargă mai departe și dacă se ajunge
        # la pandas (mult mai lent).
        return {"bars": await asyncio.to_thread(fastframe.run, heikin_ashi, b.bars)}
    except Exception as e:  # noqa: BLE001 - orice eroare din funcție ajunge în pagină
        raise HTTPException(500, f"heikin_ashi() din heikin_aishi.py: {type(e).__name__}: {e}") from e


# ---------- Datastore local și indicatori ----------
# Lumânările închise ale fiecărui simbol / timeframe (M1–W1) deschis într-o pagină se țin pe disc,
# în datastore/ (regulile sunt în datastore.py). Drumul live nu trece pe aici: ticurile merg doar
# prin memorie (sursă -> TickHub -> WebSocket -> chart). Drumul de analiză:
#   1. La deschiderea perechii, datastore-ul se aduce la zi (prima dată: ultimele 10.000 de
#      lumânări), cu progresul trimis paginii (mesajul "store"); abia apoi pagina primește hello-ul,
#      cu lumânările închise luate din datastore + lumânarea în curs de la sursă.
#   2. Cât timp perechea e deschisă, la închiderea fiecărei lumânări (+ CLOSE_DELAY secunde) serverul
#      cere lumânarea oficială și o adaugă (Pair.keep).
#   3. Indicatorii citesc doar din datastore (load_bars), într-un proces separat, cu rezultatul ținut
#      în cache; pagina trimite doar id-ul și parametrii indicatorului, iar rezultatul vine înapoi
#      prin mesajul "indicator", și din nou după fiecare lumânare închisă.
STORE = Datastore(DATASTORE_DIR)
# Câte secunde după închiderea unei lumânări o cerem de la sursă (sursa are nevoie de un moment
# ca s-o finalizeze).
CLOSE_DELAY = 3
# Procesele pentru calculul indicatorilor (create la pornire, în lifespan).
INDICATOR_WORKERS = 2
pool: ProcessPoolExecutor | None = None
# Rezultatele recente: (pereche, indicator, parametri, versiunea datelor) -> Future cu rezultatul.
# Aceleași date + aceiași parametri = același rezultat, deci un calcul se face o singură dată
# chiar dacă îl cer mai multe pagini sau butonul ↻.
IND_CACHE: dict[tuple, asyncio.Future] = {}
IND_CACHE_SIZE = 64


class Conn:
    """O pagină conectată: coada mesajelor perechii ei (datastore, indicatori) și indicatorii ceruți."""

    def __init__(self):
        # Coada mesajelor de „control” (progres datastore, rezultate indicatori), separată de
        # coada ticurilor; ws_endpoint le trimite pe amândouă paginii.
        self.ctrl: asyncio.Queue = asyncio.Queue(maxsize=500)
        self.indicators: dict[str, dict] = {}   # id -> parametri

    # Pune un mesaj în coadă (ca text JSON). Coadă plină = pagina nu mai citește: aruncăm cel
    # mai vechi mesaj, ca memoria să nu crească la nesfârșit.
    def send(self, msg: dict):
        if self.ctrl.full():
            self.ctrl.get_nowait()
        self.ctrl.put_nowait(json.dumps(msg))


class Pair:
    """O pereche sursă / simbol / timeframe deschisă în cel puțin o pagină."""

    def __init__(self, feed, source: str, name: str, tf: int):
        self.feed, self.source, self.name, self.tf = feed, source, name, tf
        self.store = STORE.get(source, name, tf)   # fișierul din datastore al perechii
        self.conns: set[Conn] = set()              # paginile care au perechea deschisă
        self.digits = 5
        self.keeper: asyncio.Task | None = None    # sarcina care ține datastore-ul la zi (keep)
        self._sync: asyncio.Task | None = None     # sincronizarea în curs (dacă există)

    # Numele perechii în mesajele din consolă, ex. „ctrader/EURUSD/M1”.
    @property
    def label(self) -> str:
        return f"{self.source}/{self.name}/{TF_LABELS[self.tf]}"

    # Trimite un mesaj tuturor paginilor perechii.
    def broadcast(self, msg: dict):
        for c in list(self.conns):
            c.send(msg)

    # Progresul / starea datastore-ului, către toate paginile perechii.
    def report(self, status: dict):
        self.broadcast({"type": "store", "symbol": self.name, "tf": self.tf, **status})

    # O singură sincronizare odată: cine o cere în timp ce rulează o așteaptă pe aceeași. Nu se
    # oprește dacă pagina care a pornit-o se închide (descărcarea rămâne utilă).
    async def sync(self) -> bool:
        if self._sync is None or self._sync.done():
            self._sync = asyncio.create_task(self.store.sync(self.feed, self.digits, self.report))
            self._sync.add_done_callback(lambda t: t.cancelled() or t.exception())
        return await asyncio.shield(self._sync)

    # Pornește keep() în fundal, dacă nu rulează deja.
    def start_keeper(self):
        if self.keeper is None or self.keeper.done():
            self.keeper = asyncio.create_task(self.keep())

    # Ține datastore-ul la zi: se trezește la închiderea fiecărei lumânări (după grila lumânărilor
    # salvate) + CLOSE_DELAY și cere lumânarea oficială. Cel mult o oră între verificări (D1 / W1
    # se mută cu o oră la trecerea pe ora de vară / iarnă). Dacă s-a adăugat ceva, recalculează
    # indicatorii paginilor.
    async def keep(self):
        while True:
            last = self.store.last_time()
            now = time.time()
            # Ora la care se închide lumânarea în curs: ultima salvată + un număr întreg de
            # timeframe-uri, prima după „acum”. Fără lumânări salvate: peste un minut.
            wake = now + 60 if last is None else last + (int((now - last) // self.tf) + 1) * self.tf
            await asyncio.sleep(max(1.0, min(wake, now + 3600) - now + CLOSE_DELAY))
            try:
                changed = await self.sync()
            except Exception as e:  # noqa: BLE001 - reîncercăm la următoarea închidere
                print(f"[DATASTORE] {self.label}: actualizare eșuată: {type(e).__name__}: {e}")
                continue
            if changed:
                for c in list(self.conns):
                    for ind_id, params in list(c.indicators.items()):
                        await send_indicator(self, c, ind_id, params)


# Perechile deschise acum: (sursă, simbol, timeframe) -> Pair.
PAIRS: dict[tuple[str, str, int], Pair] = {}


# O pagină deschide perechea: o creăm dacă e prima pagină, altfel o refolosim, și adăugăm pagina.
def join_pair(feed, source: str, name: str, tf: int, conn: Conn, digits: int) -> Pair:
    key = (source, name, tf)
    pair = PAIRS.get(key)
    if pair is None:
        pair = PAIRS[key] = Pair(feed, source, name, tf)
    pair.feed, pair.digits = feed, digits   # după o reconectare, sursa e un obiect nou
    pair.conns.add(conn)
    return pair


# Ultima pagină a perechii s-a închis: oprim actualizarea și eliberăm memoria (fișierul rămâne;
# la redeschidere, sincronizarea completează golul).
def leave_pair(pair: Pair, conn: Conn):
    pair.conns.discard(conn)
    # Mai sunt pagini pe pereche (sau perechea a fost deja înlocuită): nu oprim nimic.
    if pair.conns or PAIRS.get((pair.source, pair.name, pair.tf)) is not pair:
        return
    del PAIRS[(pair.source, pair.name, pair.tf)]
    if pair.keeper:
        pair.keeper.cancel()
    task = asyncio.create_task(pair.store.unload())
    STORE_TASKS.add(task)
    task.add_done_callback(STORE_TASKS.discard)


STORE_TASKS: set[asyncio.Task] = set()   # referințe la sarcinile de fundal (altfel pot fi oprite)


# Procesele de calcul, create la prima nevoie (sau din nou, după ce au murit: pool = None).
def get_pool() -> ProcessPoolExecutor:
    global pool
    if pool is None:
        pool = ProcessPoolExecutor(max_workers=INDICATOR_WORKERS)
    return pool


# Calculează un indicator pe ultimele lumânări închise din datastore (load_bars), în procesul
# separat, cu cache.
# Un indicator poate fi pe chart de mai multe ori (ex. medii mobile cu perioade diferite): pagina
# trimite id-ul copiei, „sma#2”; tipul indicatorului e partea dinainte de „#”.
def ind_type(ind_id: str) -> str:
    return ind_id.split("#", 1)[0]


# Calculul propriu-zis: citește lumânările din datastore, caută rezultatul în cache și, dacă nu
# e acolo, îl calculează în procesul separat (calc.compute din indicators.py).
async def compute_indicator(pair: Pair, ind_id: str, params: dict) -> dict:
    global pool
    ind_id = ind_type(ind_id)   # copiile cu aceiași parametri împart calculul și cache-ul
    spec = calc.SPECS[ind_id]
    n = spec["bars"](params)    # câte lumânări cere indicatorul cu acești parametri
    bars = await pair.store.load_bars(n)
    if len(bars) < spec["min"]:
        raise ValueError(f"datastore-ul are doar {len(bars)} lumânări (minimum {spec['min']})")
    try:
        code_v = calc.ANALYSIS.stat().st_mtime_ns   # analysis.py modificat = rezultat nou
    except OSError:
        code_v = 0
    # Cheia cache-ului: orice schimbare (date, parametri, cod) dă o cheie nouă = calcul nou.
    key = (pair.source, pair.name, pair.tf, ind_id, n, json.dumps(params, sort_keys=True),
           pair.store.version, bars[-1]["time"], len(bars), code_v)
    fut = IND_CACHE.get(key)
    if fut is None:
        try:
            fut = asyncio.get_running_loop().run_in_executor(get_pool(), calc.compute, ind_id, bars, params)
        except BrokenProcessPool:
            pool = None   # un proces de calcul a murit: pornim altele
            fut = asyncio.get_running_loop().run_in_executor(get_pool(), calc.compute, ind_id, bars, params)
        IND_CACHE[key] = fut
        # Cache plin: scoatem cele mai vechi intrări (dicționarul păstrează ordinea adăugării).
        while len(IND_CACHE) > IND_CACHE_SIZE:
            IND_CACHE.pop(next(iter(IND_CACHE)))
    try:
        # shield: dacă pagina care așteaptă se închide, calculul continuă pentru celelalte.
        result = await asyncio.shield(fut)
    except BaseException:
        if fut.done():
            IND_CACHE.pop(key, None)   # nu păstrăm erorile în cache
        raise
    return {**result, "bars": len(bars), "requested": n, "last": bars[-1]["time"]}


# Calculează și trimite paginii rezultatul unui indicator (sau eroarea lui).
async def send_indicator(pair: Pair, conn: Conn, ind_id: str, params: dict):
    msg = {"type": "indicator", "id": ind_id, "symbol": pair.name, "tf": pair.tf}
    try:
        msg["result"] = await compute_indicator(pair, ind_id, params)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 - eroarea apare în pagină
        where = "analysis.py" if ind_type(ind_id) in ("trend", "sma") else ind_id
        msg["error"] = f"Eroare în {where}: {type(e).__name__}: {e}"
    # Pagina poate să fi detașat indicatorul între timp.
    if conn.indicators.get(ind_id) == params:
        conn.send(msg)


# ---------- Export CSV ----------
# Un export poate dura (un an de M1 sau de ticuri), deci rulează în fundal: pagina pornește
# exportul, apoi întreabă periodic cât s-a făcut. Aici sunt exporturile din această sesiune.
EXPORTS: dict[str, dict] = {}


# Datele trimise de fereastra de export.
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
    # Starea exportului: run_export o actualizează pe măsură ce lucrează, pagina o citește.
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
                to = min(t + step, end)   # bucata curentă: [t, to)
                if tf in PERIODS:
                    # M1 și peste: lumânările gata calculate de sursă.
                    rows = bar_rows(await feed.bars_range(name, tf, t, to))
                else:
                    # Ticuri sau lumânări sub un minut: pornim de la ticurile bid ale bucății.
                    bid = await feed.ticks(name, "BID", t, to - 1, max_pages=100_000)
                    bid = [x for x in bid if t <= x[0] < to]
                    if tf == 0:
                        ask = await feed.ticks(name, "ASK", t, to - 1, max_pages=100_000)
                        ask = [x for x in ask if t <= x[0] < to]
                        rows = await asyncio.to_thread(tick_rows, bid, ask, last, price)
                    else:
                        rows = bar_rows(await asyncio.to_thread(candles_from_ticks, bid, tf, None))
                # Scrierea pe disc într-un fir separat, ca serverul să nu se blocheze.
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


# ---------- Trend multi-timeframe ----------
# Indicatorul nu folosește lumânările chartului și nici prețurile live: citește doar datastore-ul D1
# al simbolului (datastore/<sursă>/<SIMBOL>/D1/), pe orice timeframe al chartului. Pentru fiecare
# sursă / simbol cu indicatorul atașat într-o pagină există un MtfWatch, care:
#   - aduce datastore-ul D1 la zi după regulile din datastore.py (îl creează dacă nu există, îl
#     actualizează dacă e învechit) și rulează analysis.py pe zilele anului curent: la atașare, la
#     schimbarea simbolului (pagina se reconectează) și la închiderea fiecărei zile. Procentele se
#     recalculează doar după o actualizare, nu la fiecare tic;
#   - face asta o singură dată odată, oricâte pagini ar avea indicatorul pe același simbol.
D1 = 86400
# Aceleași zile + același analysis.py = același rezultat.
MTF_CACHE: dict[tuple, asyncio.Future] = {}


# Rulează run_mtf (indicators.py) în procesul separat, cu cache (la fel ca compute_indicator).
# key = (sursă, simbol, nr. lumânări, ultima lumânare, nr. zile); i se adaugă data lui analysis.py.
async def mtf_compute(key: tuple, rows: list[list]) -> dict:
    global pool
    try:
        key = (*key, calc.ANALYSIS.stat().st_mtime_ns)
    except OSError:
        pass
    fut = MTF_CACHE.get(key)
    if fut is None:
        loop = asyncio.get_running_loop()
        try:
            fut = loop.run_in_executor(get_pool(), calc.run_mtf, rows)
        except BrokenProcessPool:
            pool = None
            fut = loop.run_in_executor(get_pool(), calc.run_mtf, rows)
        MTF_CACHE[key] = fut
        while len(MTF_CACHE) > IND_CACHE_SIZE:
            MTF_CACHE.pop(next(iter(MTF_CACHE)))
    try:
        return await asyncio.shield(fut)
    except BaseException:
        if fut.done():
            MTF_CACHE.pop(key, None)
        raise


class MtfWatch:
    """Indicatorul „Trend multi-timeframe” pentru o sursă / simbol, cu paginile care îl afișează."""

    def __init__(self, feed, source: str, name: str):
        self.feed, self.source, self.name = feed, source, name
        self.store = STORE.get(source, name, D1)   # același obiect ca al chartului pe D1
        self.conns: set[Conn] = set()
        self.lock = asyncio.Lock()
        self.keeper: asyncio.Task | None = None
        self.last: dict | None = None   # ultimul mesaj trimis paginilor
        self.at = 0.0                   # când (time.monotonic) s-a făcut ultima actualizare
        self.last_bar: int | None = None   # ora ultimei lumânări D1 din datastore

    # Trimite un mesaj tuturor paginilor care afișează indicatorul pe acest simbol.
    def broadcast(self, msg: dict):
        for c in list(self.conns):
            c.send(msg)

    # Pasul curent al actualizării (1 = caut fișierul, 2 = sincronizez, 3 = rulez analysis.py),
    # afișat în caseta indicatorului cât timp lucrăm.
    def progress(self, step: int, text: str):
        self.broadcast({"type": "indicator", "id": "mtf", "symbol": self.name,
                        "progress": {"step": step, "text": text}})

    # Progresul sincronizării datastore-ului, ca text pentru pagină.
    def store_progress(self, st: dict):
        if st.get("state") != "syncing":
            return
        what = {"download": "descarc lumânările D1 (prima dată)", "rebuild": "refac fișierul D1",
                "history": "completez istoricul D1", "update": "adaug zilele lipsă"}.get(st.get("phase"), "actualizez")
        self.progress(2, f"{what}: {st.get('progress', 0) * 100:.0f}% · {st.get('bars', 0)} zile")

    # Aduce datastore-ul D1 la zi și rulează analysis.py. O actualizare cerută în timp ce alta
    # rulează o așteaptă și primește același rezultat (dacă e mai nou de 10 s). Întoarce
    # (mesaj, nou). Nu se oprește dacă pagina care a cerut-o se închide (descărcarea rămâne utilă).
    async def refresh(self, force: bool = False) -> tuple[dict, bool]:
        task = asyncio.create_task(self._refresh(force))
        STORE_TASKS.add(task)
        task.add_done_callback(STORE_TASKS.discard)
        return await asyncio.shield(task)

    # Actualizarea propriu-zisă (sub lacăt, deci una singură odată).
    async def _refresh(self, force: bool) -> tuple[dict, bool]:
        async with self.lock:
            # Avem un rezultat de acum mai puțin de 10 s: îl refolosim.
            if not force and self.last is not None and time.monotonic() - self.at < 10:
                return self.last, False
            msg = {"type": "indicator", "id": "mtf", "symbol": self.name}
            store = self.store
            rel = store.path.relative_to(ROOT).as_posix()
            try:
                digits = (await self.feed.info(self.name))["digits"]
                self.progress(1, f"caut {rel}")
                existed = await asyncio.to_thread(store.path.exists)
                self.progress(2, "verific ultimele zile și adaug ce lipsește…" if existed
                              else "nu există: îl creez (ultimele 10.000 de zile închise)…")
                await store.sync(self.feed, digits, self.store_progress)
                added = store._added   # zile adăugate la această sincronizare
                bars = await store.load_bars()
                # Fără o pagină cu chartul D1 deschis, nu ținem lumânările în memorie.
                if (self.source, self.name, D1) not in PAIRS:
                    await store.unload()
                if not bars:
                    raise ValueError("datastore-ul D1 e gol")
                self.last_bar = bars[-1]["time"]
                # Anul ultimei zile salvate și zilele lui, în forma citită de analysis.py.
                year = mtf.day_of(self.feed, self.last_bar).year
                rows = mtf.year_rows(self.feed, bars, year)
                if not rows:
                    raise ValueError(f"nicio zi închisă în {year} în datastore")
                self.progress(3, "rulez analysis.py (neschimbat, doar sursa datelor din read_csv)…")
                result = await mtf_compute((self.source, self.name, len(bars), self.last_bar, len(rows)), rows)
                # Rezultatul + detaliile despre baza de date, afișate în casetele indicatorului.
                msg["result"] = {**result, "digits": digits, "source": SOURCES.get(self.source, self.source),
                                 "file": rel, "updated": int(time.time() * 1000),
                                 "db": {"created": not existed, "added": added,
                                        "total": len(bars), "days": len(rows),
                                        "first": rows[0][0][:10], "last": rows[-1][0][:10],
                                        "first_bar": store.meta.get("first_bar", ""),
                                        "complete": bool(store.meta.get("history_complete"))}}
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - eroarea apare în pagină
                msg["error"] = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
                print(f"[MTF] {self.source}/{self.name}: {msg['error']}")
            self.last, self.at = msg, time.monotonic()
            self.broadcast(msg)
            return msg, True

    # Se trezește la închiderea fiecărei zile (după grila lumânărilor D1 salvate) + CLOSE_DELAY, ca
    # Pair.keep; cel mult o oră între verificări (ziua se mută cu o oră la ora de vară / iarnă).
    async def keep(self):
        while True:
            last, now = self.last_bar, time.time()
            wake = now + 3600 if last is None else last + (int((now - last) // D1) + 1) * D1
            await asyncio.sleep(max(1.0, min(wake, now + 3600) - now + CLOSE_DELAY))
            await self.refresh(force=True)


# Indicatorii „Trend multi-timeframe” activi: (sursă, simbol) -> MtfWatch.
MTF: dict[tuple[str, str], MtfWatch] = {}


# O pagină atașează indicatorul: creăm (sau refolosim) MtfWatch-ul simbolului, pornim
# actualizarea zilnică și trimitem paginii rezultatul curent.
async def mtf_watch(feed, source: str, name: str, conn: Conn):
    w = MTF.get((source, name))
    if w is None:
        w = MTF[(source, name)] = MtfWatch(feed, source, name)
    w.feed = feed
    w.conns.add(conn)
    if w.keeper is None or w.keeper.done():
        w.keeper = asyncio.create_task(w.keep())
    msg, fresh = await w.refresh()
    # Un rezultat nou a fost deja trimis tuturor (broadcast); unul refolosit îl trimitem doar ei.
    if not fresh and conn in w.conns:
        conn.send(msg)


# Pagina a detașat indicatorul (sau s-a închis); ultima pagină oprește actualizarea zilnică.
def mtf_unwatch(source: str, name: str, conn: Conn):
    w = MTF.get((source, name))
    if w is None:
        return
    w.conns.discard(conn)
    if not w.conns:
        del MTF[(source, name)]
        if w.keeper:
            w.keeper.cancel()


# Conexiunea WebSocket a unei pagini. Adresa e de forma /ws?symbol=EURUSD&tf=1
# (FastAPI preia automat symbol și tf din adresă).
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket, symbol: str = "EURUSD", tf: int = 1):
    await ws.accept()
    if tf not in TIMEFRAMES:
        tf = 1

    # Trimite o eroare paginii și închide. retry=True: pagina reîncearcă singură mai târziu
    # (ex. sursa de prețuri temporar indisponibilă); fără retry: eroare definitivă (simbol inexistent).
    # `extra` = câmpuri în plus pentru pagină (ex. code="unknown_symbol").
    async def fail(msg: str, retry: bool, **extra):
        try:
            await ws.send_text(json.dumps({"type": "error", "msg": msg, "retry": retry, **extra}))
            await ws.close()
        except (WebSocketDisconnect, RuntimeError):
            pass   # pagina s-a închis între timp

    # Sursa activă la conectare; dacă se schimbă între timp, pagina se reconectează (mesajul "source").
    src, source = feed, PRICE_SOURCE
    try:
        await asyncio.wait_for(src.ready.wait(), 10)
    except asyncio.TimeoutError:
        await fail(f"{SOURCES[source]} indisponibil", retry=True)
        return
    name = src.resolve(symbol)
    if name is None:
        # Simbolul nu există la această sursă (ex. după schimbarea sursei): pagina anunță
        # utilizatorul și deschide meniul de simboluri.
        await fail(f"{symbol} nu există la {SOURCES[source]}", retry=False,
                   code="unknown_symbol", symbol=symbol, source=SOURCES[source])
        return

    # Ne abonăm la ticurile simbolului ÎNAINTE de a cere istoricul, ca să nu pierdem ticurile
    # sosite între timp; primim o coadă proprie. `conn` primește mesajele perechii (progresul
    # datastore-ului, rezultatele indicatorilor).
    q = hub.subscribe(name)
    conn = Conn()
    pair: Pair | None = None
    jobs: set[asyncio.Task] = set()   # calculele de indicatori cerute de pagină
    receiver = getter = ctrl_getter = None

    # Mesajele paginii: {"type": "indicator", "id": "trend" (sau o copie, „sma#2”), "params": {...}} = calculează și
    # trimite rezultatul (și din nou la fiecare lumânare închisă); {"type": "indicator_off", "id"}.
    def on_client(text: str):
        try:
            data = json.loads(text)
        except ValueError:
            return
        if not isinstance(data, dict):
            return
        ind_id = data.get("id")
        if not isinstance(ind_id, str) or len(ind_id) > 40:
            return
        if data.get("type") == "indicator_off":
            conn.indicators.pop(ind_id, None)
            if ind_id == "mtf":
                mtf_unwatch(source, name, conn)
        elif data.get("type") == "indicator" and ind_id == "mtf":
            # Pe orice timeframe: indicatorul citește baza de date D1, nu datastore-ul chartului.
            job = asyncio.create_task(mtf_watch(src, source, name, conn))
            jobs.add(job)
            job.add_done_callback(jobs.discard)
        elif data.get("type") == "indicator" and ind_type(ind_id) in calc.SPECS:
            params = data.get("params") if isinstance(data.get("params"), dict) else {}
            if pair is None:
                conn.send({"type": "indicator", "id": ind_id, "symbol": name, "tf": tf,
                           "error": "Indicatorii nu rulează pe 1s–30s (aceste lumânări nu se salvează "
                                    "în datastore). Alege M1 sau un timeframe mai mare."})
                return
            conn.indicators[ind_id] = params
            job = asyncio.create_task(send_indicator(pair, conn, ind_id, params))
            jobs.add(job)
            job.add_done_callback(jobs.discard)

    try:
        # Detectează închiderea paginii (și primește mesajele ei).
        receiver = asyncio.create_task(ws.receive_text())
        started = time.monotonic()
        if tf in PERIODS:
            # Datastore-ul perechii se aduce la zi înainte de hello: chartul primește lumânările
            # închise din el. Între timp pagina primește progresul (mesajele "store").
            try:
                info = await asyncio.wait_for(src.info(name), HELLO_TIMEOUT)
            except Exception as e:  # noqa: BLE001
                print(f"[WEB] {SOURCES[source]} {name}: {str(e) or type(e).__name__}")
                await fail(f"{SOURCES[source]} indisponibil", retry=True)
                return
            pair = join_pair(src, source, name, tf, conn, info["digits"])
            if pair.store.status.get("state") == "syncing":
                conn.send({"type": "store", "symbol": name, "tf": tf, **pair.store.status})
            sync = asyncio.ensure_future(pair.sync())
            # Cât durează sincronizarea: trimitem paginii progresul și citim mesajele ei.
            while not sync.done():
                ctrl_getter = asyncio.create_task(conn.ctrl.get())
                done, _ = await asyncio.wait({sync, ctrl_getter, receiver}, return_when=asyncio.FIRST_COMPLETED)
                if ctrl_getter in done:
                    await ws.send_text(ctrl_getter.result())
                else:
                    ctrl_getter.cancel()
                if receiver in done:
                    on_client(receiver.result())   # ridică WebSocketDisconnect dacă s-a închis
                    receiver = asyncio.create_task(ws.receive_text())
            try:
                sync.result()
            except Exception as e:  # noqa: BLE001 - orice eroare ajunge ca mesaj în pagină
                reason = str(e) or type(e).__name__
                print(f"[DATASTORE] {pair.label}: {reason}")
                while not conn.ctrl.empty():
                    await ws.send_text(conn.ctrl.get_nowait())
                await fail(f"datastore {TF_LABELS[tf]}: {reason}", retry=True)
                return
        try:
            # Limită de timp: dacă sursa nu răspunde, pagina primește o eroare și reîncearcă,
            # în loc să aștepte la nesfârșit.
            hello = await asyncio.wait_for(build_hello(src, name, tf, pair), HELLO_TIMEOUT)
        except Exception as e:  # noqa: BLE001 - orice eroare a sursei ajunge ca mesaj în pagină
            reason = str(e) or type(e).__name__
            if isinstance(e, asyncio.TimeoutError):
                reason = f"fără răspuns în {HELLO_TIMEOUT}s"
            print(f"[WEB] istoric {SOURCES[source]} {name} {TF_LABELS[tf]}: {reason}")
            await fail(f"{SOURCES[source]} indisponibil", retry=True)
            return
        elapsed = time.monotonic() - started
        if elapsed > 5:
            print(f"[WEB] istoric {SOURCES[source]} {name} {TF_LABELS[tf]}: încărcat lent ({elapsed:.1f}s)")
        hello["source"] = source
        # Starea datastore-ului (pentru indicatorul de pe chart); sub un minut nu se salvează.
        hello["store"] = dict(pair.store.status) if pair else {"state": "off"}
        # Mesajele de progres rămase sunt mai vechi decât starea din hello.
        while not conn.ctrl.empty():
            conn.ctrl.get_nowait()
        await ws.send_text(json.dumps(hello))
        if pair:
            pair.start_keeper()
        while True:
            # Așteptăm, în paralel: ticuri / alarme (q), mesaje ale perechii (conn.ctrl) sau un
            # mesaj / închiderea paginii (receiver).
            getter = asyncio.create_task(q.get())
            ctrl_getter = asyncio.create_task(conn.ctrl.get())
            done, _ = await asyncio.wait({getter, ctrl_getter, receiver}, return_when=asyncio.FIRST_COMPLETED)
            # Anulăm imediat (fără await între) cozile care n-au dat nimic: mesajul rămâne în coadă.
            for t in (getter, ctrl_getter):
                if t not in done:
                    t.cancel()
            for t in (getter, ctrl_getter):
                if t in done:
                    await ws.send_text(t.result())
            if receiver in done:
                on_client(receiver.result())   # ridică WebSocketDisconnect dacă s-a închis
                receiver = asyncio.create_task(ws.receive_text())
    except (WebSocketDisconnect, RuntimeError):
        # Pagina s-a închis sau conexiunea a căzut – situație normală, nu o eroare.
        pass
    finally:
        # Indiferent cum s-a terminat, eliberăm coada și oprim sarcinile de așteptare rămase.
        # (Fără asta, Python afișează în consolă „Task exception was never retrieved” când
        #  pagina se închide exact în timp ce îi trimitem un mesaj.)
        for t in (receiver, getter, ctrl_getter, *jobs):
            if t is not None:
                t.cancel()
                t.add_done_callback(lambda t: t.cancelled() or t.exception())
        hub.unsubscribe(name, q)
        mtf_unwatch(source, name, conn)
        if pair:
            leave_pair(pair, conn)


# Punctul de pornire când rulezi `python server.py`.
# log_level="warning": nu afișăm fiecare cerere HTTP, doar avertismentele și erorile.
if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
