"""
Simularea RAPIDĂ pentru optimizare (algoritmul genetic): același backtest ca backtester.run în modul
„Open prices only”, dar compilat (numba) și fără raport complet: doar balanța finală și câteva cifre
din tabelul de optimizare al MT5. Datele se încarcă o singură dată, apoi fiecare set de parametri
durează câteva milisecunde.
"""
# =====================================================================================
#  CE FACE ACEST FIȘIER (pe scurt)
# -------------------------------------------------------------------------------------
#  prepare(settings, max_window) -> Prepared: lumânările perioadei (ca backtester.load_period, cu
#    încălzirea pentru cel mai mare InpTLBars din optimizare) și cele 4 prețuri OHLC ale fiecărei
#    lumânări (bid / ask / cursul USD), calculate o singură dată.
#  simulate(prep, inputs) -> dict: un backtest cu parametrii `inputs` (aceleași chei ca la tester).
#    - liniile de trend (strategy_fast.trend_breaks) depind doar de InpTLBars: se păstrează pe
#      fiecare valoare (Prepared.breaks), restul semnalelor (MA) se calculează la fiecare apel;
#    - SL / TP: aceleași operații ca strategy.transaction_limits (medie_mobila pe fereastra
#      lumânării, pas cu pas), dar compilate, doar la intrările posibile (semnal fără poziție);
#    - bucla de lumânări (_run): aceleași reguli și aceleași operații în virgulă mobilă ca
#      backtester.run (SL / TP / stop out pe cele 4 prețuri, închiderea pe MA, lotul, rotunjirile
#      round(x, 2) ca în Python), deci aceeași balanță la cent.
#  check(settings, inputs) compară simulate cu backtester.run (balanța și fiecare tranzacție).
# =====================================================================================
import math
import time

import numpy as np
from numba import njit

import backtester
import strategy_fast


# ---------- round(x, d) din Python, în numba ----------
# Python rotunjește corect valoarea zecimală exactă a lui x (jumătate -> par). x * 10^d are o eroare
# de rotunjire; o recuperăm exact (Dekker: produs fără eroare), ca să decidem corect la limită.
@njit(cache=True, inline="always")
def _split(a):
    t = 134217729.0 * a                     # 2^27 + 1
    hi = t - (t - a)
    return hi, a - hi


@njit(cache=True)
def _round(x, scale):
    """round(x, d) cu scale = 10.0 ** d (d >= 0)."""
    y = x * scale
    if not math.isfinite(y):
        return x
    ah, al = _split(x)
    bh, bl = _split(scale)
    err = ((ah * bh - y) + ah * bl + al * bh) + al * bl     # x * scale = y + err, exact
    q = np.rint(y)
    d = (y - q) + err
    if d > 0.5 or (d == 0.5 and q % 2 != 0):
        q += 1.0
    elif d < -0.5 or (d == -0.5 and q % 2 != 0):
        q -= 1.0
    return q / scale


@njit(cache=True)
def _normalize(lots, vol_min, vol_step, vol_max):
    # Account.normalize. round(v, 8) e exact aici: v = k × vol_step e departe de jumătăți la 8 zecimale.
    lots = math.floor(lots / vol_step + 1e-9) * vol_step
    return _round(max(vol_min, min(vol_max, lots)), 1e8)


# ---------- SL / TP: strategy.transaction_limits ----------
@njit(cache=True)
def _limits(d, k, p, c, period, window, rr):
    """(ok, sl, tp) la deschiderea lui k, direcția d; ok = False când transaction_limits întoarce None.
    medie_mobila(df)[-1] pe df = lumânările max(0, k - window) ... k - 1, cu aceleași operații.
    Se apelează doar la intrările posibile (fără poziție), nu la fiecare semnal."""
    s = max(0, k - window)
    val = 0.0
    for j in range(period):
        val += p[s + j]
    sma = val / period
    for i in range(period, k - s):
        sma = (sma * period + p[s + i] - p[s + i - period]) / period
    op = c[k - 1]
    if d > 0 and op > sma:
        return True, sma, op + abs(op - sma) * rr
    if d < 0 and op < sma:
        return True, sma, op - abs(op - sma) * rr
    return False, sma, 0.0


# ---------- Bucla de lumânări: backtester.run, modul „open” ----------
@njit(cache=True)
def _dd(st, o, v):
    """tester_report.Drawdown.update pe o valoare; st[o:o+6] = peak, min, max_dd, max_dd_pct, rel_pct, rel_money."""
    peak = max(st[o], v)
    dd = peak - v
    if dd > st[o + 2]:
        st[o + 2] = dd
        st[o + 3] = dd / peak * 100 if peak > 0 else 0.0
    rel = dd / peak * 100 if peak > 0 else 0.0
    if rel > st[o + 4]:
        st[o + 4] = rel
        st[o + 5] = dd
    st[o] = peak
    st[o + 1] = min(st[o + 1], v)


