"""
Copia RAPIDĂ a strategiei din strategy.py (verify_entry / verify_exit), pentru Strategy Tester.
Aici aceeași logică e calculată incremental, o singură dată pentru toate lumânările, în loc să fie
reluată de la zero pe fiecare lumânare (cum o cere strategy.py, apelat cu fereastra lumânării).
"""
# =====================================================================================
#  CE FACE ACEST FIȘIER (pe scurt)
# -------------------------------------------------------------------------------------
#  De ce: verify_entry verifică toate perechile de pivoturi din fereastră pe închiderile de după ele,
#  la fiecare lumânare. Chiar vectorizat (~10 ms la 1000 de lumânări), un backtest H1 pe 5 ani ar
#  dura minute; aici toate lumânările deodată durează zeci de milisecunde.
#
#  signals(...) calculează, pentru fiecare lumânare k, ce ar întoarce funcțiile originale dacă
#  le-am apela la deschiderea lui k, cu fereastra df = ultimele `window` lumânări ÎNCHISE
#  (k-window ... k-1), exact cum le apelează testerul:
#    entry[k]  = verify_entry(df, period, price)        -> +1 'buy', -1 'sell', 0 'stay'
#    sma[k]    = medie_mobila(df, period, price)[-1]     (NaN dacă fereastra e prea scurtă)
#    exit_buy[k] / exit_sell[k] = verify_exit(df, period, price, 'buy' / 'sell')
#
#  Cum (aceleași reguli ca analysis.trace + strategy.verify_entry):
#    - pivot maxim la g: high[g] >= high-ul celor 3 de după și > al celor 3 de dinainte (minim: invers);
#    - o dreaptă prin pivoturile a < b e validă cât timp nicio închidere de după a nu trece de ea,
#      cu valoarea (l - a) / m + n, m = (b - a) / (preț_b - preț_a), n = preț_a (aceleași operații
#      în virgulă mobilă ca în trace, deci aceleași rezultate);
#    - verify_entry compară dreptele valide pe df2 = df fără ultima lumânare cu cele valide pe df:
#      una dispărută = spartă de ultima închidere. O dreaptă (a, b) e în df2 dacă a e în fereastră
#      (a >= start + 3) și b e pivot confirmat în df2 (b <= ultima - 4);
#    - validitatea unei drepte nu depinde de începutul ferestrei, deci ajunge, pentru fiecare
#      dreaptă (a, b), prima închidere care o trece: înainte de confirmarea lui b = dreaptă invalidă,
#      după (cât a e încă în fereastră) = semnal la lumânarea următoare. Toate dreptele se verifică
#      deodată (numpy), fără buclă pe lumânări.
#  verify_samples(...) rulează funcțiile ORIGINALE din strategy.py pe lumânări alese și compară
#  rezultatul cu cel de aici (butonul „Verifică” din tester).
# =====================================================================================
import numpy as np
from numba import njit

PRICES = ("close", "open", "high", "low", "median", "typical", "weighted")


# Coloana de preț pentru medie (ca ENUM_APPLIED_PRICE din MT5).
def price_column(o, h, l, c, price: str) -> np.ndarray:
    return {"close": c, "open": o, "high": h, "low": l, "median": (h + l) / 2,
            "typical": (h + l + c) / 3, "weighted": (h + l + 2 * c) / 4}[price]


def _pivots(x: np.ndarray, is_max: bool) -> np.ndarray:
    """Indicii g ai pivoturilor din toată seria (fără condiția de fereastră)."""
    n = len(x)
    ok = np.zeros(n, dtype=bool)
    if n < 7:
        return np.nonzero(ok)[0]
    g = np.arange(3, n - 3)
    ok[3:n - 3] = True
    for d in (1, 2, 3):
        if is_max:
            ok[g] &= (x[g] >= x[g + d]) & (x[g] > x[g - d])
        else:
            ok[g] &= (x[g] <= x[g + d]) & (x[g] < x[g - d])
    return np.nonzero(ok)[0]


@njit(cache=True, error_model="numpy")
def _first_cross(a: np.ndarray, m: np.ndarray, n0: np.ndarray, c: np.ndarray, end: np.ndarray,
                 is_max: bool) -> np.ndarray:
    """Prima închidere l din (a[i], end[i]] care trece de dreapta i (-1 dacă niciuna), cu aceleași
    operații ca în trace. Compilată (numba): fiecare dreaptă se oprește la prima închidere care o
    trece (cele mai multe se sparg în primele zeci de lumânări)."""
    first = np.full(len(a), -1, dtype=np.int64)
    for i in range(len(a)):
        ai, mi, ni = a[i], m[i], n0[i]
        for l in range(ai + 1, end[i] + 1):
            v = (l - ai) / mi + ni
            if (v < c[l]) if is_max else (v > c[l]):
                first[i] = l
                break
    return first


