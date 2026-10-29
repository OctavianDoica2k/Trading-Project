"""
Simularea RAPIDĂ pentru optimizare (algoritmul genetic): backtest-ul cu aceleași reguli ca testerul, dar fără
raport complet: doar balanța finală și câteva cifre din tabelul de optimizare al MT5. Datele se încarcă o
singură dată, apoi fiecare set de parametri durează câteva milisecunde, pe toate cele trei modelling-uri.
"""
# =====================================================================================
#  CE FACE ACEST FIȘIER (pe scurt)
# -------------------------------------------------------------------------------------
#  prepare(settings, max_window) -> Prepared: lumânările perioadei (ca backtester.load_period, cu
#    încălzirea pentru cel mai mare InpTLBars din optimizare) și prețurile simulate, o singură dată:
#    - Open prices: cele 4 prețuri OHLC ale fiecărei lumânări (bid / ask / cursul USD), simcore.run_bars;
#    - Real ticks / 1 minute OHLC: toate ticurile (sau cele 4 prețuri ale fiecărui minut), ca întregi în
#      puncte, plus indexul pe minute simcore.build_index (PreparedTicks, simcore.run_blocks). Fișierele
#      stau în tester_cache/index/, iar procesele de lucru le mapează din același loc (fără copii).
#  simulate(prep, inputs) -> dict: un backtest cu parametrii `inputs` (aceleași chei ca la tester).
#    Liniile de trend (strategy_fast.trend_breaks) depind doar de InpTLBars: se păstrează pe fiecare
#    valoare (Prepared.breaks); restul semnalelor (MA) se calculează la fiecare apel.
#  check(settings, inputs) compară simulate cu backtester.run (balanța și fiecare tranzacție).
# =====================================================================================
import json
import math
import os
from collections import OrderedDict
import shutil
import time
import uuid

import numpy as np

import backtester
import simcore
import strategy_fast


class Prepared:
    """Datele unei optimizări: lumânările perioadei și prețurile lor OHLC, calculate o dată."""
    mode = "open"

    def __init__(self, settings: dict, bars: dict, first: int, t, bid, ask, rate, max_window: int):
        self.settings, self.bars, self.first, self.max_window = settings, bars, first, max_window
        self.t, self.bid, self.ask, self.rate = t, bid, ask, rate
        n = len(bars["t"])
        self.js = np.arange(n, dtype=np.int64) * 4
        self.je = self.js + 4
        self.bar_t = np.asarray(bars["t"], np.int64)
        self.c = np.ascontiguousarray(bars["c"], dtype=np.float64)
        self.spec = backtester.SYMBOLS[settings["symbol"]]
        self._breaks: dict[int, tuple] = {}
        self._prices: dict[str, np.ndarray] = {}

    def __getstate__(self):                     # la procesele de lucru: fără cache-ul de semnale
        return {**self.__dict__, "_sig": OrderedDict()}

    def signals(self, period: int, price: str, window: int) -> dict:
        """Semnalele strategiei, sumele cumulate ale ieșirilor pe MA și media pentru SL la intrări (smak, completată
        de simcore.run_blocks la prima intrare pe fiecare lumânare). Depind doar de (MA, preț, TLBars): algoritmul
        genetic testează multe seturi care diferă doar prin RR / Close on MA / risc, deci se păstrează ultimele
        SIGNALS_CACHE combinații."""
        cache = self.__dict__.setdefault("_sig", OrderedDict())
        key = (period, price, window)
        if key in cache:
            cache.move_to_end(key)
            return cache[key]
        b = self.bars
        sig = strategy_fast.signals(b["o"], b["h"], b["l"], b["c"], period, price, window, self.breaks(window))
        n = len(b["c"])
        sig = {**sig, "smak": np.full(n + 1, np.nan),
               "exit_cb": np.r_[0, np.cumsum(sig["exit_buy"][:n])].astype(np.int64),
               "exit_cs": np.r_[0, np.cumsum(sig["exit_sell"][:n])].astype(np.int64)}
        cache[key] = sig
        if len(cache) > SIGNALS_CACHE:
            cache.popitem(last=False)
        return sig

    def breaks(self, window: int) -> tuple:
        if window not in self._breaks:
            b = self.bars
            self._breaks[window] = strategy_fast.trend_breaks(b["h"], b["l"], b["c"], window)
        return self._breaks[window]

    def price(self, name: str) -> np.ndarray:
        if name not in self._prices:
            b = self.bars
            self._prices[name] = np.ascontiguousarray(strategy_fast.price_column(b["o"], b["h"], b["l"], b["c"], name),
                                                      dtype=np.float64)
        return self._prices[name]


