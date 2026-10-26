"""
Baza de date QuestDB de pe mașina virtuală (VM): ticuri, lumânări oficiale și simboluri.
Scrierea prin ILP (InfluxDB Line Protocol, pe HTTP), citirea prin interogări SQL pe HTTP (/exec).
Scrie collector.py (pe VM); citesc questfeed.py și datastore.py (server.py, pe PC).
"""
# =====================================================================================
#  CE FACE ACEST FIȘIER (pe scurt)
# -------------------------------------------------------------------------------------
#  Tabelele (create de ensure_tables, dacă nu există):
#    ticks         : un rând pe tic: ts (ora brokerului, UTC), source, symbol, bid, ask, recv_ts
#                    (când a fost scris rândul). Partiționat pe zile; DEDUP pe (ts, source, symbol),
#                    deci același tic scris de două ori (live + din istoric) rămâne un singur rând.
#    tick_coverage : un rând pe zi / simbol: complete (ziua e salvată toată, din istoricul
#                    brokerului), empty (zi fără ticuri: weekend, sărbătoare) sau partial (ziua de azi).
#    bars          : lumânările oficiale cTrader, închise (M1–W1): ts = ora deschiderii, tf = secunde.
#                    DEDUP pe (ts, source, symbol, tf): o lumânare corectată de broker se rescrie.
#    bar_status    : starea fiecărei serii de lumânări (series = „EURUSD:60”): câte sunt, prima,
#                    ultima, dacă e tot istoricul brokerului. Se citește ultima stare (LATEST ON).
#    symbols       : simbolurile colectate: zecimale, descriere, categorie (ultima stare, LATEST ON).
#    collector_status : starea colectorului, scrisă de el la câteva secunde cât recuperează un gol
#                    și o dată pe minut în rest (ultima stare, LATEST ON): live / sync (cu progresul
#                    și de când lipsesc ticuri) / error. server.py o arată în pagină (bara de progres).
#
#  - TickDB.live(...)       : ticurile live; NU blochează (le pune într-o coadă). Un fir separat
#                             le trimite pe loturi: la 1000 de rânduri sau la 100 ms.
#  - TickDB.write_rows(...) : ticurile unei zile din istoric, scrise dintr-o dată (blochează; se
#                             apelează cu asyncio.to_thread). La fel write_bars / write_symbols.
#  - TickDB.compress_days(...) : convertește zilele vechi din ticks în Parquet comprimat (ZSTD 3,
#                             setat în server.conf), fără pierderi; ~6 octeți pe tic în loc de ~40.
#  - TickDB.tick_count(...) / wait_ticks(...) : verifică că ticurile scrise chiar se văd în QuestDB
#                             (o zi se marchează completă doar după asta).
#  - TickDB.live_ok()       : ticurile live trimise acum o jumătate de secundă se văd deja în
#                             QuestDB? Istoricul așteaptă cât timp nu (live-ul are prioritate).
#  - TickDB.wal_problems()  : tabelele noastre cu WAL suspendat sau cu jurnalul de tranzacții
#                             resetat (după o oprire bruscă a VM-ului): în ele scrierile nu se aplică.
#  - TickDB.query(...)      : o interogare SQL; întoarce rândurile ca listă de liste. Fiecare fir
#                             își păstrează conexiunea HTTP deschisă (fără o conexiune nouă la
#                             fiecare interogare: contează la interogările live, la 50 ms).
#
#  Setările vin din .env: QUESTDB_HOST (implicit localhost), QUESTDB_USER, QUESTDB_PASSWORD.
# =====================================================================================
import base64
import collections
import http.client
import json
import os
import queue
import threading
import time
import urllib.parse
from datetime import datetime, timezone

from questdb import Sender, TimestampMicros

TICKS = "ticks"
COVERAGE = "tick_coverage"
BARS = "bars"
BAR_STATUS = "bar_status"
SYMBOLS = "symbols"
STATUS = "collector_status"
TABLES = (TICKS, COVERAGE, BARS, BAR_STATUS, SYMBOLS, STATUS)

