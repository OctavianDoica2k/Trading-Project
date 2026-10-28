"""
Strategy Tester: backtest-ul strategiei din strategy.py, cu aceleași reguli ca Strategy Tester-ul din
MetaTrader 5 și ca EA-ul mt5/MQL5/Experts/Nexus/Nexus_MATrend_EA.mq5. Rulează într-un PROCES separat
(vezi server.py), ca prețurile live să nu încetinească.
"""
# =====================================================================================
#  CE FACE ACEST FIȘIER (pe scurt)
# -------------------------------------------------------------------------------------
#  1. Datele: ticurile (bid / ask) din QuestDB, păstrate și pe disc, în tester_cache/, pe luni
#     (ca fișierele .tkc ale MT5: prima rulare le descarcă, următoarele le citesc de pe disc).
#     Din ticuri se fac lumânările M1 (bid, cu spread-ul minim din minut) și din ele lumânările
#     timeframe-ului testat. Lunile doar de încălzire (dinaintea startului) nu au nevoie de ticuri:
#     lumânările lor M1 le calculează direct QuestDB (SAMPLE BY 1m), aceleași valori, mult mai repede.
#  2. Semnalele strategiei: strategy_fast.signals (aceeași logică ca strategy.py, calculată
#     incremental). SL / TP vin din strategy.transaction_limits, funcția ORIGINALĂ.
#  3. Prețurile din interiorul lumânării, după „Modelling” (ca în MT5):
#     - real  = „Every tick based on real ticks”: toate ticurile reale; SL / TP se execută la
#               prețul ticului care le atinge (bid pentru buy, ask pentru sell);
#     - ohlc1 = „1 minute OHLC”: 4 prețuri pe fiecare lumânare M1: Open, apoi Low și High (lumânare
#               urcătoare: Open→Low→High→Close; coborâtoare: Open→High→Low→Close; doji: invers
#               față de lumânarea anterioară); ask = bid + spread-ul minutului. Cu 1 tic în minut doar
#               Close, cu 2 doar Open și Close. SL / TP se execută la prețul din ordin;
#     - open  = „Open prices only”: strategia rulează doar la Open; SL / TP / stop out se verifică pe
#               cele 4 prețuri OHLC ale lumânării testate (ascunse strategiei), la prețul din ordin.
#  4. Ca EA-ul: o dată pe lumânare, la primul tic: întâi SL / TP / stop out pe acel tic, apoi
#     închiderea pe MA (InpCloseOnMA), apoi, fără poziție, intrarea (o singură poziție). Lotul: la
#     SL se pierde InpRiskPct % din balanță, limitat de marja liberă (95%). La final, poziția rămasă
#     se închide („end of test”), ca în MT5.
#  5. Statisticile raportului (tester_report.py) și graficul balanță / equity.
# =====================================================================================
import calendar
import math
import sys
import time
import types
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

import strategy_fast
import tester_report

ROOT = Path(__file__).parent
CACHE = ROOT / "tester_cache"
SOURCE = "ctrader"

# Specificația simbolurilor (ca în MT5: Symbol → Specification). Profitul e în USD la ambele.
SYMBOLS = {
    "EURUSD": {"digits": 5, "contract": 100_000, "vol_min": 0.01, "vol_step": 0.01, "vol_max": 500.0,
               "margin": "forex"},
    "XAUUSD": {"digits": 2, "contract": 100, "vol_min": 0.01, "vol_step": 0.01, "vol_max": 500.0,
               "margin": "cfd"},
}
STOP_OUT = 50.0          # %: sub acest nivel al marjei, poziția se închide forțat („so”)
TFS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400}
# H4 la cTrader începe la 01:00 New York (17:00 NY + 4 ore × n): {tf: ora locală NY de aliniere, în s}.
NY_ALIGNED = {14400: 3600}
MODES = {"real": "Every tick based on real ticks", "ohlc1": "1 minute OHLC", "open": "Open prices only"}
CURRENCIES = ("USD", "EUR")
DAY = 86400


class Cancelled(Exception):
    pass


# ---------- strategy.py, neschimbat ----------
# strategy.py face `from analysis import medie_mobila, trace`, dar partea 1 din analysis.py citește
# un CSV de pe alt calculator. Ca la indicatori (indicators.analysis_part2), încărcăm doar partea a
# 2-a ca modul „analysis”, apoi importăm strategy.py exact cum e scris. (Doar în procesul testerului.)
def load_strategy():
    import importlib
    import indicators
    if "analysis" not in sys.modules or not hasattr(sys.modules["analysis"], "trace"):
        mod = types.ModuleType("analysis")
        mod.__file__ = str(indicators.ANALYSIS)
        exec(compile(indicators.analysis_part2(), str(indicators.ANALYSIS), "exec"), mod.__dict__)
        sys.modules["analysis"] = mod
    import strategy
    return importlib.reload(strategy)