@njit(cache=True)
def _run(first, n, entry, exit_buy, exit_sell, close_on_ma, p, c, period, window, rr, bid, ask, rate,
         point, price_scale, contract, vol_min, vol_step, vol_max, leverage, deposit, risk_pct, stop_out):
    balance = deposit
    # Equity: st[0:6], balanța: st[6:12] (vezi _dd); st[12] = nivelul minim al marjei.
    st = np.array([deposit, deposit, 0.0, 0.0, 0.0, 0.0, deposit, deposit, 0.0, 0.0, 0.0, 0.0, np.inf])
    profits = np.zeros(n + 1)
    n_tr = 0
    has = False
    pdir, plots, pprice, psl, ptp = 0, 0.0, 0.0, 0.0, 0.0
    for k in range(first, n):
        for phase in range(3):
            # 0 = primul preț (SL / TP), 1 = închiderea pe MA și intrarea, 2 = restul prețurilor.
            if phase == 1:
                b0, a0, r0 = bid[k, 0], ask[k, 0], rate[k, 0]
                if has and close_on_ma and ((pdir > 0 and exit_buy[k]) or (pdir < 0 and exit_sell[k])):
                    px = b0 if pdir > 0 else a0
                    profit = _round((px - pprice) * pdir * (plots * contract * r0), 100.0)
                    balance = _round(balance + profit, 100.0)
                    _dd(st, 6, balance)
                    profits[n_tr] = profit
                    n_tr += 1
                    has = False
                if not has and entry[k] != 0:
                    d = entry[k]
                    ent = a0 if d > 0 else b0
                    good, sl, tp = _limits(d, k, p, c, period, window, rr)
                    if good:
                        good = (ent - sl >= point and tp > ent) if d > 0 else (sl - ent >= point and tp < ent)
                    if good:
                        # Account.lots_for
                        tick_value = contract * point * r0
                        per_lot = abs(ent - sl) / point * tick_value
                        lots = 0.0
                        if per_lot > 0:
                            lots = _normalize(balance * risk_pct / 100.0 / per_lot, vol_min, vol_step, vol_max)
                            while lots > 0 and lots * contract * ent / leverage * r0 > balance * 0.95:
                                if lots <= vol_min:
                                    lots = 0.0
                                    break
                                lots = _normalize(lots * 0.8, vol_min, vol_step, vol_max)
                        if lots > 0:
                            has = True
                            pdir, plots, pprice = d, lots, ent
                            psl, ptp = _round(sl, price_scale), _round(tp, price_scale)
                continue
            if not has:
                continue
            # walk: SL / TP / stop out pe prețurile j0 ... j1 - 1.
            j0, j1 = (0, 1) if phase == 0 else (1, 4)
            margin_base = plots * contract * pprice / leverage
            for j in range(j0, j1):
                b, a, r = bid[k, j], ask[k, j], rate[k, j]
                px = b if pdir > 0 else a
                eq = balance + (px - pprice) * pdir * plots * contract * r
                level = eq / (margin_base * r) * 100
                if pdir > 0:
                    hit_sl, hit_tp = b <= psl, b >= ptp
                else:
                    hit_sl, hit_tp = a >= psl, a <= ptp
                _dd(st, 0, eq)
                st[12] = min(st[12], level)
                if hit_sl or hit_tp or level <= stop_out:
                    fill = px if not (hit_sl or hit_tp) else (psl if hit_sl else ptp)
                    profit = _round((fill - pprice) * pdir * (plots * contract * r), 100.0)
                    balance = _round(balance + profit, 100.0)
                    _dd(st, 6, balance)
                    profits[n_tr] = profit
                    n_tr += 1
                    has = False
                    break
    if has:                                   # „end of test”: ultimul preț al ultimei lumânări
        px = bid[n - 1, 3] if pdir > 0 else ask[n - 1, 3]
        profit = _round((px - pprice) * pdir * (plots * contract * rate[n - 1, 3]), 100.0)
        balance = _round(balance + profit, 100.0)
        _dd(st, 6, balance)
        profits[n_tr] = profit
        n_tr += 1
    return balance, profits[:n_tr], st