class PreparedTicks(Prepared):
    """Datele unei optimizări pe Real ticks / 1 minute OHLC: lumânările, prețurile ca întregi în puncte și
    indexul pe minute. Prețurile și minutele (mari) stau în fișiere mapate în memorie; restul se trimite la
    procesele de lucru (pickle), iar fiecare proces își mapează singur fișierele."""

    def __init__(self, settings: dict, bars: dict, first: int, max_window: int, mode: str, folder, bi, bf, gf, gl):
        self.settings, self.bars, self.first, self.max_window = settings, bars, first, max_window
        self.mode, self.folder, self.bi, self.bf, self.gf, self.gl = mode, str(folder), bi, bf, gf, gl
        self.bar_t = np.asarray(bars["t"], np.int64)
        self.c = np.ascontiguousarray(bars["c"], dtype=np.float64)
        self.spec = backtester.SYMBOLS[settings["symbol"]]
        self._breaks, self._prices, self._arrays = {}, {}, None

    def __getstate__(self):
        return {**self.__dict__, "_arrays": None, "_sig": OrderedDict()}

    @property
    def arrays(self) -> tuple:
        """(ki, kf, segs, păstrate): minutele și ticurile, mapate din fișiere (o singură dată pe proces)."""
        if self._arrays is None:
            self._arrays = index_arrays(self.folder)
        return self._arrays


INDEX = backtester.CACHE / "index"
SIGNALS_CACHE = 48            # combinații (MA, preț, TLBars) păstrate cu semnalele lor, pe proces


INDEX_KEEP = 4                # indexuri păstrate pe disc (cele folosite cel mai recent)
INDEX_FRESH = 3600            # s: cât se refolosește indexul unei perioade care încă se schimbă (până azi)
INDEX_VERSION = 3             # formatul indexului (în nume): cele vechi nu se mai refolosesc


def _new_index_folder(name: str):
    """Un folder nou în tester_cache/index/; cele vechi se șterg (doar cele pe care nu le mai mapează nimeni)."""
    INDEX.mkdir(parents=True, exist_ok=True)
    for old in sorted(INDEX.iterdir(), key=lambda f: f.stat().st_mtime)[:-(INDEX_KEEP - 1)]:
        shutil.rmtree(old, ignore_errors=True)
    folder = INDEX / f"{name}_{uuid.uuid4().hex[:8]}"
    folder.mkdir()
    return folder


def _find_index(key: str, stable: bool):
    """Cel mai nou index complet cu cheia key: oricât de vechi dacă perioada nu se mai schimbă (stable), altfel doar
    dacă e din ultima oră (aceeași perioadă cerută din nou, ex. testele pornite din tabelul de optimizare)."""
    if not INDEX.exists():
        return None
    found = sorted((f for f in INDEX.iterdir() if f.name.startswith(key + "_") and (f / "ok").exists()),
                   key=lambda f: f.stat().st_mtime, reverse=True)
    for f in found:
        if stable or time.time() - (f / "ok").stat().st_mtime < INDEX_FRESH:
            try:
                with open(f / "segs.json") as fh:
                    if all(os.path.exists(p) for seg in json.load(fh) for p in seg[0]):
                        return f
            except (OSError, ValueError):
                pass
    return None