# ---------- Date ----------
def ms_text(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")


def months(lo_ms: int, hi_ms: int) -> list[tuple[int, int]]:
    d = datetime.fromtimestamp(lo_ms / 1000, timezone.utc)
    y, m = d.year, d.month
    out = []
    while True:
        start = calendar.timegm((y, m, 1, 0, 0, 0)) * 1000
        if start >= hi_ms:
            return out
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def month_range(y: int, m: int) -> tuple[int, int]:
    lo = calendar.timegm((y, m, 1, 0, 0, 0)) * 1000
    y2, m2 = (y + 1, 1) if m == 12 else (y, m + 1)
    return lo, calendar.timegm((y2, m2, 1, 0, 0, 0)) * 1000


class Data:
    """Ticurile și lumânările M1 ale unui simbol, din QuestDB, păstrate pe disc pe luni."""

    def __init__(self, db, symbol: str, log, progress=None):
        self.db, self.symbol, self.log, self.progress = db, symbol, log, progress
        self.point = 10.0 ** -SYMBOLS[symbol]["digits"] if symbol in SYMBOLS else 1e-5
        self.dir = CACHE / SOURCE / symbol
        self.dir.mkdir(parents=True, exist_ok=True)
        self._ticks: dict[tuple[int, int], tuple] = {}
        self.done_hi = 0
        self.cov = self.coverage()

    # Zilele cu ticuri complete în QuestDB: (prima zi, sfârșitul ultimei zile), în ms.
    def coverage(self) -> tuple[int, int] | None:
        rows = self.db.query(f"SELECT min(day), max(day) FROM tick_coverage WHERE source = '{SOURCE}' "
                             f"AND symbol = '{self.symbol}' AND status = 'complete'")
        if not rows or rows[0][0] is None:
            return None
        lo = int(datetime.fromisoformat(rows[0][0].replace("Z", "+00:00")).timestamp() * 1000)
        hi = int(datetime.fromisoformat(rows[0][1].replace("Z", "+00:00")).timestamp() * 1000) + DAY * 1000
        self.done_hi = hi                  # sfârșitul ultimei zile complete: până aici ticurile nu se mai schimbă
        return lo, max(hi, int(time.time() * 1000))

    # O lună se păstrează pe disc doar dacă e întreagă în zilele colectate și s-a terminat de 2 zile.
    def _cacheable(self, y: int, m: int) -> bool:
        lo, hi = month_range(y, m)
        return self.cov is not None and lo >= self.cov[0] and hi <= time.time() * 1000 - 2 * DAY * 1000

    def _fetch_ticks(self, lo: int, hi: int) -> tuple:
        rows = self.db.query(f"SELECT cast(ts AS long), bid, ask FROM ticks WHERE source = '{SOURCE}' AND "
                             f"symbol = '{self.symbol}' AND ts >= {lo * 1000} AND ts < {hi * 1000} ORDER BY ts",
                             timeout=600)
        if not rows:
            return np.empty(0, np.int64), np.empty(0), np.empty(0)
        a = np.array(rows, dtype=np.float64)
        return (np.array([r[0] for r in rows], dtype=np.int64) // 1000, a[:, 1], a[:, 2])

    # Lumânările M1 din [lo, hi) calculate direct în QuestDB (SAMPLE BY), fără să descarce ticurile:
    # aceleași valori ca m1_from_ticks (bid; spread-ul minim din minut), de ~100 de ori mai puține rânduri.
    def _fetch_m1(self, lo: int, hi: int) -> dict:
        rows = self.db.query(f"SELECT cast(ts AS long), first(bid), max(bid), min(bid), last(bid), count(), "
                             f"min(ask - bid) FROM ticks WHERE source = '{SOURCE}' AND symbol = '{self.symbol}' "
                             f"AND ts >= {lo * 1000} AND ts < {hi * 1000} SAMPLE BY 1m ALIGN TO CALENDAR",
                             timeout=600)
        if not rows:
            return m1_from_ticks(np.empty(0, np.int64), np.empty(0), np.empty(0), self.point)
        a = np.array([r[1:] for r in rows], dtype=np.float64)
        return {"t": np.array([r[0] for r in rows], dtype=np.int64) // 1_000_000, "o": a[:, 0], "h": a[:, 1],
                "l": a[:, 2], "c": a[:, 3], "v": a[:, 4].astype(np.int64),
                "spr": np.rint(a[:, 5] / self.point).astype(np.int64)}

    # Ticurile unei luni (timp ms, bid, ask): de pe disc sau din QuestDB, zi cu zi (4 zile în paralel).
    # Luna neterminată se păstrează pe disc până la ultima zi completă (ticks_….part.npz, cu „upto”),
    # ca la rularea următoare să se descarce doar zilele noi.
    def month_ticks(self, y: int, m: int, report=None) -> tuple:
        key = (y, m)
        if key in self._ticks:
            return self._ticks[key]
        path = self.dir / f"ticks_{y:04d}-{m:02d}.npz"
        part = self.dir / f"ticks_{y:04d}-{m:02d}.part.npz"
        if path.exists():
            z = np.load(path)
            out = (z["t"], z["bid"], z["ask"])
        else:
            lo, hi = month_range(y, m)
            if self.cov:
                lo, hi = max(lo, self.cov[0]), min(hi, self.cov[1])
            parts, start = [], lo
            if part.exists():
                z = np.load(part)
                start = max(lo, min(int(z["upto"]), hi))
                parts.append((z["t"], z["bid"], z["ask"]))
            days = list(range(start, hi, DAY * 1000))
            with ThreadPoolExecutor(4) as ex:
                futs = [ex.submit(self._fetch_ticks, d, min(d + DAY * 1000, hi)) for d in days]
                for i, f in enumerate(futs):
                    parts.append(f.result())
                    if report:
                        report((i + 1) / len(days))
            out = tuple(np.concatenate([p[j] for p in parts]) if parts else np.empty(0) for j in range(3))
            out = (out[0].astype(np.int64), out[1].astype(np.float64), out[2].astype(np.float64))
            if self._cacheable(y, m):
                np.savez_compressed(path, t=out[0], bid=out[1], ask=out[2])
                part.unlink(missing_ok=True)
            else:
                upto = max(lo, min(hi, self.done_hi) // (DAY * 1000) * DAY * 1000)
                if upto > start:
                    n = int(np.searchsorted(out[0], upto))
                    np.savez_compressed(part, t=out[0][:n], bid=out[1][:n], ask=out[2][:n], upto=upto)
        if len(self._ticks) >= 2:                     # în memorie: cel mult 2 luni de ticuri
            self._ticks.pop(next(iter(self._ticks)))
        self._ticks[key] = out
        return out

    # Lumânările M1 dintr-o lună, făcute din ticuri: timp (s), o, h, l, c (bid), ticuri, spread minim (puncte).
    # Cu ticks=False (lunile doar de încălzire, fără simulare) le calculează QuestDB, fără ticuri.
    def month_m1(self, y: int, m: int, report=None, ticks: bool = True) -> dict:
        path = self.dir / f"m1_{y:04d}-{m:02d}.npz"
        if path.exists():
            z = np.load(path)
            return {k: z[k] for k in z.files}
        if ticks or (y, m) in self._ticks or (self.dir / f"ticks_{y:04d}-{m:02d}.npz").exists():
            t, bid, ask = self.month_ticks(y, m, report)
            out = m1_from_ticks(t, bid, ask, self.point)
        else:
            lo, hi = month_range(y, m)
            if self.cov:
                lo, hi = max(lo, self.cov[0]), min(hi, self.cov[1])
            out = self._fetch_m1(lo, hi)
        if self._cacheable(y, m):
            np.savez_compressed(path, **out)
        return out

    # Lumânările M1 din [lo_ms, hi_ms). Lunile terminate înainte de ticks_from_ms (încălzirea) vin ca M1
    # calculat în QuestDB; restul, din ticuri (care rămân pe disc pentru simulare).
    def m1(self, lo_ms: int, hi_ms: int, label: str = "", ticks_from_ms: float | None = None) -> dict:
        ms = months(lo_ms, hi_ms)
        parts = []
        for i, (y, m) in enumerate(ms):
            ticks = ticks_from_ms is None or month_range(y, m)[1] > ticks_from_ms
            cached = (self.dir / f"m1_{y:04d}-{m:02d}.npz").exists()
            if not cached:
                what = "ticurile" if ticks else "lumânările M1 (încălzire)"
                self.log(f"Descarc {what} {self.symbol} {y:04d}-{m:02d} din QuestDB{label}...")
            rep = (lambda f, i=i: self.progress("data", (i + f) / len(ms))) if self.progress else None
            parts.append(self.month_m1(y, m, rep, ticks))
            if self.progress:
                self.progress("data", (i + 1) / len(ms))
        keys = ("t", "o", "h", "l", "c", "v", "spr")
        m1 = {k: np.concatenate([p[k] for p in parts]) if parts else np.empty(0) for k in keys}
        sel = (m1["t"] * 1000 >= lo_ms) & (m1["t"] * 1000 < hi_ms)
        return {k: a[sel] for k, a in m1.items()}

    # Ticurile din [lo_ms, hi_ms) (pot fi în două luni).
    def ticks(self, lo_ms: int, hi_ms: int) -> tuple:
        out = []
        for y, m in months(lo_ms, hi_ms):
            t, b, a = self.month_ticks(y, m)
            i, j = np.searchsorted(t, lo_ms), np.searchsorted(t, hi_ms)
            out.append((t[i:j], b[i:j], a[i:j]))
        if len(out) == 1:
            return out[0]
        return tuple(np.concatenate([p[k] for p in out]) for k in range(3))


def m1_from_ticks(t: np.ndarray, bid: np.ndarray, ask: np.ndarray, point: float) -> dict:
    if len(t) == 0:
        return {k: np.empty(0, dt) for k, dt in (("t", np.int64), ("o", float), ("h", float), ("l", float),
                                                    ("c", float), ("v", np.int64), ("spr", np.int64))}
    minute = t // 60_000
    starts = np.r_[0, np.nonzero(np.diff(minute))[0] + 1]
    ends = np.r_[starts[1:], len(t)]
    spr = np.rint((ask - bid) / point).astype(np.int64)
    return {"t": minute[starts] * 60, "o": bid[starts], "h": np.maximum.reduceat(bid, starts),
            "l": np.minimum.reduceat(bid, starts), "c": bid[ends - 1], "v": (ends - starts).astype(np.int64),
            "spr": np.minimum.reduceat(spr, starts)}


# Diferența ora New York − UTC (secunde), pentru fiecare moment t (s): −4 h vara (din a doua
# duminică din martie, 02:00 local, până în prima duminică din noiembrie, 02:00 local), altfel −5 h.
def ny_offset(t: np.ndarray) -> np.ndarray:
    out = np.full(len(t), -5 * 3600, dtype=np.int64)
    years = {datetime.fromtimestamp(int(x), timezone.utc).year for x in (t.min(), t.max())}
    for y in range(min(years), max(years) + 1):
        mar = [d for d in range(8, 15) if calendar.weekday(y, 3, d) == 6][0]
        nov = [d for d in range(1, 8) if calendar.weekday(y, 11, d) == 6][0]
        lo = calendar.timegm((y, 3, mar, 7, 0, 0))
        hi = calendar.timegm((y, 11, nov, 6, 0, 0))
        out[(t >= lo) & (t < hi)] = -4 * 3600
    return out


def bars_from_m1(m1: dict, tf: int) -> dict:
    """Lumânările timeframe-ului testat din M1 (+ unde începe fiecare în M1)."""
    if tf == 60:
        return {**m1, "i0": np.arange(len(m1["t"])), "i1": np.arange(1, len(m1["t"]) + 1)}
    if not len(m1["t"]):
        return {k: np.empty(0) for k in ("t", "o", "h", "l", "c", "v", "spr", "i0", "i1")}
    # H4 la cTrader: lumânări de 4 ore numărate de la 17:00 New York (01:00 / 05:00 ... UTC vara, 02:00 / 06:00 iarna).
    ny = ny_offset(m1["t"]) if tf in NY_ALIGNED else 0
    key = (m1["t"] + ny - NY_ALIGNED.get(tf, 0)) // tf
    starts = np.r_[0, np.nonzero(np.diff(key))[0] + 1]
    ends = np.r_[starts[1:], len(key)]
    t0 = key[starts] * tf + NY_ALIGNED.get(tf, 0) - (ny[starts] if tf in NY_ALIGNED else 0)
    return {"t": t0, "o": m1["o"][starts], "h": np.maximum.reduceat(m1["h"], starts),
            "l": np.minimum.reduceat(m1["l"], starts), "c": m1["c"][ends - 1],
            "v": np.add.reduceat(m1["v"], starts), "spr": np.minimum.reduceat(m1["spr"], starts),
            "i0": starts, "i1": ends}


# Lumânare urcătoare? Doji (open = close): invers față de lumânarea anterioară (regula MT5).
def bar_up(o: np.ndarray, c: np.ndarray) -> np.ndarray:
    up = c > o
    doji = c == o
    prev_up = np.r_[False, c[:-1] > o[:-1]]
    return np.where(doji, ~prev_up, up)


# Cele (cel mult) 4 prețuri OHLC ale unor lumânări, în ordinea MT5; ask = bid + spread.
def ohlc_points(t, o, h, l, c, v, spr, up, dur: int, point: float, use_volume: bool):
    n = len(o)
    p = np.empty((n, 4))
    p[:, 0] = o
    p[:, 1] = np.where(up, l, h)
    p[:, 2] = np.where(up, h, l)
    p[:, 3] = c
    ts = (t[:, None] * 1000 + np.array([0, dur * 250, dur * 500, dur * 1000 - 1])[None, :])
    keep = np.ones((n, 4), dtype=bool)
    if use_volume:
        keep[v == 1] = [False, False, False, True]
        keep[v == 2] = [True, False, False, True]
    bid = p[keep]
    ask = bid + np.repeat(spr, keep.sum(axis=1)) * point
    return ts[keep], bid, ask


# ---------- Contul ----------
class Account:
    def __init__(self, deposit: float, currency: str, leverage: int, spec: dict, symbol: str, eur=None):
        self.balance = self.initial = float(deposit)
        self.currency, self.leverage, self.spec, self.symbol = currency, leverage, spec, symbol
        self.eur = eur          # (timp ms, EURUSD) pentru conversia USD -> EUR, dacă simbolul nu e EURUSD
        self.point = 10.0 ** -spec["digits"]

    # Câți bani din contul de depozit valorează 1 USD (vectorial).
    def usd_rate(self, t_ms, bid):
        if self.currency == "USD":
            return 1.0
        if self.symbol == "EURUSD":
            return 1.0 / bid
        t_arr, rate = self.eur
        i = np.clip(np.searchsorted(t_arr, t_ms, side="right") - 1, 0, len(rate) - 1)
        return 1.0 / rate[i]

    def margin(self, lots, price, t_ms, bid):
        # Forex: contract în moneda de bază, convertit; CFD: contract × preț. În USD ambele = lots × contract × preț.
        return lots * self.spec["contract"] * price / self.leverage * self.usd_rate(t_ms, bid)

    def normalize(self, lots: float) -> float:
        s = self.spec
        lots = math.floor(lots / s["vol_step"] + 1e-9) * s["vol_step"]
        return round(max(s["vol_min"], min(s["vol_max"], lots)), 8)

    # Ca LotsFor din EA: la SL se pierde risk_pct % din balanță, limitat de marja liberă (95%).
    def lots_for(self, entry: float, sl: float, risk_pct: float, t_ms: int, bid: float) -> float:
        tick_value = self.spec["contract"] * self.point * self.usd_rate(t_ms, bid)
        per_lot = abs(entry - sl) / self.point * tick_value
        if per_lot <= 0:
            return 0.0
        lots = self.normalize(self.balance * risk_pct / 100.0 / per_lot)
        free = self.balance
        while lots > 0 and self.margin(lots, entry, t_ms, bid) > free * 0.95:
            if lots <= self.spec["vol_min"]:
                return 0.0
            lots = self.normalize(lots * 0.8)
        return lots


# ---------- Rularea ----------
# Perioada testată și lumânările ei, cu încălzirea pentru fereastra strategiei (InpTLBars) înainte de
# start, ca indicatorul din MT5. Întoarce (start, end, lo, m1, bars, first); first = prima lumânare testată.
def load_period(data: Data, settings: dict, window: int, say) -> tuple:
    if data.cov is None:
        raise ValueError(f"Nu există ticuri complete pentru {data.symbol} în QuestDB")
    tf = TFS[settings["tf"]]
    start = int(settings["start_ms"])
    end = int(settings["end_ms"])
    say(f"{data.symbol} {settings['tf']}, {MODES[settings['modelling']]}, {ms_text(start)} → {ms_text(end)} "
        f"(UTC; data de final exclusă)")
    if start < data.cov[0]:
        say(f"Ticurile încep la {ms_text(data.cov[0])}: începutul testului se mută acolo")
        start = data.cov[0]
    end = min(end, int(time.time() * 1000))
    if end <= start:
        raise ValueError("Perioada aleasă nu are date (data de final e înaintea datelor disponibile)")
    warm_days = math.ceil(window * tf / DAY * 7 / 5 * 1.3) + 3
    lo = max(data.cov[0], start - warm_days * DAY * 1000)
    lo = lo // (tf * 1000) * tf * 1000
    m1 = data.m1(lo, end, ticks_from_ms=start)
    if not len(m1["t"]):
        raise ValueError("Nu există lumânări M1 în perioada aleasă")
    bars = bars_from_m1(m1, tf)
    first = int(np.searchsorted(bars["t"] * 1000, start))
    if first < window:
        say(f"Înainte de start sunt doar {first} lumânări (InpTLBars = {window}): la început fereastra e mai scurtă")
    n = len(bars["t"])
    if first >= n:
        raise ValueError("Nu există lumânări în perioada aleasă")
    say(f"{n - first:,} lumânări {settings['tf']} testate (+{first:,} de încălzire), {len(m1['t']):,} lumânări M1".replace(",", "."))
    return start, end, lo, m1, bars, first


def run(settings: dict, inputs: dict, progress=None, log=print, cancelled=lambda: False) -> dict:
    """Un backtest complet. progress(fază, 0..1); întoarce raportul (statistici, grafic, tranzacții)."""
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from tickdb import TickDB

    t_start = time.time()
    journal: list[str] = []

    def say(msg: str):
        journal.append(f"{datetime.now().strftime('%H:%M:%S')}  {msg}")
        log(msg)

    def prog(phase, f):
        if progress:
            progress(phase, f)
        if cancelled():
            raise Cancelled()

    symbol = settings["symbol"]
    if symbol not in SYMBOLS:
        raise ValueError(f"Simbol necunoscut: {symbol} (cunoscute: {', '.join(SYMBOLS)})")
    tf = TFS[settings["tf"]]
    mode = settings["modelling"]
    if mode not in MODES:
        raise ValueError(f"Modelling necunoscut: {mode}")
    spec = SYMBOLS[symbol]
    point = 10.0 ** -spec["digits"]
    period, price = int(inputs["ma_period"]), inputs["ma_price"]
    window, rr = int(inputs["tl_bars"]), float(inputs["rr"])
    close_on_ma, risk_pct = bool(inputs["close_on_ma"]), float(inputs["risk_pct"])
    if price not in strategy_fast.PRICES:
        raise ValueError(f"Prețul MA necunoscut: {price}")
    if period < 1 or window < 1:
        raise ValueError("Perioada MA și numărul de lumânări trebuie să fie cel puțin 1")

    strategy = load_strategy()
    db = TickDB(SOURCE, log=lambda *_: None)
    data = Data(db, symbol, say, prog)
    start, end, lo, m1, bars, first = load_period(data, settings, window, say)
    n = len(bars["t"])

    eur = None
    if settings.get("currency", "USD") == "EUR" and symbol != "EURUSD":
        e = Data(db, "EURUSD", say).m1(lo, end, " (curs EUR)", ticks_from_ms=math.inf)   # doar Close: fără ticuri
        eur = (e["t"] * 1000, e["c"])
    acct = Account(float(settings["deposit"]), settings.get("currency", "USD"), int(settings["leverage"]), spec,
                   symbol, eur)

    prog("signals", 0.0)
    sig = strategy_fast.signals(bars["o"], bars["h"], bars["l"], bars["c"], period, price, window)
    prog("signals", 1.0)

    up_m1 = bar_up(m1["o"], m1["c"])
    up_bar = bar_up(bars["o"], bars["c"])

    # Prețurile lumânării k: (timp ms, bid, ask); prima = primul tic al lumânării.
    def points(k: int, only_first: bool = False):
        t0 = int(bars["t"][k]) * 1000
        t1 = min(t0 + tf * 1000, end)
        if mode == "real":
            t, b, a = data.ticks(t0, t1)
            return (t[:1], b[:1], a[:1]) if only_first else (t, b, a)
        if mode == "open":
            if only_first:
                return (np.array([t0]), bars["o"][k:k + 1], bars["o"][k:k + 1] + bars["spr"][k:k + 1] * point)
            return ohlc_points(bars["t"][k:k + 1], bars["o"][k:k + 1], bars["h"][k:k + 1], bars["l"][k:k + 1],
                               bars["c"][k:k + 1], bars["v"][k:k + 1], bars["spr"][k:k + 1], up_bar[k:k + 1],
                               tf, point, False)
        i0, i1 = int(bars["i0"][k]), int(bars["i1"][k])
        if only_first:
            i1 = i0 + 1
        sl_ = slice(i0, i1)
        t, b, a = ohlc_points(m1["t"][sl_], m1["o"][sl_], m1["h"][sl_], m1["l"][sl_], m1["c"][sl_], m1["v"][sl_],
                              m1["spr"][sl_], up_m1[sl_], 60, point, True)
        if only_first:
            return t[:1], b[:1], a[:1]
        return t, b, a

    # ---------- Starea simulării ----------
    deals: list[dict] = []
    trades: list[dict] = []
    pos: dict | None = None
    eq_stats = tester_report.Drawdown(acct.initial)
    bal_stats = tester_report.Drawdown(acct.initial)
    min_margin_level = None
    graph: list[list] = []          # [timp s, balanță, equity minim, equity, încărcare %]
    skipped = {"wrong_side": 0, "no_lots": 0}

    def deal(t_ms, kind, direction, lots, px, profit, comment):
        deals.append({"time": int(t_ms), "deal": len(deals) + 2, "type": kind, "entry": direction,
                      "volume": lots, "price": px, "profit": round(profit, 2), "balance": round(acct.balance, 2),
                      "comment": comment})

    def close(t_ms, px, bid_now, comment):
        nonlocal pos
        sign = 1 if pos["dir"] > 0 else -1
        per_price = pos["lots"] * spec["contract"] * acct.usd_rate(t_ms, bid_now)   # bani pe o unitate de preț
        profit = round(float((px - pos["price"]) * sign * per_price), 2)
        acct.balance = round(acct.balance + profit, 2)
        bal_stats.update(np.array([acct.balance]))
        deal(t_ms, "sell" if pos["dir"] > 0 else "buy", "out", pos["lots"], px, profit, comment)
        trades.append({"dir": pos["dir"], "open_time": pos["time"], "close_time": int(t_ms), "lots": pos["lots"],
                       "open": pos["price"], "close": px, "sl": pos["sl"], "tp": pos["tp"], "profit": profit,
                       "balance_before": pos["balance_before"], "balance_after": acct.balance,
                       "mfe": round(float(max(pos["mfe"], (px - pos["price"]) * sign) * per_price), 2),
                       "mae": round(float(min(pos["mae"], (px - pos["price"]) * sign) * per_price), 2),
                       "comment": comment})
        pos = None

    # SL / TP / stop out pe prețurile t, b, a (poziția deschisă); actualizează equity și drawdown-ul.
    def walk(t, b, a):
        nonlocal min_margin_level
        if pos is None or not len(t):
            return None
        sign = pos["dir"]
        px = b if sign > 0 else a                       # prețul la care s-ar închide poziția
        rate = acct.usd_rate(t, b)
        floating = (px - pos["price"]) * sign * pos["lots"] * spec["contract"] * rate
        eq = acct.balance + floating
        margin = np.broadcast_to(acct.margin(pos["lots"], pos["price"], t, b), eq.shape)
        level = eq / margin * 100
        if sign > 0:
            hit_sl, hit_tp = b <= pos["sl"], b >= pos["tp"]
        else:
            hit_sl, hit_tp = a >= pos["sl"], a <= pos["tp"]
        so = level <= STOP_OUT
        any_hit = hit_sl | hit_tp | so
        stop = int(np.argmax(any_hit)) if any_hit.any() else len(t)
        upto = slice(0, stop + 1 if stop < len(t) else len(t))
        eq_seen = eq[upto]
        eq_stats.update(eq_seen)
        lv = float(level[upto].min())
        min_margin_level = lv if min_margin_level is None else min(min_margin_level, lv)
        fav = (px[upto] - pos["price"]) * sign
        pos["mfe"] = max(pos["mfe"], float(fav.max()))
        pos["mae"] = min(pos["mae"], float(fav.min()))
        pos["load"] = max(pos.get("load", 0.0), float((margin[upto] / np.maximum(eq_seen, 1e-9) * 100).max()))
        if stop >= len(t):
            return float(eq[-1]), float(eq_seen.min())
        i = stop
        if so[i] and not (hit_sl[i] or hit_tp[i]):
            close(t[i], float(px[i]), float(b[i]), f"so {lv:.1f}%")
        else:
            which = "sl" if hit_sl[i] else "tp"
            level_px = pos[which]
            fill = float(px[i]) if mode == "real" else level_px
            close(t[i], fill, float(b[i]), f"{which} {level_px:.{spec['digits']}f}")
        return acct.balance, float(eq_seen.min())

    names = {1: "buy", -1: "sell"}
    last_point = None
    total = n - first
    report_every = max(1, total // 200)
    for idx, k in enumerate(range(first, n)):
        if idx % report_every == 0:
            prog("run", idx / total)
        need_all = pos is not None
        t, b, a = points(k, only_first=not need_all)
        if not len(t):
            continue
        eq_min = None
        # 1. Primul tic: SL / TP / stop out pentru poziția existentă.
        if pos is not None:
            r = walk(t[:1], b[:1], a[:1])
            eq_min = r[1] if r else None
        t0, b0, a0 = int(t[0]), float(b[0]), float(a[0])
        # 2. Închiderea pe MA (lumânarea precedentă închisă de partea cealaltă a MA).
        if pos is not None and close_on_ma:
            if (pos["dir"] > 0 and sig["exit_buy"][k]) or (pos["dir"] < 0 and sig["exit_sell"][k]):
                close(t0, b0 if pos["dir"] > 0 else a0, b0, "ma")
        # 3. Intrarea, doar fără poziție.
        if pos is None and sig["entry"][k] != 0:
            d = int(sig["entry"][k])
            df = strategy_fast.frame(bars["o"], bars["h"], bars["l"], bars["c"], max(0, k - window), k)
            lim = strategy.transaction_limits(df, period, price, names[d], rr)
            entry = a0 if d > 0 else b0
            if lim is None:
                skipped["wrong_side"] += 1
            else:
                tp, sl, _op = (float(x) for x in lim)
                ok = (entry - sl >= point and tp > entry) if d > 0 else (sl - entry >= point and tp < entry)
                lots = acct.lots_for(entry, sl, risk_pct, t0, b0) if ok else 0.0
                if not ok:
                    skipped["wrong_side"] += 1
                elif lots <= 0:
                    skipped["no_lots"] += 1
                else:
                    sl = round(sl, spec["digits"])
                    tp = round(tp, spec["digits"])
                    pos = {"dir": d, "lots": lots, "price": entry, "sl": sl, "tp": tp, "time": t0,
                           "balance_before": acct.balance, "mfe": 0.0, "mae": 0.0}
                    deal(t0, names[d], "in", lots, entry, 0.0, "")
        # 4. Restul prețurilor lumânării, cât timp există o poziție.
        if pos is not None:
            if not need_all:
                t, b, a = points(k)
            r = walk(t[1:], b[1:], a[1:])
            if r:
                eq_min = r[1] if eq_min is None else min(eq_min, r[1])
        last_point = (int(t[-1]), float(b[-1]), float(a[-1]))
        # Graficul: balanța, equity-ul minim din lumânare, equity-ul la sfârșitul ei, încărcarea depozitului.
        if pos is not None:
            px = last_point[1] if pos["dir"] > 0 else last_point[2]
            eq_end = acct.balance + (px - pos["price"]) * pos["dir"] * pos["lots"] * spec["contract"] * \
                acct.usd_rate(last_point[0], last_point[1])
            load = pos.get("load", 0.0)
        else:
            eq_end, load = acct.balance, 0.0
        graph.append([int(bars["t"][k]), acct.balance, round(min(eq_min, eq_end) if eq_min is not None else eq_end, 2),
                      round(float(eq_end), 2), round(load, 2)])
        if pos is not None:
            pos["load"] = 0.0
    if pos is not None and last_point:
        tl, bl, al = last_point
        close(tl, bl if pos["dir"] > 0 else al, bl, "end of test")
        graph.append([int(tl // 1000), acct.balance, acct.balance, acct.balance, 0.0])
    if mode == "real":
        # Ticurile reale din perioadă (MT5 le numără pe toate, nu doar pe cele cu poziție deschisă).
        ticks_total = int(m1["v"][int(bars["i0"][first]):].sum())
    elif mode == "ohlc1":
        v = m1["v"][int(bars["i0"][first]):]
        ticks_total = int(np.where(v == 1, 1, np.where(v == 2, 2, 4)).sum())
    else:
        ticks_total = 4 * total
    prog("run", 1.0)
    if skipped["wrong_side"]:
        say(f"{skipped['wrong_side']} semnale fără tranzacție: SL / TP de partea greșită a prețului de intrare")
    if skipped["no_lots"]:
        say(f"{skipped['no_lots']} semnale fără tranzacție: marjă liberă insuficientă pentru lotul minim")
    say(f"Gata în {time.time() - t_start:.1f} s: {len(trades)} tranzacții")

    quality = data_quality(db, symbol, start, end)
    stats = tester_report.stats(acct.initial, trades, deals, eq_stats, bal_stats, min_margin_level,
                                bars=total, ticks=ticks_total, quality=quality)
    return {
        "settings": {**settings, "start_ms": start, "end_ms": end}, "inputs": inputs, "stats": stats,
        "graph": tester_report.downsample(graph, 4000), "deals": deals, "trades": trades,
        "journal": journal, "digits": spec["digits"], "currency": acct.currency,
        "forward_ms": settings.get("forward_ms"),
    }


# Calitatea istoricului: zilele din perioadă rezolvate în QuestDB (complete sau fără ticuri, ex.
# weekend) din totalul zilelor.
def data_quality(db, symbol: str, start: int, end: int) -> float:
    lo = start // (DAY * 1000) * DAY * 1000
    days = max(1, math.ceil((end - lo) / (DAY * 1000)))
    rows = db.query(f"SELECT count() FROM tick_coverage WHERE source = '{SOURCE}' AND symbol = '{symbol}' "
                    f"AND status IN ('complete', 'empty') AND day >= {lo * 1000} AND day < {end * 1000}")
    done = rows[0][0] if rows else 0
    return round(min(100.0, done / days * 100), 2)


# ---------- Punctul de intrare în procesul separat (server.py) ----------
# shared = dicționar comun cu serverul (multiprocessing.Manager): phase, progress, log, cancel.
def job(kind: str, settings: dict, inputs: dict, samples: int, shared) -> dict:
    def progress(phase, f):
        shared["phase"], shared["progress"] = phase, round(float(f), 4)

    def log(msg):
        shared["log"] = [*shared["log"], f"{datetime.now().strftime('%H:%M:%S')}  {msg}"][-300:]

    def cancelled():
        return bool(shared.get("cancel"))

    if kind == "verify":
        return verify(settings, inputs, samples, progress, log, cancelled)
    return run(settings, inputs, progress, log, cancelled)


# ---------- Verificarea copiei rapide ----------
def verify(settings: dict, inputs: dict, samples: int, progress=None, log=print, cancelled=lambda: False) -> dict:
    """Compară semnalele copiei rapide cu strategy.py (original) pe `samples` lumânări din perioadă:
    jumătate dintre cele cu semnal, restul la întâmplare."""
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from tickdb import TickDB

    def prog(phase, f):
        if progress:
            progress(phase, f)
        if cancelled():
            raise Cancelled()

    strategy = load_strategy()
    symbol, tf = settings["symbol"], TFS[settings["tf"]]
    period, price, window = int(inputs["ma_period"]), inputs["ma_price"], int(inputs["tl_bars"])
    data = Data(TickDB(SOURCE, log=lambda *_: None), symbol, log, prog)
    if data.cov is None:
        raise ValueError(f"Nu există ticuri complete pentru {symbol} în QuestDB")
    start = max(int(settings["start_ms"]), data.cov[0])
    end = min(int(settings["end_ms"]), int(time.time() * 1000))
    warm_days = math.ceil(window * tf / DAY * 7 / 5 * 1.3) + 3
    lo = max(data.cov[0], start - warm_days * DAY * 1000) // (tf * 1000) * tf * 1000
    bars = bars_from_m1(data.m1(lo, end, ticks_from_ms=start), tf)
    first = int(np.searchsorted(bars["t"] * 1000, start))
    n = len(bars["t"])
    if first >= n:
        raise ValueError("Nu există lumânări în perioada aleasă")
    sig = strategy_fast.signals(bars["o"], bars["h"], bars["l"], bars["c"], period, price, window)
    rng = np.random.default_rng(int(settings.get("seed", 7)))
    with_signal = np.nonzero(sig["entry"][first:n])[0] + first
    half = min(len(with_signal), samples // 2)
    ks = list(rng.choice(with_signal, half, replace=False)) if half else []
    rest = np.setdiff1d(np.arange(first, n), ks)
    ks += list(rng.choice(rest, min(len(rest), samples - half), replace=False))
    ks = sorted(int(k) for k in ks)
    t0 = time.time()
    res = strategy_fast.verify_samples(strategy, bars["o"], bars["h"], bars["l"], bars["c"], period, price, window,
                                       ks, lambda i, tot: prog("verify", i / tot))
    for r in res:
        r["time"] = int(bars["t"][r["k"]])
    bad = [r for r in res if not r["ok"]]
    return {"checked": len(res), "mismatches": len(bad), "results": res, "seconds": round(time.time() - t0, 1),
            "signals_in_period": int(len(with_signal))}