# Lotul live pleacă la LIVE_BATCH rânduri sau la LIVE_INTERVAL secunde, ce vine primul.
LIVE_BATCH = 1000
LIVE_INTERVAL = 0.1
# Rândurile din istoric se trimit în bucăți de atâtea rânduri (o cerere HTTP pe bucată).
HISTORY_BATCH = 50_000

SCHEMA = [
    f"CREATE TABLE IF NOT EXISTS {TICKS} (ts TIMESTAMP, source SYMBOL, symbol SYMBOL, bid DOUBLE, "
    "ask DOUBLE, recv_ts TIMESTAMP) TIMESTAMP(ts) PARTITION BY DAY WAL "
    "DEDUP UPSERT KEYS(ts, source, symbol)",
    f"CREATE TABLE IF NOT EXISTS {COVERAGE} (day TIMESTAMP, source SYMBOL, symbol SYMBOL, "
    "status SYMBOL, ticks LONG, checked TIMESTAMP) TIMESTAMP(day) PARTITION BY YEAR WAL "
    "DEDUP UPSERT KEYS(day, source, symbol)",
    f"CREATE TABLE IF NOT EXISTS {BARS} (ts TIMESTAMP, source SYMBOL, symbol SYMBOL, tf LONG, "
    "open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume LONG) TIMESTAMP(ts) PARTITION BY MONTH WAL "
    "DEDUP UPSERT KEYS(ts, source, symbol, tf)",
    f"CREATE TABLE IF NOT EXISTS {BAR_STATUS} (updated TIMESTAMP, source SYMBOL, series SYMBOL, "
    "bars LONG, first_bar TIMESTAMP, last_bar TIMESTAMP, complete BOOLEAN) TIMESTAMP(updated) "
    "PARTITION BY MONTH WAL",
    f"CREATE TABLE IF NOT EXISTS {SYMBOLS} (updated TIMESTAMP, source SYMBOL, name SYMBOL, "
    "digits LONG, description STRING, category STRING) TIMESTAMP(updated) PARTITION BY YEAR WAL",
    f"CREATE TABLE IF NOT EXISTS {STATUS} (ts TIMESTAMP, source SYMBOL, state SYMBOL, progress DOUBLE, "
    "gap_from TIMESTAMP, eta LONG, detail STRING) TIMESTAMP(ts) PARTITION BY DAY WAL",
]


# Milisecunde UTC -> ora în forma acceptată de QuestDB (microsecunde).
def micros(ms: int) -> TimestampMicros:
    return TimestampMicros(int(ms) * 1000)


