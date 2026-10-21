"""
Client pentru cTrader Open API: prețuri live, istoric de ticuri și lumânări, prin internet.

Nu are nevoie de niciun program de trading deschis pe PC: ne conectăm direct la serverele
cTrader (Spotware), cu datele aplicației (din .env) și cu un token de acces (ctrader_token.json,
creat de ctrader_setup.py).
"""
# =====================================================================================
#  CE FACE ACEST FIȘIER (pe scurt)
# -------------------------------------------------------------------------------------
#  - Connection : O conexiune WebSocket cu serverul cTrader. Trimite cereri JSON și
#                 potrivește fiecare răspuns cu cererea lui (după câmpul clientMsgId).
#  - TokenStore : Tokenul de acces la cont, salvat în ctrader_token.json. Se reînnoiește
#                 singur înainte să expire (tokenul e valabil ~30 de zile).
#  - CTraderFeed: Ține conexiunea deschisă permanent (cu reconectare automată), se abonează
#                 la prețurile simbolurilor cerute și oferă istoricul (ticuri, lumânări).
#
#  Protocolul (pe scurt):
#   - Fiecare mesaj e un JSON: {"clientMsgId": "...", "payloadType": 2100, "payload": {...}}.
#     payloadType = tipul mesajului (numerele vin din fișierele .proto oficiale ale cTrader).
#   - Ordinea la conectare: autorizăm APLICAȚIA (clientId + clientSecret), apoi CONTUL
#     (ctidTraderAccountId + accessToken). Abia apoi putem cere prețuri.
#   - Prețurile vin ca numere întregi în 1/100000 din unitate: 108623 înseamnă 1.08623.
#   - Trebuie să trimitem un „heartbeat” cel puțin o dată la 30 de secunde, altfel serverul
#     închide conexiunea. Noi îl trimitem la 10 secunde.
#   - Limite: maximum 50 de cereri/s, dintre care cel mult 5/s cereri de istoric.
# =====================================================================================

# asyncio   : toată comunicarea e asincronă, în bucla serverului web (fără fire separate).
# itertools : count() generează numere 1, 2, 3... pentru identificatorii cererilor.
# json      : mesajele cTrader sunt text JSON.
# time      : ora curentă (expirarea tokenului, ritmul cererilor de istoric).
import asyncio
import itertools
import json
import time
from pathlib import Path

# websockets: biblioteca cu care deschidem conexiunea WebSocket către cTrader.
import websockets

# Adresele serverelor: conturile demo și cele reale sunt pe servere diferite.
# Portul 5036 = varianta JSON a protocolului (5035 ar fi varianta binară, Protobuf).
HOSTS = {False: "wss://demo.ctraderapi.com:5036", True: "wss://live.ctraderapi.com:5036"}

# Prețurile vin în 1/100000 din unitate.
PRICE_SCALE = 100_000

# --- Tipurile de mesaje (payloadType), din OpenApiModelMessages.proto ---
ERROR_RES = 50
HEARTBEAT = 51
APP_AUTH_REQ = 2100
ACCOUNT_AUTH_REQ = 2102
SYMBOLS_LIST_REQ = 2114
ASSET_CLASS_LIST_REQ = 2153
SYMBOL_CATEGORY_REQ = 2160
SYMBOL_BY_ID_REQ = 2116
SYMBOL_CHANGED_EVENT = 2120
SUBSCRIBE_SPOTS_REQ = 2127
UNSUBSCRIBE_SPOTS_REQ = 2129
SPOT_EVENT = 2131
GET_TRENDBARS_REQ = 2137
OA_ERROR_RES = 2142
GET_TICKDATA_REQ = 2145
TOKEN_INVALIDATED_EVENT = 2147
CLIENT_DISCONNECT_EVENT = 2148
GET_ACCOUNTS_REQ = 2149
ACCOUNT_DISCONNECT_EVENT = 2164
REFRESH_TOKEN_REQ = 2173

# Cererile de istoric au limita lor separată (5/s); le distanțăm la 0,25 s (4/s), cu rezervă.
HISTORICAL = {GET_TRENDBARS_REQ, GET_TICKDATA_REQ}
HIST_INTERVAL = 0.25

# Timeframe-urile pentru care cTrader are lumânări gata calculate (secunde -> numele perioadei).
PERIODS = {60: "M1", 300: "M5", 900: "M15", 1800: "M30", 3600: "H1", 14400: "H4", 86400: "D1",
           604800: "W1"}
