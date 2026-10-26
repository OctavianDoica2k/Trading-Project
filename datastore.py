"""
Lumânările ÎNCHISE ale fiecărui simbol / timeframe (1s–W1), citite din QuestDB de pe mașina
virtuală. Indicatorii și chartul (lumânările închise) citesc doar de aici, prin load_bars, ca să
vadă garantat aceleași date.
"""
# =====================================================================================
#  CE FACE ACEST FIȘIER (pe scurt)
# -------------------------------------------------------------------------------------
#  - M1–W1: lumânările oficiale cTrader din tabelul bars. Regulile le aplică colectorul, pe VM
#    (vezi collector.py, punctul 5): ultimele 10.000 de lumânări închise, adăugate la fiecare
#    închidere, ultimele 5 verificate și rescrise dacă brokerul le corectează, completare spre trecut.
#  - 1s–30s: lumânările calculate de QuestDB din ticurile salvate (SAMPLE BY, vezi tickdb.py):
#    ultimele 10.000 de lumânări închise; în memorie se țin cel mult 2 × 10.000 (cele mai noi).
#  Aici doar le citim:
#   - prima citire a unei serii aduce lumânările ei în memorie;
#   - fiecare sincronizare (la închiderea unei lumânări) recitește doar capătul: ultimele
#     VERIFY_BARS lumânări din memorie și tot ce e mai nou; dacă ceva s-a schimbat, `version`
#     crește (cheia cache-ului de indicatori din server.py).
#  Nu mai există fișiere CSV pe PC: vechiul folder datastore/ nu mai e folosit.
# =====================================================================================
import asyncio
import time
from datetime import datetime, timezone

from tickdb import BARS, BAR_STATUS, TickDB

STORE_BARS = 10_000
VERIFY_BARS = 5
LABELS = {0: "Ticuri", 1: "1s", 5: "5s", 15: "15s", 30: "30s", 60: "M1", 300: "M5", 900: "M15",
          1800: "M30", 3600: "H1", 14400: "H4", 86400: "D1", 604800: "W1"}
# Sub un minut, câte lumânări ținem cel mult în memorie (cele mai noi).
SUB_MAX = 2 * STORE_BARS