class TickDB:
    """Legătura cu QuestDB: scrierea ticurilor (live și din istoric) și interogări SQL."""

    def __init__(self, source: str, log=print):
        self.source = source
        self.log = log
        self.host = os.getenv("QUESTDB_HOST", "localhost").strip()
        user = os.getenv("QUESTDB_USER", "").strip()
        password = os.getenv("QUESTDB_PASSWORD", "").strip()
        auth = f"username={user};password={password};" if user else ""
        self.conf = f"http::addr={self.host}:9000;{auth}auto_flush=off;"
        self._auth = ("Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()) if user else ""
        self._local = threading.local()   # conexiunea HTTP a fiecărui fir (vezi query)
        # Coada ticurilor live: (simbol, timp_ms, bid, ask). Plină = QuestDB nu mai ține pasul;
        # aruncăm ticul (oricum ziua se rescrie complet din istoric după ce se termină).
        self._live: queue.Queue = queue.Queue(maxsize=200_000)
        self.dropped = 0
        # Ultimul tic live trimis pe fiecare simbol: (timp_ms, când s-a trimis, time.time()).
        self.flushed: dict[str, tuple[int, float]] = {}
        # Ultimele loturi live trimise: (când, {simbol: ultimul tic}), pentru live_ok().
        self.recent_flushes: collections.deque = collections.deque(maxlen=400)
        self._thread: threading.Thread | None = None

    # ---------- Interogări ----------
    # Rulează o interogare SQL pe /exec și întoarce rândurile (blochează: din asyncio, prin to_thread).
    # O conexiune căzută (QuestDB repornit, rețea) se redeschide o dată și cererea se repetă.
    def query(self, sql: str, timeout: float = 60) -> list[list]:
        path = "/exec?" + urllib.parse.urlencode({"query": sql})
        headers = {"Authorization": self._auth} if self._auth else {}
        for attempt in (1, 2):
            conn = getattr(self._local, "conn", None)
            if conn is None:
                conn = self._local.conn = http.client.HTTPConnection(self.host, 9000, timeout=timeout)
            try:
                conn.timeout = timeout
                conn.request("GET", path, headers=headers)
                r = conn.getresponse()
                body = r.read()
                break
            except (OSError, http.client.HTTPException):
                conn.close()
                self._local.conn = None
                if attempt == 2:
                    raise
        if r.status == 401:
            raise PermissionError("QuestDB: utilizator / parolă greșite (QUESTDB_USER / QUESTDB_PASSWORD)")
        res = json.loads(body)
        if "error" in res:
            raise RuntimeError(f"QuestDB: {res['error']}")
        return res.get("dataset", [])

    # Creează tabelele dacă lipsesc.
    def ensure_tables(self):
        for sql in SCHEMA:
            self.query(sql)

    # Zilele deja rezolvate (complete / empty) ale unui simbol: {început_zi_ms, ...}.
    def done_days(self, symbol: str) -> set[int]:
        rows = self.query(f"SELECT day FROM {COVERAGE} WHERE source = '{self.source}' AND symbol = '{symbol}' "
                          "AND status IN ('complete', 'empty')")
        return {iso_ms(r[0]) for r in rows}

    # ---------- Lumânări sub un minut, din ticuri ----------
    # Lumânările de tf secunde (bid) deschise în [lo_us, hi_us), calculate de QuestDB din ticuri:
    # [{time (secunde), open, high, low, close, volume = numărul de ticuri}, ...]. Intervalele fără
    # ticuri lipsesc (ca la lumânările brokerului).
    def sample(self, symbol: str, tf: int, lo_us: int, hi_us: int) -> list[dict]:
        rows = self.query(f"SELECT cast(ts AS long), first(bid), max(bid), min(bid), last(bid), count() "
                          f"FROM {TICKS} WHERE source = '{self.source}' AND symbol = '{symbol}' "
                          f"AND ts >= {int(lo_us)} AND ts < {int(hi_us)} SAMPLE BY {tf}s ALIGN TO CALENDAR")
        return [{"time": t // 1_000_000, "open": o, "high": h, "low": lo, "close": c, "volume": n}
                for t, o, h, lo, c, n in rows]

    # Ultimele `limit` lumânări de tf secunde deschise înainte de hi_us. Fereastra de căutare
    # crește de 4 ori până găsește destule (nopți, weekenduri) sau ajunge la primul tic salvat.
    def last_sampled(self, symbol: str, tf: int, hi_us: int, limit: int) -> list[dict]:
        rows = self.query(f"SELECT cast(min(ts) AS long) FROM {TICKS} "
                          f"WHERE source = '{self.source}' AND symbol = '{symbol}'")
        first = rows[0][0] if rows and rows[0][0] is not None else hi_us
        span = tf * limit * 2 * 1_000_000
        while True:
            step = tf * 1_000_000
            lo = max(hi_us - span, first) // step * step   # aliniat: prima lumânare e întreagă
            bars = self.sample(symbol, tf, lo, hi_us)
            if len(bars) >= limit or lo <= first:
                return bars[-limit:]
            span *= 4

    # Ultimele `limit` ticuri [timp_ms, bid, ask] dinaintea momentului before_ms, cronologic.
    def last_ticks(self, symbol: str, before_ms: int, limit: int) -> list[list]:
        rows = self.query(f"SELECT cast(ts AS long), bid, ask FROM {TICKS} WHERE source = '{self.source}' "
                          f"AND symbol = '{symbol}' AND ts < {int(before_ms) * 1000} ORDER BY ts DESC LIMIT {int(limit)}")
        return [[t // 1000, b, a] for t, b, a in reversed(rows)]

    # Ora (ms) ultimului tic salvat pentru simbol înainte de momentul written_before (ms, după
    # recv_ts), sau None dacă nu există niciunul. Ticurile live sosite după reconectare au recv_ts
    # mai nou, deci nu ascund golul de dinaintea lor.
    def last_tick(self, symbol: str, written_before: int) -> int | None:
        rows = self.query(f"SELECT max(ts) FROM {TICKS} WHERE source = '{self.source}' AND symbol = '{symbol}' "
                          f"AND recv_ts < {int(written_before) * 1000}")
        return iso_ms(rows[0][0]) if rows and rows[0][0] else None

    # ---------- Scriere ----------
    # Ticuri live [[timp_ms, bid, ask], ...]; nu blochează niciodată.
    def live(self, symbol: str, ticks: list[list]):
        for t, bid, ask in ticks:
            try:
                self._live.put_nowait((symbol, t, bid, ask))
            except queue.Full:
                self.dropped += 1

    # Câte ticuri live așteaptă încă să fie trimise.
    def pending(self) -> int:
        return self._live.qsize()

    # Pornește firul care trimite ticurile live.
    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._live_loop, name="questdb-live", daemon=True)
            self._thread.start()

    def _live_loop(self):
        while True:
            try:
                with Sender.from_conf(self.conf) as s:
                    pending, since = 0, time.monotonic()
                    batch: dict[str, int] = {}
                    while True:
                        try:
                            symbol, t, bid, ask = self._live.get(timeout=LIVE_INTERVAL)
                            s.row(TICKS, symbols={"source": self.source, "symbol": symbol},
                                  columns={"bid": bid, "ask": ask, "recv_ts": micros(time.time() * 1000)},
                                  at=micros(t))
                            pending += 1
                            batch[symbol] = max(t, batch.get(symbol, 0))
                        except queue.Empty:
                            pass
                        if pending and (pending >= LIVE_BATCH or time.monotonic() - since >= LIVE_INTERVAL):
                            s.flush()
                            sent = time.time()
                            self.flushed.update({sym: (t, sent) for sym, t in batch.items()})
                            self.recent_flushes.append((sent, batch))
                            pending, since, batch = 0, time.monotonic(), {}
                        elif not pending:
                            since = time.monotonic()
            except Exception as e:  # noqa: BLE001 - QuestDB oprit / rețea: reîncercăm
                self.log(f"[QUESTDB] scriere live eșuată: {type(e).__name__}: {e}; reîncerc în 5 s")
                time.sleep(5)

    # Ticurile unei zile din istoric, [[timp_ms, bid, ask], ...]; blochează până sunt scrise.
    def write_rows(self, symbol: str, rows: list[list]):
        now = micros(time.time() * 1000)
        with Sender.from_conf(self.conf) as s:
            for i, (t, bid, ask) in enumerate(rows, 1):
                s.row(TICKS, symbols={"source": self.source, "symbol": symbol},
                      columns={"bid": bid, "ask": ask, "recv_ts": now}, at=micros(t))
                if i % HISTORY_BATCH == 0:
                    s.flush()
            s.flush()

    # Lumânări oficiale închise [{time (secunde), open, high, low, close, volume}, ...] ale unei serii.
    def write_bars(self, symbol: str, tf: int, bars: list[dict]):
        with Sender.from_conf(self.conf) as s:
            for i, b in enumerate(bars, 1):
                s.row(BARS, symbols={"source": self.source, "symbol": symbol},
                      columns={"tf": tf, "open": float(b["open"]), "high": float(b["high"]),
                               "low": float(b["low"]), "close": float(b["close"]), "volume": int(b["volume"])},
                      at=micros(b["time"] * 1000))
                if i % HISTORY_BATCH == 0:
                    s.flush()
            s.flush()

    # Starea unei serii de lumânări, după o actualizare.
    def mark_bars(self, symbol: str, tf: int, count: int, first: int, last: int, complete: bool):
        with Sender.from_conf(self.conf) as s:
            s.row(BAR_STATUS, symbols={"source": self.source, "series": f"{symbol}:{tf}"},
                  columns={"bars": count, "first_bar": micros(first * 1000), "last_bar": micros(last * 1000),
                           "complete": complete}, at=micros(time.time() * 1000))
            s.flush()

    # Ultima stare a seriei: {bars, first, last (secunde), complete}, sau None dacă nu există.
    def bar_status(self, symbol: str, tf: int) -> dict | None:
        rows = self.query(f"SELECT bars, first_bar, last_bar, complete FROM {BAR_STATUS} "
                          f"WHERE source = '{self.source}' AND series = '{symbol}:{tf}' "
                          "LATEST ON updated PARTITION BY series")
        if not rows:
            return None
        n, first, last, complete = rows[0]
        return {"bars": n, "first": iso_ms(first) // 1000, "last": iso_ms(last) // 1000, "complete": complete}

    # Simbolurile colectate: [{name, digits, description, category}, ...].
    def write_symbols(self, items: list[dict]):
        now = micros(time.time() * 1000)
        with Sender.from_conf(self.conf) as s:
            for it in items:
                s.row(SYMBOLS, symbols={"source": self.source, "name": it["name"]},
                      columns={"digits": int(it["digits"]), "description": it["description"],
                               "category": it["category"]}, at=now)
            s.flush()

    # ---------- Compresia zilelor vechi ----------
    # Zilele (partițiile) din ticks mai vechi decât `before` (ms), încă necomprimate, în care TOATE
    # simbolurile sunt rezolvate în tick_coverage (complete / empty). O zi în care încă se scrie
    # (istoric necompletat) nu se convertește: în ea pot intra ticuri noi.
    def compressible_days(self, symbols: list[str], before: int) -> list[str]:
        done: dict[str, set[str]] = {}
        for day, sym in self.query(f"SELECT day, symbol FROM {COVERAGE} WHERE source = '{self.source}' "
                                   "AND status IN ('complete', 'empty')"):
            done.setdefault(day[:10], set()).add(sym)
        limit = datetime.fromtimestamp(before / 1000, timezone.utc).strftime("%Y-%m-%d")
        rows = self.query(f"SELECT name, isParquet FROM table_partitions('{TICKS}')")
        return [name for name, parquet in rows
                if not parquet and name < limit and set(symbols) <= done.get(name, set())]

    # Convertește zilele date în Parquet (compresia din server.conf: ZSTD nivel 3). Fără pierderi;
    # zilele rămân în același tabel și se citesc cu același SQL.
    def compress_days(self, days: list[str]):
        if days:
            self.query(f"ALTER TABLE {TICKS} CONVERT PARTITION TO PARQUET LIST "
                       + ", ".join(f"'{d}'" for d in days))

    # ---------- Verificări ----------
    # Câte ticuri are simbolul în [lo_ms, hi_ms).
    def tick_count(self, symbol: str, lo_ms: int, hi_ms: int) -> int:
        rows = self.query(f"SELECT count() FROM {TICKS} WHERE source = '{self.source}' AND symbol = '{symbol}' "
                          f"AND ts >= {int(lo_ms) * 1000} AND ts < {int(hi_ms) * 1000}")
        return rows[0][0] if rows else 0

    # Așteaptă (cel mult `timeout` secunde) ca ticurile scrise în [lo_ms, hi_ms) să se vadă în QuestDB
    # (WAL-ul le aplică cu o mică întârziere). Rândurile scrise au momente distincte, deci tabelul
    # trebuie să aibă cel puțin `expected` ticuri acolo. Altfel: RuntimeError (scrierea s-a pierdut).
    def wait_ticks(self, symbol: str, lo_ms: int, hi_ms: int, expected: int, timeout: float = 180):
        deadline = time.monotonic() + timeout
        while True:
            n = self.tick_count(symbol, lo_ms, hi_ms)
            if n >= expected:
                return
            if time.monotonic() > deadline:
                raise RuntimeError(f"QuestDB nu a aplicat ticurile scrise pentru {symbol} "
                                   f"({n} din {expected} vizibile după {timeout:.0f} s); vezi wal_tables()")
            time.sleep(1)

    # Ticurile live țin pasul? Cel mai nou lot live trimis acum cel puțin max_ms trebuie să se vadă
    # deja în QuestDB (altfel graficele rămân în urmă). Fără ticuri live recente: True.
    def live_ok(self, max_ms: float = 500) -> bool:
        limit = time.time() - max_ms / 1000
        for sent, batch in reversed(self.recent_flushes):
            if sent <= limit:
                if limit - sent > 60:
                    return True
                return all(self.tick_count(sym, t, t + 1) for sym, t in batch.items())
        return True

    # Problemele WAL ale tabelelor noastre (text pentru log / pagină), [] dacă nu e niciuna:
    #   - suspendat: QuestDB a oprit aplicarea tranzacțiilor (fișier WAL corupt etc.);
    #   - jurnal resetat: sequencerTxn < writerTxn. Apare după o oprire bruscă (paginile jurnalului
    #     nu ajunseseră pe disc): QuestDB socotește tranzacțiile noi deja aplicate și le ignoră.
    def wal_problems(self) -> list[str]:
        out = []
        for name, suspended, writer, _buf, seq, _tag, error, *_ in self.query("wal_tables()"):
            if name not in TABLES:
                continue
            if suspended:
                out.append(f"{name}: WAL suspendat ({error or 'fără detalii'})")
            elif seq < writer:
                out.append(f"{name}: jurnalul WAL a fost resetat (sequencer {seq} < writer {writer}); "
                           "scrierile noi nu se aplică")
        return out

    # ---------- Starea colectorului ----------
    def write_status(self, state: str, progress: float = 1.0, gap_from_ms: int | None = None,
                     eta: int = 0, detail: str = ""):
        columns = {"progress": float(progress), "eta": int(eta), "detail": detail}
        if gap_from_ms:
            columns["gap_from"] = micros(gap_from_ms)
        with Sender.from_conf(self.conf) as s:
            s.row(STATUS, symbols={"source": self.source, "state": state}, columns=columns,
                  at=micros(time.time() * 1000))
            s.flush()

    # Ultima stare scrisă de colector: {state, progress, gap_from (ms), eta, detail, ts (ms)} sau None.
    def read_status(self) -> dict | None:
        rows = self.query(f"SELECT ts, state, progress, gap_from, eta, detail FROM {STATUS} "
                          f"WHERE source = '{self.source}' LATEST ON ts PARTITION BY source")
        if not rows:
            return None
        ts, state, progress, gap_from, eta, detail = rows[0]
        return {"ts": iso_ms(ts), "state": state, "progress": progress,
                "gap_from": iso_ms(gap_from) if gap_from else None, "eta": eta, "detail": detail or ""}

    # Marchează o zi: status = complete / empty / partial, cu numărul de ticuri.
    def mark_day(self, symbol: str, day_ms: int, status: str, ticks: int):
        with Sender.from_conf(self.conf) as s:
            s.row(COVERAGE, symbols={"source": self.source, "symbol": symbol, "status": status},
                  columns={"ticks": ticks, "checked": micros(time.time() * 1000)}, at=micros(day_ms))
            s.flush()


# Ora din răspunsul QuestDB („2026-01-02T00:00:00.000000Z”) -> milisecunde UTC.
def iso_ms(text: str) -> int:
    return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000)