# Istoricul de lumânări se cere în bucăți de cel mult atâtea lumânări (ca durată).
BARS_CHUNK = 2000

# Reînnoim tokenul când mai are mai puțin de 3 zile de valabilitate.
REFRESH_MARGIN = 3 * 86400


# O eroare raportată de serverul cTrader (mesajul ProtoOAErrorRes / ProtoErrorRes).
# code = numele erorii (ex. "SYMBOL_NOT_FOUND"), description = explicația serverului.
class CTraderError(Exception):
    def __init__(self, code: str, description: str = "", retry_after: float | None = None):
        super().__init__(f"{code}: {description}" if description else code)
        self.code = code
        self.description = description
        self.retry_after = retry_after


# O problemă de autorizare care nu se rezolvă singură (date greșite în .env, token revocat):
# reîncercăm rar și afișăm ce trebuie făcut.
class AuthError(Exception):
    pass


# =====================================================================================
#  Connection – o singură conexiune WebSocket
# =====================================================================================
class Connection:
    """Trimite cereri și primește răspunsuri/evenimente pe o conexiune WebSocket cTrader."""

    def __init__(self, live: bool, on_event=None):
        self.url = HOSTS[live]
        # on_event(payloadType, payload): apelată pentru mesajele care NU sunt răspunsuri la o
        # cerere (prețuri noi, deconectări anunțate de server).
        self.on_event = on_event
        self.ws = None
        # pending: pentru fiecare cerere în așteptare, „viitorul” (Future) în care punem răspunsul.
        self.pending: dict[str, asyncio.Future] = {}
        self.ids = itertools.count(1)
        self._reader: asyncio.Task | None = None
        self._heartbeat: asyncio.Task | None = None
        # Ritmul cererilor de istoric (vezi HIST_INTERVAL).
        self._hist_lock = asyncio.Lock()
        self._hist_next = 0.0

    # Deschide conexiunea și pornește în fundal cititorul de mesaje și heartbeat-ul.
    async def open(self):
        # max_size: lista de simboluri poate avea câțiva MB; limita implicită (1 MB) e prea mică.
        self.ws = await websockets.connect(self.url, max_size=64 * 2**20, open_timeout=15)
        self._reader = asyncio.create_task(self._read())
        self._heartbeat = asyncio.create_task(self._beat())

    # Închide conexiunea: oprește heartbeat-ul, închide WebSocket-ul și așteaptă cititorul să termine.
    async def close(self):
        if self._heartbeat:
            self._heartbeat.cancel()
        if self.ws:
            await self.ws.close()
        if self._reader:
            await asyncio.gather(self._reader, return_exceptions=True)

    # Se termină când conexiunea se închide (din orice motiv).
    async def wait_closed(self):
        await asyncio.shield(self._reader)

    # Motivul închiderii, dacă serverul a trimis unul (ex. „Bye” la date de aplicație greșite).
    @property
    def close_reason(self) -> str:
        return getattr(self.ws, "close_reason", "") or ""

    # Trimite periodic heartbeat, ca serverul să nu închidă conexiunea.
    async def _beat(self):
        try:
            while True:
                await asyncio.sleep(10)
                await self.ws.send(json.dumps({"payloadType": HEARTBEAT, "payload": {}}))
        except (websockets.ConnectionClosed, asyncio.CancelledError):
            pass

    # Citește mesajele primite până la închiderea conexiunii.
    async def _read(self):
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                pt = msg.get("payloadType")
                payload = msg.get("payload") or {}
                # E răspunsul la o cerere de-a noastră? (are același clientMsgId)
                fut = self.pending.pop(msg.get("clientMsgId"), None)
                if fut is not None:
                    if fut.done():
                        continue
                    if pt in (OA_ERROR_RES, ERROR_RES):
                        fut.set_exception(CTraderError(
                            payload.get("errorCode", "UNKNOWN_ERROR"), payload.get("description", ""),
                            payload.get("retryAfter")))
                    else:
                        fut.set_result(payload)
                elif pt in (OA_ERROR_RES, ERROR_RES):
                    # Eroare fără clientMsgId (ex. o cerere pe care serverul n-a putut-o citi).
                    print(f"[cTrader] eroare: {payload.get('errorCode')} {payload.get('description', '')}")
                elif pt != HEARTBEAT and self.on_event:
                    self.on_event(pt, payload)
        except websockets.ConnectionClosed:
            pass
        finally:
            # Cererile rămase fără răspuns primesc o eroare, ca să nu aștepte la nesfârșit.
            for fut in self.pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError("conexiunea cu cTrader s-a închis"))
            self.pending.clear()

    # Trimite o cerere și așteaptă răspunsul (payload-ul lui). Aruncă CTraderError dacă serverul
    # răspunde cu eroare și ConnectionError dacă se închide conexiunea între timp.
    async def request(self, pt: int, payload: dict, timeout: float = 20) -> dict:
        for attempt in range(3):
            try:
                return await self._request_once(pt, payload, timeout)
            except CTraderError as e:
                # Am depășit limita de cereri: așteptăm cât ne spune serverul și reîncercăm.
                if e.code not in ("REQUEST_FREQUENCY_EXCEEDED", "BLOCKED_PAYLOAD_TYPE") or attempt == 2:
                    raise
                await asyncio.sleep(float(e.retry_after or 1))
        raise AssertionError("unreachable")

    # O singură încercare: trimite cererea cu un clientMsgId nou și așteaptă răspunsul cu același id
    # (îl pune _read() în Future-ul din pending).
    async def _request_once(self, pt: int, payload: dict, timeout: float) -> dict:
        # Cererile de istoric așteaptă rândul, ca să nu depășim 5 cereri/s (vezi HIST_INTERVAL).
        if pt in HISTORICAL:
            async with self._hist_lock:
                wait = self._hist_next - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                self._hist_next = time.monotonic() + HIST_INTERVAL
        cid = f"m{next(self.ids)}"
        fut = asyncio.get_running_loop().create_future()
        self.pending[cid] = fut
        try:
            try:
                await self.ws.send(json.dumps({"clientMsgId": cid, "payloadType": pt, "payload": payload}))
            except websockets.ConnectionClosed as e:
                raise ConnectionError("conexiunea cu cTrader s-a închis") from e
            return await asyncio.wait_for(fut, timeout)
        finally:
            self.pending.pop(cid, None)