def _breaks(x: np.ndarray, c: np.ndarray, window: int, is_max: bool) -> np.ndarray:
    """broke[k] = True dacă la deschiderea lui k o dreaptă validă în df2 a fost spartă de close[k-1].

    Dreapta (a, b) apare la confirmarea lui b (L0 = b + 3), dacă b - 3 și a - 3 sunt în fereastra de
    atunci (s = max(0, L0 + 1 - window)) și nicio închidere din (a, L0] nu trece de ea. Rămâne în
    fereastră cât timp L <= a + window - 4. Prima închidere p de după L0 care o trece o sparge, iar
    semnalul e la deschiderea lui p + 1. Deci ajunge, pentru fiecare dreaptă, prima trecere p din
    (a, min(n - 1, a + window - 4)]: p <= L0 = dreaptă invalidă, p > L0 = broke[p + 1]."""
    n = len(c)
    broke = np.zeros(n + 1, dtype=bool)
    piv = _pivots(x, is_max)
    s = np.maximum(0, piv + 4 - window)
    lo = np.searchsorted(piv, s + 3)                 # primul pivot a din fereastra lui b
    cnt = np.where(piv - 3 >= s, np.arange(len(piv)) - lo, 0)
    if not cnt.sum():
        return broke
    bi = np.repeat(np.arange(len(piv)), cnt)
    ai = np.arange(len(bi)) - np.repeat(np.cumsum(cnt) - cnt, cnt) + np.repeat(lo, cnt)
    A, B = piv[ai], piv[bi]
    end = np.minimum(n - 1, A + window - 4)
    BATCH = 50_000                                   # dreptele se verifică pe loturi (memorie)
    for i in range(0, len(A), BATCH):
        a, b = A[i:i + BATCH], B[i:i + BATCH]
        with np.errstate(divide="ignore", invalid="ignore"):
            m = (b - a) / (x[b] - x[a])
        p = _first_cross(a, m, x[a], c, end[i:i + BATCH], is_max)
        broke[p[p > b + 3] + 1] = True
    return broke


def trend_breaks(h, l, c, window: int) -> tuple[np.ndarray, np.ndarray]:
    """Partea de linii de trend din signals: depinde doar de `window` (nu de MA), deci optimizarea o
    poate păstra pentru fiecare InpTLBars. (up, down): dreaptă de rezistență / suport spartă."""
    h, l, c = (np.asarray(a, dtype=np.float64) for a in (h, l, c))
    return _breaks(h, c, window, True), _breaks(l, c, window, False)


def signals(o, h, l, c, period: int, price: str, window: int, breaks=None) -> dict:
    """Rezultatul funcțiilor din strategy.py la deschiderea fiecărei lumânări k = 0..n (vezi sus).
    breaks = trend_breaks(h, l, c, window) deja calculat (optimizarea), altfel se calculează aici."""
    o, h, l, c = (np.asarray(a, dtype=np.float64) for a in (o, h, l, c))
    n = len(c)
    p = price_column(o, h, l, c, price)
    sma = np.full(n + 1, np.nan)
    if period >= 1 and n >= period:
        sums = np.lib.stride_tricks.sliding_window_view(p, period).sum(axis=1) / period
        sma[period:] = sums                          # media lumânărilor k-period ... k-1
    k = np.arange(n + 1)
    nwin = np.minimum(k, window)
    sma[nwin < period] = np.nan                      # fereastra trimisă e mai scurtă decât perioada
    prev_c = np.r_[np.nan, c]
    with np.errstate(invalid="ignore"):
        sma_dir = np.where(prev_c < sma, -1, np.where(prev_c > sma, 1, 0))
    up, down = breaks if breaks is not None else trend_breaks(h, l, c, window)
    trend = np.where(up, 1, np.where(down, -1, 0))
    entry = np.where((trend == sma_dir) & (trend != 0), trend, 0)
    entry[np.isnan(sma)] = 0
    with np.errstate(invalid="ignore"):
        exit_buy = prev_c < sma
        exit_sell = prev_c > sma
    return {"entry": entry.astype(np.int8), "sma": sma, "exit_buy": exit_buy, "exit_sell": exit_sell}


# ---------- Verificarea cu funcțiile originale ----------
class Frame:
    """Ce folosesc strategy.py / analysis.py dintr-un DataFrame: len(df), df.iloc[i]['col'] și
    df.iloc[:-1]. Valorile sunt numpy.float64, ca în pandas (împărțirea la 0 dă inf, la fel)."""

    def __init__(self, rows):
        self.rows = rows

    def __len__(self):
        return len(self.rows)

    @property
    def iloc(self):
        return self

    def __getitem__(self, i):
        return Frame(self.rows[i]) if isinstance(i, slice) else self.rows[i]


def frame(o, h, l, c, lo: int, hi: int) -> Frame:
    """Lumânările lo ... hi-1 ca Frame, cu toate coloanele de preț (inclusiv median / typical / weighted)."""
    cols = {name: price_column(o, h, l, c, name) for name in PRICES}
    return Frame([{name: np.float64(a[i]) for name, a in cols.items()} for i in range(lo, hi)])


def verify_samples(strategy, o, h, l, c, period: int, price: str, window: int, ks, progress=None) -> list[dict]:
    """Rulează verify_entry / verify_exit ORIGINALE la lumânările ks și le compară cu signals()."""
    o, h, l, c = (np.asarray(a, dtype=np.float64) for a in (o, h, l, c))
    fast = signals(o, h, l, c, period, price, window)
    names = {1: "buy", -1: "sell", 0: "stay"}
    out = []
    for i, k in enumerate(ks):
        df = frame(o, h, l, c, max(0, k - window), k)
        with np.errstate(divide="ignore", invalid="ignore"):
            orig = strategy.verify_entry(df, period, price)
            ex = {d: (bool(strategy.verify_exit(df, period, price, d)) if df.rows and len(df) >= period else None)
                  for d in ("buy", "sell")}
        got = names[int(fast["entry"][k])]
        ok = orig == got
        if ex["buy"] is not None:
            ok = ok and ex["buy"] == bool(fast["exit_buy"][k]) and ex["sell"] == bool(fast["exit_sell"][k])
        out.append({"k": int(k), "original": orig, "fast": got, "exit_original": ex,
                    "exit_fast": {"buy": bool(fast["exit_buy"][k]), "sell": bool(fast["exit_sell"][k])}, "ok": ok})
        if progress:
            progress(i + 1, len(ks))
    return out