def tick_index(data, settings: dict, bars: dict, first: int, end: int, m1: dict, log) -> tuple:
    """Indexul pe minute al lumânărilor first ... n-1 (Real ticks / 1 minute OHLC): de pe disc dacă există, altfel
    construit (simcore.build_index) și păstrat. Întoarce (folder, bi, bt, bf, gf, gl); minutele și ticurile (mari) se
    mapează din fișiere cu index_arrays. Ticurile nu se copiază: segmentele indexului sunt fișierele raw/ ale lunilor
    terminate; doar luna neterminată (și prețurile 1 minute OHLC) se scriu în folderul indexului."""
    mode = settings["modelling"]
    symbol, tf = settings["symbol"], backtester.TFS[settings["tf"]]
    spec = backtester.SYMBOLS[symbol]
    point, scale = 10.0 ** -spec["digits"], 10 ** spec["digits"]
    n = len(bars["t"])
    t0s = bars["t"][first:].astype(np.int64) * 1000
    t1s = np.minimum(t0s + tf * 1000, end)
    lo_ms, hi_ms = int(t0s[0]), int(t1s.max())
    # O perioadă cu toate zilele complete în QuestDB (și lunile terminate pe disc) nu se mai schimbă.
    stable = hi_ms <= data.done_hi or all(data._cacheable(y, m) for y, m in backtester.months(lo_ms, hi_ms))
    key = f"v{INDEX_VERSION}_{symbol}_{settings['tf']}_{mode}_{lo_ms}_{hi_ms}_{len(t0s)}"
    found = _find_index(key, stable)
    if found is not None:
        os.utime(found)
        log("Folosesc indexul pe minute păstrat pe disc")
        return (str(found), *(np.load(found / f"{k}.npy") for k in ("bi", "bt", "bf", "gf", "gl")))
    folder = _new_index_folder(key)
    segs = []              # (fișierele t / b / s, începutul segmentului în ms, ticurile lui [i, j) din fișiere)

    def own(name, off, bp, sp, parts):
        """Prețuri scrise în folderul indexului (aceleași 3 fișiere pentru toate segmentele parts = [(început, i, j)]:
        pe Windows, fiecare fișier nou se deschide greu prima dată)."""
        paths = [str(folder / f"{name}.{k}.npy") for k in ("t", "b", "s")]
        for p, arr in zip(paths, (off, bp, sp)):
            np.save(p, arr)
        segs.extend((paths, base, i, j) for base, i, j in parts)

    if mode == "real":
        # Ticurile perioadei testate: lunile terminate din raw/ (mapate), luna neterminată din .npz / QuestDB.
        for y, m in backtester.months(lo_ms, hi_ms):
            base = backtester.month_range(y, m)[0]
            if data.month_raw(y, m) is not None:
                segs.append(([str(p) for p in data.raw_paths(y, m)], base, 0, None))
                continue
            t, b, a = data.month_ticks(y, m, need_hi=hi_ms)
            bp, ap = np.rint(b * scale), np.rint(a * scale)
            if len(t) and not (np.array_equal(bp / scale, b) and np.array_equal(ap / scale, a)):
                raise ValueError("Ticurile nu sunt multipli exacți de punct")
            own(f"{y:04d}-{m:02d}", (t - base).astype(np.uint32), bp.astype(np.int32), (ap - bp).astype(np.int16),
                [(base, 0, len(t))])
        ask_exact = True
    else:
        # 1 minute OHLC: cele (cel mult) 4 prețuri ale fiecărui minut, ca în backtester.run, câte o lună pe segment.
        i0, i1 = int(bars["i0"][first]), int(bars["i1"][n - 1])
        s_ = slice(i0, i1)
        up = backtester.bar_up(m1["o"], m1["c"])[s_]
        t, bid, _ = backtester.ohlc_points(m1["t"][s_], m1["o"][s_], m1["h"][s_], m1["l"][s_], m1["c"][s_],
                                           m1["v"][s_], m1["spr"][s_], up, 60, point, True)
        v = m1["v"][s_]
        sp = np.repeat(m1["spr"][s_], np.where(v == 1, 1, np.where(v == 2, 2, 4))).astype(np.int16)
        bp = np.rint(bid * scale).astype(np.int32)
        if not np.array_equal(bp / scale, bid):
            raise ValueError("Prețurile M1 nu sunt multipli exacți de punct")
        t = np.asarray(t, np.int64)
        off, parts = np.zeros(len(t), np.uint32), []
        for y, m in backtester.months(lo_ms, hi_ms):
            base, end_m = backtester.month_range(y, m)
            i, j = int(np.searchsorted(t, base)), int(np.searchsorted(t, end_m))
            off[i:j] = t[i:j] - base
            parts.append((base, i, j))
        own("ohlc", off, bp, sp, parts)
        ask_exact = False
    with open(folder / "segs.json", "w") as f:
        json.dump(segs, f)
    seg, _keep = _segments(folder)
    log(f"Construiesc indexul pe minute ({int(seg[:, 3].sum()):,} de prețuri)...".replace(",", "."))
    bi, bt, bf, ki, kf, gf, gl = simcore.build_index(seg, t0s, t1s, scale, point, ask_exact)
    for name, arr in (("ki", ki), ("kf", kf), ("bi", bi), ("bt", bt), ("bf", bf), ("gf", gf), ("gl", gl)):
        np.save(folder / f"{name}.npy", arr)
    (folder / "ok").touch()                       # scris complet: se poate refolosi
    return str(folder), bi, bt, bf, gf, gl