# =====================================================================================
#  TokenStore – tokenul de acces la contul cTrader (ctrader_token.json)
# =====================================================================================
class TokenStore:
    """accessToken + refreshToken + contul ales, salvate pe disc (se schimbă la reînnoire)."""

    def __init__(self, path: Path):
        self.path = path
        self.data: dict = {}
        if path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))

    # True dacă ctrader_setup.py a fost rulat (avem token și cont ales).
    @property
    def configured(self) -> bool:
        return bool(self.data.get("accessToken") and self.data.get("accountId"))

    # Id-ul contului cTrader ales la configurare.
    @property
    def account_id(self) -> int:
        return int(self.data["accountId"])

    # True = cont real (server live), False = cont demo.
    @property
    def is_live(self) -> bool:
        return bool(self.data.get("isLive"))

    # True dacă tokenul expiră în mai puțin de REFRESH_MARGIN (3 zile) - e timpul să-l reînnoim.
    def expires_soon(self) -> bool:
        return self.data.get("expiresAt", 0) - time.time() < REFRESH_MARGIN

    # Salvează tokenurile noi (după reînnoire). La fel ca la alerts.json: scriem într-un fișier
    # temporar și apoi îl redenumim, ca să nu rămână un fișier pe jumătate scris.
    def update(self, access: str, refresh: str, expires_in: int):
        self.data.update(accessToken=access, refreshToken=refresh,
                         expiresAt=int(time.time()) + int(expires_in))
        self.save()

    # Scrie self.data pe disc (fișier temporar, apoi redenumire).
    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        tmp.replace(self.path)


# Cere un token nou pe baza refreshToken-ului (conexiunea trebuie să aibă aplicația autorizată).
# Atenție: tokenul vechi devine invalid imediat.
async def refresh_token(conn: Connection, tokens: TokenStore):
    try:
        res = await conn.request(REFRESH_TOKEN_REQ, {"refreshToken": tokens.data["refreshToken"]})
    except CTraderError as e:
        raise AuthError(f"cTrader a refuzat reînnoirea tokenului ({e}). "
                        "Rulează din nou: python ctrader_setup.py") from e
    tokens.update(res["accessToken"], res["refreshToken"], int(res["expiresIn"]))
    print("[cTrader] token reînnoit")


