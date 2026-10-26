"""
Sursa de prețuri a serverului: baza de date QuestDB de pe mașina virtuală (VM), ținută la zi de
collector.py. PC-ul nu se mai conectează la cTrader: tot ce afișează și calculează server.py
(ticuri live, istoric, lumânări, informațiile simbolurilor) se citește de aici.

Are aceeași interfață ca CTraderFeed (ctrader.py), deci restul lui server.py nu depinde de sursă.
"""
# =====================================================================================
#  CE FACE ACEST FIȘIER (pe scurt)
# -------------------------------------------------------------------------------------
#  - Ticuri live: un fir separat întreabă QuestDB ce ticuri noi au apărut după ultimul tic trimis
#    (pe fiecare simbol) și le predă serverului (on_ticks), exact ca un tic venit de la cTrader.
#    Cât de des: la POLL_INTERVAL (50 ms) cât e deschis un grafic și vin ticuri; la POLL_QUIET
#    (250 ms) după QUIET_AFTER secunde fără ticuri (noapte, weekend); la POLL_IDLE (1 s) fără
#    nicio pagină deschisă (doar alarmele). Așa QuestDB de pe VM nu lucrează degeaba. Colectorul scrie ticurile în ordine, deci „după ultimul tic
#    trimis” nu sare peste niciunul; ticurile completate din istoric (mai vechi) nu se retrimit.
#  - Istoric de ticuri (ticks): din tabelul ticks; pentru o latură (BID / ASK) păstrăm doar
#    rândurile în care prețul acelei laturi s-a schimbat, ca în istoricul cTrader.
#  - Lumânări (trendbars / bars_range): de la M1 în sus, cele închise, din tabelul bars (lumânările
#    oficiale cTrader salvate de colector). După ultima lumânare salvată (lumânarea în curs, sau una
#    tocmai închisă pe care colectorul n-a scris-o încă), lumânarea se calculează din ticuri (bid).
#    Sub un minut (1s–30s), toate lumânările se calculează în QuestDB din ticuri (SAMPLE BY).
#  - Ticuri brute (tick_rows): pentru timeframe-ul „Ticuri”, unul câte unul.
#  - Simboluri: doar cele colectate (tabelul symbols): zecimale, descriere, categorie.
#  - Recuperarea (sync): la fiecare SYNC_POLL secunde, starea scrisă de colector în collector_status
#    (sync cu progresul / live / error) și, la WAL_POLL secunde, wal_tables() (un tabel suspendat
#    sau cu jurnalul resetat nu mai primește date). Rezultatul (self.sync) merge la pagini
#    (on_sync), pentru bara de progres; colectorul tăcut de COLLECTOR_STALE secunde = „stale”.
#  - Starea: dacă QuestDB nu răspunde POLL_LOST secunde, sursa e „deconectată” (paginile afișează
#    asta și își reîncarcă istoricul la revenire), la fel ca la o cădere a conexiunii cTrader.
# =====================================================================================
import asyncio
import threading
import time

from tickdb import BARS, SYMBOLS, TICKS, TickDB

POLL_INTERVAL = 0.05
POLL_QUIET = 0.25
QUIET_AFTER = 10
POLL_IDLE = 1.0
POLL_LOST = 3.0
# Un simbol fără ticuri de mult (ex. piață închisă) nu face interogarea live să citească zile întregi.
POLL_WINDOW_US = 10 * 60 * 1_000_000
POLL_LIMIT = 20_000
# Câte lumânări de după ultima salvată se calculează cel mult din ticuri.
DERIVED_MAX = 3
SYNC_POLL = 2.0
WAL_POLL = 30.0
# Colectorul scrie starea cel puțin o dată pe minut; mai mult de atât fără ea = oprit / blocat.
COLLECTOR_STALE = 180