def _segments(folder) -> tuple:
    """Segmentele indexului (segs.json), mapate din fișiere: (segs pentru simcore, tablourile mapate). Tablourile
    trebuie păstrate cât timp se folosesc adresele lor. Segmentele goale se sar."""
    with open(os.path.join(folder, "segs.json")) as f:
        segs = json.load(f)
    maps, rows = {}, []
    for paths, base, i, j in segs:
        arrs = [maps[p] if p in maps else maps.setdefault(p, np.load(p, mmap_mode="r")) for p in paths]
        j = len(arrs[0]) if j is None else j
        if j <= i:
            continue
        assert [a.dtype for a in arrs] == [np.uint32, np.int32, np.int16] and all(a.flags.c_contiguous for a in arrs)
        rows.append([a.ctypes.data + i * a.itemsize for a in arrs] + [j - i, base])
    return np.array(rows, np.int64).reshape(-1, 5), list(maps.values())


def index_arrays(folder: str) -> tuple:
    """(ki, kf, segs, păstrate): minutele indexului și ticurile segmentelor, mapate din fișiere (fără copii).
    „păstrate” = tablourile mapate: trebuie ținute cât timp se folosesc segs (adresele lor)."""
    seg, keep = _segments(folder)
    ki, kf = (np.load(os.path.join(folder, f"{k}.npy"), mmap_mode="r") for k in ("ki", "kf"))
    return np.asarray(ki), np.asarray(kf), seg, keep


def _prepare_ticks(settings: dict, max_window: int, db, log, progress) -> PreparedTicks:
    mode = settings["modelling"]
    if settings.get("currency", "USD") != "USD":
        raise ValueError("Optimizarea pe Real ticks / 1 minute OHLC merge deocamdată doar cu cont în USD")
    data = backtester.Data(db, settings["symbol"], log, progress)
    start, end, lo, m1, bars, first = backtester.load_period(data, settings, max_window, log)
    folder, bi, _bt, bf, gf, gl = tick_index(data, settings, bars, first, end, m1, log)
    return PreparedTicks({**settings, "start_ms": start, "end_ms": end}, bars, first, max_window, mode, folder,
                         bi, bf, gf, gl)


def prepare(settings: dict, max_window: int, db=None, log=print, progress=None) -> Prepared:
    """Încarcă perioada o singură dată (ca backtester.run), cu încălzirea pentru max_window lumânări.
    Cu mai multă încălzire decât are nevoie un InpTLBars mai mic, rezultatele lui rămân aceleași."""
    from dotenv import load_dotenv
    load_dotenv(backtester.ROOT / ".env")
    if db is None:
        from tickdb import TickDB
        db = TickDB(backtester.SOURCE, log=lambda *_: None)
    if settings["modelling"] in ("real", "ohlc1"):
        return _prepare_ticks(settings, max_window, db, log, progress)
    if settings["modelling"] != "open":
        raise ValueError(f"Modelling necunoscut: {settings['modelling']}")
    symbol, tf = settings["symbol"], backtester.TFS[settings["tf"]]
    spec = backtester.SYMBOLS[symbol]
    point = 10.0 ** -spec["digits"]
    data = backtester.Data(db, symbol, log, progress)
    start, end, lo, _m1, bars, first = backtester.load_period(data, settings, max_window, log)
    up = backtester.bar_up(bars["o"], bars["c"])
    t, bid, ask = backtester.ohlc_points(bars["t"], bars["o"], bars["h"], bars["l"], bars["c"], bars["v"],
                                         bars["spr"], up, tf, point, False)
    eur = None
    if settings.get("currency", "USD") == "EUR" and symbol != "EURUSD":
        e = backtester.Data(db, "EURUSD", log).m1(lo, end, " (curs EUR)", ticks_from_ms=math.inf)
        eur = (e["t"] * 1000, e["c"])
    acct = backtester.Account(float(settings["deposit"]), settings.get("currency", "USD"), int(settings["leverage"]),
                              spec, symbol, eur)
    rate = np.ascontiguousarray(np.broadcast_to(acct.usd_rate(t, bid), bid.shape), dtype=np.float64)
    return Prepared({**settings, "start_ms": start, "end_ms": end}, bars, first, np.asarray(t, np.int64),
                    np.asarray(bid, np.float64), np.asarray(ask, np.float64), rate, max_window)