# Autorizează aplicația pe o conexiune nouă. Cu date greșite serverul închide conexiunea fără
# un mesaj de eroare, așa că transformăm închiderea într-un mesaj clar.
async def app_auth(conn: Connection, client_id: str, client_secret: str):
    try:
        await conn.request(APP_AUTH_REQ, {"clientId": client_id, "clientSecret": client_secret})
    except CTraderError as e:
        # Aplicația există, dar Spotware nu a activat-o încă (status „submitted”).
        if "not in active state" in e.description:
            raise AuthError("aplicația cTrader nu e încă aprobată de Spotware "
                            "(status „submitted” pe openapi.ctrader.com)") from e
        raise AuthError("cTrader a refuzat aplicația: verifică CTRADER_CLIENT_ID și "
                        f"CTRADER_CLIENT_SECRET din .env ({e})") from e
    except ConnectionError as e:
        raise AuthError("cTrader a refuzat aplicația: verifică CTRADER_CLIENT_ID și "
                        f"CTRADER_CLIENT_SECRET din .env ({conn.close_reason or e})") from e


# Decodează istoricul de ticuri primit de la cTrader: primul tic are timpul și prețul complete,
# următoarele doar diferența față de ticul anterior. Ordinea e de la cel mai nou la cel mai vechi.
def decode_ticks(data: list[dict]) -> list[tuple[int, float]]:
    if not data:
        return []
    # Diferențele de timp sunt negative (mergem înapoi în timp). Dacă le primim pozitive,
    # înseamnă că trebuie scăzute, nu adunate: semnul de mai jos acoperă ambele variante.
    sign = 1 if len(data) < 2 or int(data[1]["timestamp"]) <= 0 else -1
    t, p = int(data[0]["timestamp"]), int(data[0]["tick"])
    out = [(t, p / PRICE_SCALE)]
    for td in data[1:]:
        t += sign * int(td["timestamp"])
        p += sign * int(td["tick"])
        out.append((t, p / PRICE_SCALE))
    return out