# Secunde UTC -> text: 1767225600 -> „2026-01-01 00:00:00”.
def utc_text(t: int) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class Store:
    """Seria de lumânări a unei perechi sursă / simbol / timeframe, ținută și în memorie."""

    def __init__(self, db: TickDB, source: str, name: str, tf: int):
        self.db, self.source, self.name, self.tf, self.label = db, source, name, tf, LABELS[tf]
        # Unde sunt datele (afișat în pagină, la trecerea mouse-ului peste starea datastore-ului).
        self.where = (f"QuestDB {db.host} · {BARS} · {name} {self.label}" if tf >= 60
                      else f"QuestDB {db.host} · ticks → o lumânare pe tic · {name}" if tf == 0
                      else f"QuestDB {db.host} · ticks → lumânări {self.label} · {name}")
        self.lock = asyncio.Lock()
        self.bars: list[dict] | None = None
        self.meta: dict = {}           # first_bar, history_complete (din bar_status)
        self._added = 0                # lumânări noi la ultima sincronizare
        self.version = 0
        self.status: dict = {"state": "idle"}

    # ---------- Citire din QuestDB ----------
    def _select(self, since: int | None) -> list[dict]:
        now = time.time()
        if self.tf == 0:
            return self._tick_bars(since, int(now * 1000) + 1)
        if self.tf < 60:
            # Prima dată: ultimele STORE_BARS (+1, lumânarea în curs, scoasă mai jos); apoi capătul.
            hi = int(now * 1_000_000) + 1
            bars = (self.db.last_sampled(self.name, self.tf, hi, STORE_BARS + 1) if since is None
                    else self.db.sample(self.name, self.tf, since * 1_000_000, hi))
            return [b for b in bars if b["time"] + self.tf <= now]
        sql = (f"SELECT cast(ts AS long), open, high, low, close, volume FROM {BARS} "
               f"WHERE source = '{self.source}' AND symbol = '{self.name}' AND tf = {self.tf}")
        if since is not None:
            sql += f" AND ts >= {since * 1_000_000}"
        rows = self.db.query(sql + " ORDER BY ts")
        return [{"time": t // 1_000_000, "open": o, "high": h, "low": lo, "close": c, "volume": v}
                for t, o, h, lo, c, v in rows if t // 1_000_000 + self.tf <= now]

    # „Ticuri”: o lumânare pe tic (bid), de la bid-ul ticului anterior (open) la bid-ul lui
    # (close), ca pe chart (app.js, tickBar). `time` = ora ticului în MILISECUNDE (două ticuri pot
    # cădea în aceeași secundă). Prima dată ultimele STORE_BARS ticuri, apoi cele de la `since` (ms).
    def _tick_bars(self, since: int | None, hi_ms: int) -> list[dict]:
        if since is None:
            rows = self.db.last_ticks(self.name, hi_ms, STORE_BARS + 1)
            prev, rows = (rows[0][1], rows[1:]) if rows else (None, rows)
        else:
            before = self.db.last_ticks(self.name, since, 1)
            prev = before[0][1] if before else None
            rows = self.db.query(f"SELECT cast(ts AS long), bid, ask FROM ticks WHERE source = '{self.source}' "
                                 f"AND symbol = '{self.name}' AND ts >= {since * 1000} ORDER BY ts")
            rows = [[t // 1000, b, a] for t, b, a in rows]
        out = []
        for t, bid, _ask in rows:
            o = bid if prev is None else prev
            out.append({"time": t, "open": o, "high": max(o, bid), "low": min(o, bid), "close": bid, "volume": 1})
            prev = bid
        return out

    def _complete(self) -> bool:
        if self.tf < 60:   # din ticuri: „tot istoricul” = mai puțin de STORE_BARS de la primul tic
            return len(self.bars or []) < STORE_BARS
        rows = self.db.query(f"SELECT complete FROM {BAR_STATUS} WHERE source = '{self.source}' "
                             f"AND series = '{self.name}:{self.tf}' LATEST ON updated PARTITION BY series")
        return bool(rows and rows[0][0])

    async def unload(self):
        async with self.lock:
            self.bars = None

    # ---------- Citire (pentru chart și indicatori) ----------
    async def load_bars(self, n: int | None = None) -> list[dict]:
        async with self.lock:
            bars = self.bars or []
            return list(bars[-n:] if n else bars)

    # Cel mult n lumânări deschise înainte de `before` (secunde), pentru derularea chartului.
    async def load_before(self, before: int, n: int) -> list[dict]:
        async with self.lock:
            if self.bars is None:
                self.bars = await asyncio.to_thread(self._select, None)
            bars = self.bars
            lo, hi = 0, len(bars)
            while lo < hi:                       # căutare binară: prima lumânare cu time >= before
                mid = (lo + hi) // 2
                if bars[mid]["time"] < before:
                    lo = mid + 1
                else:
                    hi = mid
            return bars[max(0, lo - n):lo]

    def last_time(self) -> int | None:
        return self.bars[-1]["time"] if self.bars else None

    # ---------- Progres ----------
    def _set(self, report, state: str, error: str = "", phase: str = "", progress: float = 1.0):
        bars = self.bars or []
        complete = bool(self.meta.get("history_complete"))
        self.status = {
            "state": state, "phase": phase, "progress": progress,
            "pct": 1.0 if complete or len(bars) >= STORE_BARS else round(len(bars) / STORE_BARS, 4),
            "bars": len(bars), "target": STORE_BARS, "complete": complete,
            # Pagina primește orele în secunde (pe „Ticuri” lumânările au ora în milisecunde).
            "first": bars[0]["time"] // (1000 if self.tf == 0 else 1) if bars else None,
            "last": bars[-1]["time"] // (1000 if self.tf == 0 else 1) if bars else None,
            "file": self.where, "error": error,
        }
        if report:
            report(dict(self.status))

    # ---------- Sincronizare ----------
    async def sync(self, feed=None, digits: int = 5, report=None) -> bool:
        """Recitește lumânările noi (sau toate, prima dată). Întoarce True dacă s-a schimbat ceva."""
        async with self.lock:
            self._added = 0
            try:
                if self.bars is None:
                    self._set(report, "syncing", phase="read", progress=0.0)
                    bars = await asyncio.to_thread(self._select, None)
                    if not bars:
                        raise ConnectionError(f"colectorul n-a salvat încă lumânări {self.label} pentru "
                                              f"{self.name} în QuestDB")
                    self.bars, self._added, changed = bars, len(bars), True
                else:
                    k = min(VERIFY_BARS, len(self.bars))
                    since = self.bars[-k]["time"] if k else None
                    fresh = await asyncio.to_thread(self._select, since)
                    old_tail = self.bars[-k:] if k else []
                    keep = self.bars[:-k] if k else self.bars
                    changed = fresh != old_tail
                    if changed:
                        self._added = max(0, len(fresh) - len(old_tail))
                        self.bars = keep + fresh
                        if self.tf < 60 and len(self.bars) > SUB_MAX:
                            self.bars = self.bars[-STORE_BARS:]
                self.meta["history_complete"] = await asyncio.to_thread(self._complete)
                self.meta["first_bar"] = utc_text(self.bars[0]["time"] // (1000 if self.tf == 0 else 1)) if self.bars else ""
                if changed:
                    self.version += 1
                self._set(report, "ready")
                return changed
            except Exception as e:  # noqa: BLE001 - pagina primește eroarea ca text
                self._set(report, "error", error=str(e) or type(e).__name__)
                raise


class Datastore:
    """Toate seriile citite; câte un obiect Store pentru fiecare pereche folosită."""

    def __init__(self, db: TickDB):
        self.db = db
        self.stores: dict[tuple[str, str, int], Store] = {}

    # Același obiect pentru aceeași pereche = același lock și aceleași lumânări în memorie.
    def get(self, source: str, name: str, tf: int) -> Store:
        key = (source, name, tf)
        if key not in self.stores:
            self.stores[key] = Store(self.db, source, name, tf)
        return self.stores[key]