class QuestFeed:
    """Prețuri și istoric din QuestDB (aceeași interfață ca CTraderFeed)."""

    def __init__(self, source: str = "ctrader"):
        self.source = source
        self.db = TickDB(source)
        self.on_ticks = None     # callback(simbol, [[timp_ms, bid, ask], ...]) în bucla asyncio
        self.on_status = None    # callback(conectat: bool)
        self.on_sync = None      # callback(starea recuperării: dict), vezi _sync_state
        self.sync: dict = {"state": "unknown"}
        self.ready = asyncio.Event()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.symbols: dict[str, dict] = {}       # nume -> {digits, description, category}
        self._lookup: dict[str, str] = {}
        self.quotes: dict[str, list] = {}        # ultimul preț [timp_ms, bid, ask]
        self.wanted: set[str] = set()
        self.viewers = True      # e deschis cel puțin un grafic (polling rapid)
        self._lost: asyncio.Event | None = None
        self._stop = threading.Event()

    # ---------- Simboluri ----------
    @staticmethod
    def _norm(name: str) -> str:
        return name.upper().replace("/", "").replace(" ", "")

    def resolve(self, name: str) -> str | None:
        return self._lookup.get(self._norm(name))

    def symbol_names(self) -> tuple[list[str], list[str]]:
        names = sorted(self.symbols)
        return names, list(names)

    def symbol_tree(self) -> list[dict]:
        return [{"s": n, "d": s["description"], "g": [g for g in s["category"].split(" / ") if g] or ["Altele"]}
                for n, s in sorted(self.symbols.items())]

    async def info(self, name: str) -> dict:
        s = self.symbols.get(name)
        if s is None:
            raise ConnectionError(f"{name} nu e colectat în QuestDB")
        d = s["digits"]
        return {"name": name, "id": 0, "digits": d, "point": round(10 ** -d, d), "description": s["description"]}

    def _check_ready(self):
        if not self.ready.is_set():
            raise ConnectionError("QuestDB (VM) indisponibil")

    # Interogare SQL într-un fir separat; orice eroare devine ConnectionError (ca la cTrader).
    async def _q(self, sql: str) -> list[list]:
        self._check_ready()
        try:
            return await asyncio.to_thread(self.db.query, sql)
        except Exception as e:  # noqa: BLE001
            raise ConnectionError(f"QuestDB: {e}") from e

    def _where(self, name: str) -> str:
        return f"source = '{self.source}' AND symbol = '{name}'"

    # ---------- Prețuri ----------
    # Colectorul hotărăște ce simboluri se urmăresc; aici doar ținem minte ce cere serverul.
    def want(self, names: set[str], viewers: bool = True):
        self.wanted = {r for n in names if (r := self.resolve(n))}
        self.viewers = viewers

    async def quote(self, name: str, wait: float = 3.0) -> list | None:
        if name in self.quotes:
            return list(self.quotes[name])
        rows = await self._q(f"SELECT cast(ts AS long), bid, ask FROM {TICKS} WHERE {self._where(name)} "
                             "LATEST ON ts PARTITION BY symbol")
        return [rows[0][0] // 1000, rows[0][1], rows[0][2]] if rows else None

    # ---------- Istoric ----------
    # Ticurile unei laturi ("BID" / "ASK") între start_ms și end_ms (inclusiv), cronologic:
    # [(timp_ms, preț), ...], doar rândurile în care prețul laturii s-a schimbat.
    async def ticks(self, name: str, side: str, start_ms: int, end_ms: int,
                    max_pages: int = 10) -> list[tuple[int, float]]:
        col = "bid" if side == "BID" else "ask"
        rows = await self._q(f"SELECT cast(ts AS long), {col} FROM {TICKS} WHERE {self._where(name)} "
                             f"AND ts >= {int(start_ms) * 1000} AND ts <= {int(end_ms) * 1000} ORDER BY ts")
        out: list[tuple[int, float]] = []
        prev = None
        for t, p in rows:
            if p != prev:
                out.append((t // 1000, p))
                prev = p
        return out

    # Lumânările deschise în [start_s, end_s): cel mult `limit`, cele mai noi. M1 și peste: cele
    # închise, salvate de colector (tabelul bars). Sub un minut: calculate de QuestDB din ticuri
    # (inclusiv lumânarea în curs).
    async def _stored(self, name: str, tf: int, start_s: int, end_s: int, limit: int | None) -> list[dict]:
        if tf < 60:
            self._check_ready()
            try:
                if limit:
                    bars = await asyncio.to_thread(self.db.last_sampled, name, tf, end_s * 1_000_000, limit)
                    return [b for b in bars if b["time"] >= start_s]
                return await asyncio.to_thread(self.db.sample, name, tf, start_s * 1_000_000, end_s * 1_000_000)
            except Exception as e:  # noqa: BLE001
                raise ConnectionError(f"QuestDB: {e}") from e
        sql =(f"SELECT cast(ts AS long), open, high, low, close, volume FROM {BARS} "
               f"WHERE {self._where(name)} AND tf = {tf} AND ts >= {start_s * 1_000_000} "
               f"AND ts < {end_s * 1_000_000} ORDER BY ts" + (" DESC" if limit else ""))
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = await self._q(sql)
        if limit:
            rows.reverse()
        return [{"time": t // 1_000_000, "open": o, "high": h, "low": lo, "close": c, "volume": v}
                for t, o, h, lo, c, v in rows]

    # Ultimele `count` ticuri [timp_ms, bid, ask] dinaintea momentului before_ms (timeframe-ul „Ticuri”).
    async def tick_rows(self, name: str, before_ms: int, count: int) -> list[list]:
        self._check_ready()
        try:
            return await asyncio.to_thread(self.db.last_ticks, name, before_ms, count)
        except Exception as e:  # noqa: BLE001
            raise ConnectionError(f"QuestDB: {e}") from e

    # Lumânările de după ultima salvată (în curs / închise de curând), calculate din ticurile bid.
    async def _derived(self, name: str, tf: int, after_s: int | None, end_ms: int) -> list[dict]:
        if after_s is None:
            return []
        out = []
        start = after_s + tf
        # Cel mult DERIVED_MAX lumânări, cele mai noi (dacă colectorul a rămas mult în urmă).
        start = max(start, start + ((end_ms // 1000 - start) // tf - DERIVED_MAX + 1) * tf)
        while start * 1000 < end_ms:
            stop = min((start + tf) * 1000, end_ms)
            rows = await self._q(f"SELECT first(bid), max(bid), min(bid), last(bid), count() FROM {TICKS} "
                                 f"WHERE {self._where(name)} AND ts >= {start * 1_000_000} AND ts < {stop * 1000}")
            if rows and rows[0][4]:
                o, h, lo, c, n = rows[0]
                out.append({"time": start, "open": o, "high": h, "low": lo, "close": c, "volume": n})
            start += tf
        return out

    async def _last_stored(self, name: str, tf: int) -> int | None:
        rows = await self._q(f"SELECT cast(max(ts) AS long) FROM {BARS} WHERE {self._where(name)} AND tf = {tf}")
        return rows[0][0] // 1_000_000 if rows and rows[0][0] is not None else None

    # Ultimele `count` lumânări deschise înainte de end_ms.
    async def trendbars(self, name: str, tf: int, count: int, end_ms: int) -> list[dict]:
        bars = await self._stored(name, tf, 0, -(-end_ms // 1000), count)
        if tf < 60:
            return bars
        last = bars[-1]["time"] if bars else await self._last_stored(name, tf)
        bars += [b for b in await self._derived(name, tf, last, end_ms) if b["time"] * 1000 < end_ms]
        return bars[-count:]

    # Toate lumânările deschise între start_ms (inclusiv) și end_ms (exclusiv).
    async def bars_range(self, name: str, tf: int, start_ms: int, end_ms: int) -> list[dict]:
        bars = await self._stored(name, tf, start_ms // 1000, -(-end_ms // 1000), None)
        if tf < 60:
            return [b for b in bars if start_ms <= b["time"] * 1000 < end_ms]
        last = await self._last_stored(name, tf)
        if last is not None and (last + tf) * 1000 < end_ms:
            bars += [b for b in await self._derived(name, tf, last, end_ms) if b["time"] * 1000 >= start_ms]
        return [b for b in bars if start_ms <= b["time"] * 1000 < end_ms]

    # ---------- Conexiunea ----------
    # Rulează cât timp merge serverul: citește simbolurile, pornește firul de ticuri live și, dacă
    # QuestDB nu mai răspunde, reîncearcă la fiecare 5 secunde.
    async def run(self):
        self.loop = asyncio.get_running_loop()
        while True:
            try:
                await asyncio.to_thread(self._load_symbols)
                last = await asyncio.to_thread(self._last_ticks)
            except Exception as e:  # noqa: BLE001
                print(f"[QuestDB] indisponibil ({type(e).__name__}: {e}); reîncerc în 5 s")
                await asyncio.sleep(5)
                continue
            if not self.symbols:
                print("[QuestDB] tabelul symbols e gol: colectorul de pe VM n-a pornit încă; reîncerc în 5 s")
                await asyncio.sleep(5)
                continue
            self._lost = asyncio.Event()
            self._stop.clear()
            thread = threading.Thread(target=self._poll_loop, args=(last,), name="questdb-poll", daemon=True)
            thread.start()
            self.ready.set()
            print(f"[QuestDB] conectat: {self.db.host}, simboluri {', '.join(sorted(self.symbols))}")
            if self.on_status:
                self.on_status(True)
            try:
                await self._lost.wait()
            finally:
                self._stop.set()
                self.ready.clear()
                self.quotes.clear()
            if self.on_status:
                self.on_status(False)
            print("[QuestDB] legătura s-a pierdut, reîncerc...")
            await asyncio.sleep(1)

    def _load_symbols(self):
        rows = self.db.query(f"SELECT name, digits, description, category FROM {SYMBOLS} "
                             f"WHERE source = '{self.source}' LATEST ON updated PARTITION BY name")
        self.symbols = {n: {"digits": int(d), "description": desc or n, "category": cat or ""}
                        for n, d, desc, cat in rows}
        self._lookup = {self._norm(n): n for n in self.symbols}

    # Ora (µs) ultimului tic salvat pentru fiecare simbol: ticurile live pornesc de după el.
    def _last_ticks(self) -> dict[str, int]:
        rows = self.db.query(f"SELECT symbol, cast(ts AS long) FROM {TICKS} WHERE source = '{self.source}' "
                             "LATEST ON ts PARTITION BY symbol")
        return {s: t for s, t in rows}

    # Firul de ticuri live: ticurile de după ultimul trimis (pe simbol), la POLL_INTERVAL /
    # POLL_QUIET / POLL_IDLE (vezi începutul fișierului).
    def _poll_loop(self, last: dict[str, int]):
        db = TickDB(self.source)   # conexiunea HTTP proprie a firului
        failed_since = None
        next_sync = next_wal = 0.0
        problems: list[str] = []
        last_new = time.monotonic()
        while not self._stop.is_set():
            started = time.monotonic()
            if started >= next_sync:
                next_sync = started + SYNC_POLL
                try:
                    if started >= next_wal:
                        next_wal = started + WAL_POLL
                        problems = db.wal_problems()
                except Exception:  # noqa: BLE001 - QuestDB ocupat: data viitoare
                    pass
                try:
                    status = db.read_status()
                except Exception:  # noqa: BLE001 - tabelul nu există încă (colector vechi)
                    status = None
                st = self._sync_state(status, problems)
                if st != self.sync:
                    self.loop.call_soon_threadsafe(self._emit_sync, st)
            now_us = int(time.time() * 1_000_000)
            since = min([last.get(s, 0) for s in self.symbols] or [0])
            since = max(since, now_us - POLL_WINDOW_US)
            try:
                rows = db.query(f"SELECT symbol, cast(ts AS long), bid, ask FROM {TICKS} "
                                f"WHERE source = '{self.source}' AND ts > {since} ORDER BY ts LIMIT {POLL_LIMIT}",
                                timeout=5)
                failed_since = None
            except Exception as e:  # noqa: BLE001
                failed_since = failed_since or time.monotonic()
                if time.monotonic() - failed_since > POLL_LOST:
                    print(f"[QuestDB] ticurile live nu se mai pot citi: {type(e).__name__}: {e}")
                    self.loop.call_soon_threadsafe(self._lost.set)
                    return
                time.sleep(0.5)
                continue
            new: dict[str, list[list]] = {}
            for sym, t, bid, ask in rows:
                if t > last.get(sym, 0) and sym in self.symbols:
                    new.setdefault(sym, []).append([t // 1000, bid, ask])
                    last[sym] = t
            for sym, data in new.items():
                self.loop.call_soon_threadsafe(self._emit, sym, data)
            if new:
                last_new = time.monotonic()
            if not self.viewers:
                interval = POLL_IDLE
            elif time.monotonic() - last_new > QUIET_AFTER:
                interval = POLL_QUIET
            else:
                interval = POLL_INTERVAL
            time.sleep(max(0.0, interval - (time.monotonic() - started)))

    # Starea recuperării, pentru pagină: state = live / sync / error / stale / unknown; la sync,
    # progress (0–1), gap_from (ms: de când lipsesc ticuri) și eta (secunde).
    @staticmethod
    def _sync_state(st: dict | None, problems: list[str]) -> dict:
        if problems:
            return {"state": "error", "detail": "; ".join(problems)}
        if st is None:
            return {"state": "unknown"}
        if time.time() * 1000 - st["ts"] > COLLECTOR_STALE * 1000:
            return {"state": "stale", "since": st["ts"]}
        out = {"state": st["state"], "detail": st["detail"]}
        if st["state"] in ("sync", "error"):
            out.update(progress=st["progress"], gap_from=st["gap_from"], eta=st["eta"])
        return out

    def _emit_sync(self, st: dict):
        self.sync = st
        if self.on_sync:
            self.on_sync(st)

    # Rulează în bucla asyncio: reține ultimul preț și anunță serverul.
    def _emit(self, symbol: str, data: list[list]):
        self.quotes[symbol] = data[-1]
        if self.on_ticks:
            self.on_ticks(symbol, data)