# =====================================================================================
#  CTraderFeed – sursa de prețuri folosită de server.py
# =====================================================================================
class CTraderFeed:
    """Conexiune permanentă la cTrader: abonări la prețuri + istoric, cu reconectare automată."""

    def __init__(self, client_id: str, client_secret: str, tokens: TokenStore):
        self.client_id = client_id
        self.client_secret = client_secret
        self.tokens = tokens
        # on_ticks(simbol, [[timp_ms, bid, ask], ...]): apelată la fiecare preț nou (în bucla asyncio).
        self.on_ticks = None
        # on_status(conectat: bool): apelată când conexiunea cu cTrader cade sau revine.
        self.on_status = None
        self.conn: Connection | None = None
        # ready = suntem conectați, autorizați și avem lista de simboluri.
        self.ready = asyncio.Event()
        # Simbolurile contului: nume -> date (symbolId, descriere...). _lookup permite căutarea
        # și după forme ca „eur/usd” sau „eurusd”; _by_id face legătura inversă id -> nume.
        self.symbols: dict[str, dict] = {}
        self._lookup: dict[str, str] = {}
        self._by_id: dict[int, str] = {}
        # Meniul de simboluri: [{"s": nume, "d": descriere, "g": [clasa de active, categoria]}]
        self._tree: list[dict] = []
        # Detaliile complete (zecimale etc.), cerute doar pentru simbolurile folosite.
        self._details: dict[str, dict] = {}
        # Ultimul preț complet [timp_ms, bid, ask] al fiecărui simbol la care suntem abonați.
        self.quotes: dict[str, list] = {}
        # Prețuri incomplete (avem doar bid sau doar ask), până sosește și cealaltă latură.
        self._partial: dict[str, list] = {}
        # Abonările: ce simboluri vrea serverul (_want_raw, cum le-a cerut), ce simboluri
        # urmărim efectiv (subscribed). _wake trezește sarcina care le sincronizează.
        self._want_raw: set[str] = set()
        self.wanted: set[str] = set()
        self.subscribed: set[str] = set()
        self._failed: set[str] = set()
        self._wake = asyncio.Event()

    # ---------- Simboluri ----------
    # Forma „normalizată” a unui nume, pentru căutare: „eur/usd” și „EUR USD” devin „EURUSD”.
    @staticmethod
    def _norm(name: str) -> str:
        return name.upper().replace("/", "").replace(" ", "")

    # Numele exact al simbolului la broker, pornind de la ce a scris utilizatorul (sau None).
    def resolve(self, name: str) -> str | None:
        return self._lookup.get(self._norm(name))

    # Lista numelor de simboluri, în ordinea din cTrader; „enabled” = vizibile pentru trading.
    def symbol_names(self) -> tuple[list[str], list[str]]:
        items = sorted(self.symbols.values(), key=lambda s: s.get("sortingNumber", 1e18))
        return ([s["symbolName"] for s in items if s.get("enabled", True)],
                [s["symbolName"] for s in items])

    # Simbolurile cu categoria lor, pentru meniul de simboluri.
    def symbol_tree(self) -> list[dict]:
        return list(self._tree)

    # Zecimalele, pasul minim și descrierea unui simbol (cerute o singură dată, apoi păstrate).
    async def info(self, name: str) -> dict:
        if name in self._details:
            return self._details[name]
        light = self.symbols[name]
        res = await self._conn().request(SYMBOL_BY_ID_REQ, {
            "ctidTraderAccountId": self.tokens.account_id, "symbolId": [light["symbolId"]]})
        if not res.get("symbol"):
            raise CTraderError("SYMBOL_NOT_FOUND", name)
        digits = int(res["symbol"][0]["digits"])
        d = {"name": name, "id": int(light["symbolId"]), "digits": digits,
             "point": round(10 ** -digits, digits),
             "description": light.get("description") or name}
        self._details[name] = d
        return d

    # Conexiunea curentă, gata de cereri; aruncă ConnectionError dacă suntem deconectați.
    def _conn(self) -> Connection:
        if not self.ready.is_set() or self.conn is None:
            raise ConnectionError("Nu sunt conectat la cTrader")
        return self.conn

    # ---------- Abonări la prețuri ----------
    # Setează simbolurile pe care vrem să le urmărim (restul se dezabonează).
    # Poate fi apelată oricând, chiar înainte de conectare.
    def want(self, names: set[str]):
        self._want_raw = set(names)
        # Numele exacte la broker, fără simbolurile la care abonarea a eșuat deja.
        wanted = {r for n in names if (r := self.resolve(n)) and r not in self._failed}
        self.wanted = wanted
        if wanted != self.subscribed:
            self._wake.set()

    # Ultimul preț [timp_ms, bid, ask] al unui simbol. Dacă nu suntem abonați la el, îl luăm
    # din istoricul de ticuri (ultimul bid și ultimul ask din ultimele zile).
    async def quote(self, name: str, wait: float = 3.0) -> list | None:
        deadline = time.monotonic() + wait
        # Abia ne-am abonat? Serverul trimite prețul curent imediat după abonare.
        while name in self.wanted and name not in self.quotes and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        if name in self.quotes:
            return list(self.quotes[name])
        # Nu avem preț live: căutăm ultimul bid și ultimul ask din ultimele 7 zile de ticuri.
        end = int(time.time() * 1000)
        bid = await self.ticks(name, "BID", end - 7 * 86400_000, end, max_pages=1)
        ask = await self.ticks(name, "ASK", end - 7 * 86400_000, end, max_pages=1)
        if not bid or not ask:
            return None
        return [max(bid[-1][0], ask[-1][0]), bid[-1][1], ask[-1][1]]

    # Mesajele trimise de server din proprie inițiativă (nu răspunsuri): prețuri noi, simboluri
    # modificate, sesiune închisă.
    def _on_event(self, pt: int, p: dict):
        if pt == SPOT_EVENT:
            self._on_spot(p)
        elif pt == SYMBOL_CHANGED_EVENT:
            # Brokerul a modificat simboluri (ex. zecimalele): le recitim la următoarea folosire.
            changed = {self._by_id.get(int(i)) for i in p.get("symbolId", [])}
            for name in changed:
                self._details.pop(name, None)
        elif pt in (TOKEN_INVALIDATED_EVENT, CLIENT_DISCONNECT_EVENT, ACCOUNT_DISCONNECT_EVENT):
            # Serverul a închis sesiunea (token reînnoit/revocat, mentenanță...): ne reconectăm.
            print(f"[cTrader] sesiune închisă de server ({pt}): {p.get('reason', '')}")
            if self.conn:
                asyncio.create_task(self.conn.close())

    # Un preț nou. Evenimentul conține doar ce s-a schimbat (bid, ask sau amândouă), deci
    # completăm cu valorile anterioare.
    def _on_spot(self, p: dict):
        name = self._by_id.get(int(p["symbolId"]))
        if name is None or name not in self.wanted or ("bid" not in p and "ask" not in p):
            return
        prev = self.quotes.get(name) or self._partial.get(name) or [0, 0.0, 0.0]
        digits = self._details[name]["digits"] if name in self._details else 5
        # [timp_ms, bid, ask]: latura care lipsește din eveniment o luăm din prețul anterior.
        q = [int(p.get("timestamp") or time.time() * 1000),
             round(int(p["bid"]) / PRICE_SCALE, digits) if "bid" in p else prev[1],
             round(int(p["ask"]) / PRICE_SCALE, digits) if "ask" in p else prev[2]]
        if q[1] <= 0 or q[2] <= 0:
            # Încă nu avem ambele prețuri: le ținem deoparte până vine și celălalt.
            self._partial[name] = q
            return
        self.quotes[name] = q
        if self.on_ticks:
            self.on_ticks(name, [list(q)])

    # Sincronizează abonările cu cTrader de câte ori se schimbă lista dorită.
    async def _sync_loop(self, conn: Connection):
        acc = self.tokens.account_id
        while True:
            # Dormim până când want() anunță o schimbare.
            await self._wake.wait()
            self._wake.clear()
            # Simbolurile care nu mai sunt dorite: dezabonare.
            for name in self.subscribed - self.wanted:
                self.subscribed.discard(name)
                self.quotes.pop(name, None)
                try:
                    await conn.request(UNSUBSCRIBE_SPOTS_REQ, {
                        "ctidTraderAccountId": acc, "symbolId": [self.symbols[name]["symbolId"]]})
                except CTraderError as e:
                    print(f"[cTrader] dezabonare {name}: {e}")
            # Simbolurile dorite la care nu suntem încă abonați: abonare.
            for name in self.wanted - self.subscribed:
                try:
                    info = await self.info(name)   # zecimalele, pentru rotunjirea prețurilor
                    await conn.request(SUBSCRIBE_SPOTS_REQ, {
                        "ctidTraderAccountId": acc, "symbolId": [info["id"]],
                        "subscribeToSpotTimestamp": True})
                    self.subscribed.add(name)
                except CTraderError as e:
                    if e.code == "ALREADY_SUBSCRIBED":
                        self.subscribed.add(name)
                    else:
                        print(f"[cTrader] abonare {name} eșuată: {e}")
                        self._failed.add(name)
                        self.wanted.discard(name)

    # ---------- Istoric ----------
    # Ticurile unei laturi ("BID" sau "ASK") între start_ms și end_ms, în ordine cronologică:
    # [(timp_ms, preț), ...]. cTrader le trimite în bucăți, de la cele mai noi spre cele mai vechi;
    # cerem cel mult max_pages bucăți (deci pentru intervale foarte aglomerate pot lipsi cele
    # mai vechi ticuri).
    async def ticks(self, name: str, side: str, start_ms: int, end_ms: int,
                    max_pages: int = 10) -> list[tuple[int, float]]:
        conn = self._conn()
        info = await self.info(name)
        out: list[tuple[int, float]] = []
        to = end_ms
        for _ in range(max_pages):
            res = await conn.request(GET_TICKDATA_REQ, {
                "ctidTraderAccountId": self.tokens.account_id, "symbolId": info["id"],
                "type": side, "fromTimestamp": start_ms, "toTimestamp": to})
            page = decode_ticks(res.get("tickData") or [])   # bucata primită, decodată
            out.extend(page)
            if not res.get("hasMore") or not page:
                break
            # Bucata următoare: tot ce e mai vechi decât cel mai vechi tic primit.
            to = min(t for t, _ in page) - 1
            if to <= start_ms:
                break
        d = info["digits"]
        # Mulțimea {...} elimină ticurile duplicate (bucățile se pot suprapune), sorted le pune
        # în ordine cronologică.
        return sorted({(t, round(p, d)) for t, p in out})

    # Ultimele `count` lumânări gata calculate de cTrader (M1 și peste), deschise înainte de end_ms.
    # Mergem înapoi în timp, bucată cu bucată; ne oprim după 5 bucăți goale la rând (începutul
    # istoricului; weekendurile și sărbătorile sunt mai scurte de atât).
    async def trendbars(self, name: str, tf: int, count: int, end_ms: int) -> list[dict]:
        bars: list[dict] = []
        to, empty = end_ms, 0
        while len(bars) < count and empty < 5 and to > 0:
            # Bucata dinaintea lui `to`, lungă de BARS_CHUNK lumânări; o lipim în fața celor avute.
            frm = max(0, to - tf * 1000 * BARS_CHUNK)
            chunk = await self.bars_range(name, tf, frm, to)
            if chunk:
                bars = chunk + bars
                empty = 0
            else:
                empty += 1
            to = frm
        return bars[-count:]

    # Toate lumânările (M1 și peste) deschise între start_ms (inclusiv) și end_ms (exclusiv).
    # Cerem intervalul de la capăt spre început, în bucăți de BARS_CHUNK lumânări; dacă serverul
    # consideră o bucată prea mare (INCORRECT_BOUNDARIES), o înjumătățim.
    async def bars_range(self, name: str, tf: int, start_ms: int, end_ms: int) -> list[dict]:
        conn = self._conn()
        info = await self.info(name)
        d = info["digits"]
        bars: dict[int, dict] = {}   # ora deschiderii -> lumânarea (fără duplicate)
        window = tf * 1000 * BARS_CHUNK   # lungimea unei bucăți, în milisecunde
        to = end_ms - 1
        while to >= start_ms:
            frm = max(start_ms, to - window + 1)
            try:
                res = await conn.request(GET_TRENDBARS_REQ, {
                    "ctidTraderAccountId": self.tokens.account_id, "symbolId": info["id"],
                    "period": PERIODS[tf], "fromTimestamp": frm, "toTimestamp": to,
                    "count": BARS_CHUNK})
            except CTraderError as e:
                if e.code != "INCORRECT_BOUNDARIES" or window <= tf * 1000:
                    raise
                window //= 2
                continue
            for b in res.get("trendbar", []):
                # Prețurile vin ca „low” + diferențe față de low.
                low = int(b.get("low", 0))
                t = int(b["utcTimestampInMinutes"]) * 60
                bars[t] = {"time": t,
                           "open": round((low + int(b.get("deltaOpen", 0))) / PRICE_SCALE, d),
                           "high": round((low + int(b.get("deltaHigh", 0))) / PRICE_SCALE, d),
                           "low": round(low / PRICE_SCALE, d),
                           "close": round((low + int(b.get("deltaClose", 0))) / PRICE_SCALE, d),
                           "volume": int(b.get("volume", 0))}
            to = frm - 1   # următoarea bucată: imediat înaintea celei de acum
        # În ordine cronologică, doar cele din interval.
        return [bars[t] for t in sorted(bars) if start_ms <= t * 1000 < end_ms]

    # ---------- Conexiunea permanentă ----------
    # Rulează cât timp merge serverul: se conectează și, la orice întrerupere, se reconectează
    # cu pauze tot mai mari (1s, 2s, 4s... maximum 30s; 60s la erori de autorizare).
    async def run(self):
        delay = 1
        while True:
            started = time.monotonic()
            try:
                await self._session()
                reason = "conexiunea s-a închis"
            except AuthError as e:
                reason, delay = str(e), 60
            except Exception as e:  # noqa: BLE001 - bucla nu trebuie să se oprească
                reason = f"{type(e).__name__}: {e}"
            finally:
                # Orice s-a întâmplat: anunțăm deconectarea și ștergem starea conexiunii vechi.
                if self.ready.is_set() and self.on_status:
                    self.on_status(False)
                self.ready.clear()
                self.subscribed.clear()
                self.quotes.clear()
                self._partial.clear()
                if self.conn:
                    await self.conn.close()
                    self.conn = None
            # O sesiune care a mers măcar un minut resetează pauza.
            if time.monotonic() - started > 60 and delay < 60:
                delay = 1
            print(f"[cTrader] {reason}; reconectare în {delay}s")
            await asyncio.sleep(delay)
            # Dublăm pauza (maximum 30 s); la erori de autorizare rămâne 60 s.
            delay = min(delay * 2, 30) if delay < 60 else 60

    # O sesiune completă: conectare, autorizare aplicație + cont, lista de simboluri, abonări.
    # Se termină abia când conexiunea se închide (atunci run() se reconectează).
    async def _session(self):
        tok = self.tokens
        conn = Connection(tok.is_live, self._on_event)
        self.conn = conn
        await conn.open()
        await app_auth(conn, self.client_id, self.client_secret)
        if tok.expires_soon():
            await refresh_token(conn, tok)
        try:
            await conn.request(ACCOUNT_AUTH_REQ, {"ctidTraderAccountId": tok.account_id,
                                                  "accessToken": tok.data["accessToken"]})
        except CTraderError as e:
            if e.code not in ("CH_ACCESS_TOKEN_INVALID", "OA_AUTH_TOKEN_EXPIRED"):
                raise AuthError(f"cTrader a refuzat contul {tok.account_id}: {e}") from e
            # Token expirat: încercăm o reînnoire, apoi încă o dată autorizarea.
            await refresh_token(conn, tok)
            await conn.request(ACCOUNT_AUTH_REQ, {"ctidTraderAccountId": tok.account_id,
                                                  "accessToken": tok.data["accessToken"]})
        await self._load_symbols(conn)
        self._failed.clear()
        self.ready.set()
        print(f"[cTrader] conectat: cont {tok.data.get('login', tok.account_id)} "
              f"({tok.data.get('broker', '')}, {'real' if tok.is_live else 'demo'}), "
              f"{len(self.symbols)} simboluri")
        # Reluăm abonările dorite (după o reconectare, serverul nu le mai ține minte).
        self.want(self._want_raw)
        self._wake.set()
        if self.on_status:
            self.on_status(True)
        # În fundal: sincronizarea abonărilor și verificarea orară a tokenului.
        tasks = [asyncio.create_task(self._sync_loop(conn)), asyncio.create_task(self._token_watch(conn))]
        try:
            await conn.wait_closed()
        finally:
            for t in tasks:
                t.cancel()

    # Citește lista de simboluri a contului și construiește tabelele de căutare (nume, id, meniu).
    async def _load_symbols(self, conn: Connection):
        res = await conn.request(SYMBOLS_LIST_REQ, {"ctidTraderAccountId": self.tokens.account_id})
        self.symbols = {s["symbolName"]: s for s in res.get("symbol", []) if s.get("symbolName")}
        self._lookup = {self._norm(n): n for n in self.symbols}
        self._by_id = {int(s["symbolId"]): n for n, s in self.symbols.items()}
        self._details.clear()
        self._tree = await self._load_tree(conn)

    # Categoriile simbolurilor: clasa de active (Forex, Metals, US Shares...) și, unde există,
    # categoria din ea (ex. EU Shares / France). „Default Category” nu adaugă nimic, deci o sărim.
    # Ordinea e cea din cTrader (sortingNumber). Fără categorii (eroare), toate ajung la „Altele”.
    async def _load_tree(self, conn: Connection) -> list[dict]:
        acc = {"ctidTraderAccountId": self.tokens.account_id}
        try:
            classes = (await conn.request(ASSET_CLASS_LIST_REQ, acc)).get("assetClass", [])
            cats = (await conn.request(SYMBOL_CATEGORY_REQ, acc)).get("symbolCategory", [])
        except CTraderError as e:
            print(f"[cTrader] categoriile simbolurilor nu s-au putut citi: {e}")
            classes, cats = [], []
        classes = {int(c["id"]): c for c in classes}
        cats = {int(c["id"]): c for c in cats}

        # Pentru un simbol: (cheia de sortare, grupul din meniu, ex. ["Forex"] sau ["Shares", "France"]).
        def place(s: dict) -> tuple:
            cat = cats.get(int(s.get("symbolCategoryId", -1)))
            cls = classes.get(int(cat["assetClassId"])) if cat else None
            group = [cls["name"]] if cls else ["Altele"]
            if cat and cat.get("name") and cat["name"] != "Default Category":
                group.append(cat["name"])
            order = (cls.get("sortingNumber", 1e18) if cls else 1e18,
                     cat.get("sortingNumber", 1e18) if cat else 1e18, s.get("sortingNumber", 1e18))
            return order, group

        placed = sorted((place(s) + (s,) for s in self.symbols.values()), key=lambda x: x[0])
        return [{"s": s["symbolName"], "d": s.get("description", ""), "g": g} for _, g, s in placed]

    # Verifică o dată pe oră dacă tokenul expiră curând; dacă da, închide conexiunea, iar la
    # reconectare tokenul se reînnoiește (vezi _session).
    async def _token_watch(self, conn: Connection):
        while True:
            await asyncio.sleep(3600)
            if self.tokens.expires_soon():
                await conn.close()
                return