def simulate(prep: Prepared, inputs: dict, trades: bool = False) -> dict:
    """Un backtest cu parametrii `inputs` (pe modelling-ul lui prep): balanța finală + cifrele din tabelul de optimizare."""
    period, price = int(inputs["ma_period"]), inputs["ma_price"]
    window, rr = int(inputs["tl_bars"]), float(inputs["rr"])
    if window > prep.max_window:
        raise ValueError(f"InpTLBars = {window} > {prep.max_window} (încălzirea pregătită)")
    b, spec, s = prep.bars, prep.spec, prep.settings
    n = len(b["t"])
    sig = prep.signals(period, price, window)
    deposit = float(s["deposit"])
    contract = float(spec["contract"])
    S = simcore.new_state(deposit)
    if prep.mode == "open":
        rows, _ = simcore.run_bars(
            S, prep.first, n, prep.js[prep.first:], prep.je[prep.first:], prep.bar_t, prep.t, prep.bid, prep.ask,
            prep.rate, sig["entry"], sig["exit_buy"], sig["exit_sell"], bool(inputs["close_on_ma"]), prep.price(price),
            prep.c, period, window, rr, 10.0 ** -spec["digits"], 10.0 ** spec["digits"], contract, spec["vol_min"],
            spec["vol_step"], spec["vol_max"], float(s["leverage"]), float(inputs["risk_pct"]), backtester.STOP_OUT,
            False, False)
        profits = np.concatenate([rows[:, simcore.T_PROFIT], simcore.finish(S, contract)[:, simcore.T_PROFIT]])
    else:
        ki, kf, segs, _keep = prep.arrays
        profits = simcore.run_blocks(
            S, prep.first, n, prep.bi, prep.bf, ki, kf, prep.gf, prep.gl, segs, 10 ** spec["digits"],
            10.0 ** -spec["digits"], prep.mode == "real", sig["entry"], sig["exit_cb"], sig["exit_cs"],
            bool(inputs["close_on_ma"]), sig["smak"], prep.price(price), period, window, prep.c, rr,
            10.0 ** spec["digits"], contract, spec["vol_min"],
            spec["vol_step"], spec["vol_max"], float(s["leverage"]), float(inputs["risk_pct"]), backtester.STOP_OUT,
            prep.mode == "real")
    balance = float(S[simcore.BAL])
    gross_p = float(profits[profits > 0].sum())
    gross_l = float(profits[profits < 0].sum())
    net = round(balance - deposit, 2)
    eq_dd = float(S[simcore.EQ_DD + 2])
    out = {"balance": balance, "profit": net, "trades": int(len(profits)),
           "profit_factor": round(gross_p / -gross_l, 2) if gross_l < 0 else None,
           "expected_payoff": round(net / len(profits), 2) if len(profits) else 0.0,
           "equity_dd": round(eq_dd, 2), "equity_dd_pct": round(float(S[simcore.EQ_DD + 4]), 2),
           "balance_dd": round(float(S[simcore.BAL_DD + 2]), 2),
           "recovery": round(net / eq_dd, 2) if eq_dd > 0 else None}
    if trades:
        out["profits"] = profits
    return out


def check(settings: dict, inputs: dict, prep: Prepared | None = None, db=None, log=print) -> dict:
    """Compară simulate cu backtester.run (testerul): balanța și profitul fiecărei tranzacții."""
    prep = prep or prepare(settings, int(inputs["tl_bars"]), db, log=lambda *_: None)
    t0 = time.perf_counter()
    fast = simulate(prep, inputs, trades=True)
    ms = (time.perf_counter() - t0) * 1000
    ref = backtester.run(settings, inputs, log=lambda *_: None)
    ref_profits = np.array([t["profit"] for t in ref["trades"]])
    same = len(ref_profits) == len(fast["profits"]) and bool(np.array_equal(ref_profits, fast["profits"]))
    ref_balance = ref["trades"][-1]["balance_after"] if ref["trades"] else float(settings["deposit"])
    res = {"ok": same and ref_balance == fast["balance"], "balance_fast": fast["balance"],
           "balance_run": ref_balance, "trades_fast": fast["trades"], "trades_run": len(ref_profits), "ms": round(ms, 1)}
    log(res)
    return res