# ---------- Interfața ----------
class Prepared:
    """Datele unei optimizări: lumânările perioadei și prețurile lor OHLC, calculate o dată."""

    def __init__(self, settings: dict, bars: dict, first: int, bid, ask, rate, max_window: int):
        self.settings, self.bars, self.first, self.max_window = settings, bars, first, max_window
        self.bid, self.ask, self.rate = bid, ask, rate
        self.spec = backtester.SYMBOLS[settings["symbol"]]
        self._breaks: dict[int, tuple] = {}
        self._prices: dict[str, np.ndarray] = {}

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


def prepare(settings: dict, max_window: int, db=None, log=print, progress=None) -> Prepared:
    """Încarcă perioada o singură dată (ca backtester.run), cu încălzirea pentru max_window lumânări.
    Cu mai multă încălzire decât are nevoie un InpTLBars mai mic, rezultatele lui rămân aceleași."""
    from dotenv import load_dotenv
    load_dotenv(backtester.ROOT / ".env")
    if db is None:
        from tickdb import TickDB
        db = TickDB(backtester.SOURCE, log=lambda *_: None)
    if settings["modelling"] != "open":
        raise ValueError("Simularea rapidă există doar pentru „Open prices only”")
    symbol, tf = settings["symbol"], backtester.TFS[settings["tf"]]
    spec = backtester.SYMBOLS[symbol]
    point = 10.0 ** -spec["digits"]
    data = backtester.Data(db, symbol, log, progress)
    start, end, lo, _m1, bars, first = backtester.load_period(data, settings, max_window, log)
    n = len(bars["t"])
    up = backtester.bar_up(bars["o"], bars["c"])
    t4, bid, ask = backtester.ohlc_points(bars["t"], bars["o"], bars["h"], bars["l"], bars["c"], bars["v"],
                                          bars["spr"], up, tf, point, False)
    eur = None
    if settings.get("currency", "USD") == "EUR" and symbol != "EURUSD":
        e = backtester.Data(db, "EURUSD", log).m1(lo, end, " (curs EUR)", ticks_from_ms=math.inf)
        eur = (e["t"] * 1000, e["c"])
    acct = backtester.Account(float(settings["deposit"]), settings.get("currency", "USD"), int(settings["leverage"]),
                              spec, symbol, eur)
    rate = np.broadcast_to(acct.usd_rate(t4, bid), bid.shape).astype(np.float64)
    shape = (n, 4)
    return Prepared({**settings, "start_ms": start, "end_ms": end}, bars, first, bid.reshape(shape),
                    ask.reshape(shape), rate.reshape(shape), max_window)


def simulate(prep: Prepared, inputs: dict, trades: bool = False) -> dict:
    """Un backtest „Open prices only” cu parametrii `inputs`: balanța finală + cifrele din tabelul de optimizare."""
    period, price = int(inputs["ma_period"]), inputs["ma_price"]
    window, rr = int(inputs["tl_bars"]), float(inputs["rr"])
    if window > prep.max_window:
        raise ValueError(f"InpTLBars = {window} > {prep.max_window} (încălzirea pregătită)")
    b, spec, s = prep.bars, prep.spec, prep.settings
    n = len(b["t"])
    sig = strategy_fast.signals(b["o"], b["h"], b["l"], b["c"], period, price, window, prep.breaks(window))
    point = 10.0 ** -spec["digits"]
    deposit = float(s["deposit"])
    balance, profits, st = _run(prep.first, n, sig["entry"], sig["exit_buy"], sig["exit_sell"],
                                bool(inputs["close_on_ma"]), prep.price(price), b["c"], period, window, rr, prep.bid, prep.ask, prep.rate, point, 10.0 ** spec["digits"],
                                float(spec["contract"]), spec["vol_min"], spec["vol_step"], spec["vol_max"],
                                float(s["leverage"]), deposit, float(inputs["risk_pct"]), backtester.STOP_OUT)
    gross_p = float(profits[profits > 0].sum())
    gross_l = float(profits[profits < 0].sum())
    net = round(balance - deposit, 2)
    out = {"balance": float(balance), "profit": net, "trades": int(len(profits)),
           "profit_factor": round(gross_p / -gross_l, 2) if gross_l < 0 else None,
           "expected_payoff": round(net / len(profits), 2) if len(profits) else 0.0,
           "equity_dd": round(float(st[2]), 2), "equity_dd_pct": round(float(st[4]), 2),
           "balance_dd": round(float(st[8]), 2),
           "recovery": round(net / st[2], 2) if st[2] > 0 else None}
    if trades:
        out["profits"] = profits
    return out


def check(settings: dict, inputs: dict, prep: Prepared | None = None, db=None, log=print) -> dict:
    """Compară simulate cu backtester.run (originalul testerului): balanța și profitul fiecărei tranzacții."""
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
