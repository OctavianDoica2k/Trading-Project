"""
Nucleul compilat (numba) al simulării din Strategy Tester: bucla de lumânări a EA-ului, cu aceleași
reguli și aceleași operații în virgulă mobilă ca varianta Python de dinainte (deci aceleași tranzacții,
la cent). Îl folosesc backtester.run (raportul complet, toate modurile) și fastsim (optimizarea).
"""
# =====================================================================================
#  CE FACE ACEST FIȘIER (pe scurt)
# -------------------------------------------------------------------------------------
#  run_bars(...) simulează lumânările k0 ... k1-1. Prețurile lumânării k (după modul de modelling,
#  pregătite de backtester) sunt t / bid / ask / rate[js[k] : je[k]] (rate = cât valorează 1 USD în
#  moneda contului). Ca EA-ul, la fiecare lumânare:
#    1. primul preț: SL / TP / stop out pentru poziția deschisă;
#    2. închiderea pe MA (lumânarea precedentă închisă de partea cealaltă a MA);
#    3. fără poziție: intrarea, cu SL / TP ca strategy.transaction_limits și lotul ca
#       Account.lots_for (pierde risk_pct % la SL, limitat de marja liberă);
#    4. restul prețurilor lumânării, cât timp există o poziție.
#  Starea (balanța, poziția, drawdown-urile) stă în vectorul S și trece de la o bucată de lumânări
#  la următoarea (modul „real” se simulează lună cu lună, ca ticurile să nu stea toate în memorie).
#  Întoarce tranzacțiile închise (un rând pe tranzacție, coloanele T_*) și graficul (un rând pe lumânare).
#  Rotunjirile sunt exact ca round(x, d) din Python (_round), ca balanța să iasă la cent.
# =====================================================================================
import math

import numpy as np
from numba import carray, njit, types
from numba.core import cgutils
from numba.extending import intrinsic

# Vectorul de stare S (poziții):
BAL, HAS, DIR, LOTS, PRICE, SL, TP, OPEN_T, BAL_BEFORE, MFE, MAE, LOAD = range(12)
LAST_OK, LAST_T, LAST_B, LAST_A, LAST_R, MIN_LEVEL, SKIP_SIDE, SKIP_LOTS = range(12, 20)
EQ_DD, BAL_DD = 20, 26           # câte 6: vârf, minim, max_dd, max_dd_pct, rel_pct, rel_money
STATE = 32
# Coloanele unui rând de tranzacție:
(T_DIR, T_OPEN_T, T_CLOSE_T, T_LOTS, T_OPEN, T_CLOSE, T_SL, T_TP, T_PROFIT, T_BAL_BEFORE, T_BAL_AFTER,
 T_MFE, T_MAE, T_REASON, T_VALUE) = range(15)
R_SL, R_TP, R_SO, R_MA, R_END = range(5)        # motivul închiderii (T_REASON)


def new_state(deposit: float) -> np.ndarray:
    s = np.zeros(STATE)
    s[BAL] = deposit
    s[MIN_LEVEL] = np.inf
    for o in (EQ_DD, BAL_DD):
        s[o] = s[o + 1] = deposit
    return s


# ---------- round(x, d) din Python ----------
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


# ---------- Ticurile din cache-ul necomprimat (backtester.Data.month_raw) ----------
@njit(cache=True)
def decode_raw(off, bp, sp, base, scale):
    """(timp ms, bid, ask) din (ms de la începutul lunii, bid în puncte, spread în puncte), într-o singură trecere:
    bid = puncte / scale, ask = (puncte + spread) / scale (prețurile sunt multipli exacți de punct)."""
    n = len(off)
    t = np.empty(n, np.int64)
    bid = np.empty(n)
    ask = np.empty(n)
    for i in range(n):
        t[i] = base + np.int64(off[i])
        bid[i] = bp[i] / scale
        ask[i] = (np.int64(bp[i]) + sp[i]) / scale
    return t, bid, ask


# ---------- SL / TP: strategy.transaction_limits ----------
@njit(cache=True)
def sma_window(k, p, period, window):
    """medie_mobila(df)[-1] pe df = lumânările max(0, k - window) ... k - 1, cu aceleași operații."""
    s = max(0, k - window)
    val = 0.0
    for j in range(period):
        val += p[s + j]
    sma = val / period
    for i in range(period, k - s):
        sma = (sma * period + p[s + i] - p[s + i - period]) / period
    return sma


@njit(cache=True)
def limits_from(d, k, sma, c, rr):
    """(ok, sl, tp) ca strategy.transaction_limits, din media sma; ok = False când aceasta întoarce None."""
    op = c[k - 1]
    if d > 0 and op > sma:
        return True, sma, op + abs(op - sma) * rr
    if d < 0 and op < sma:
        return True, sma, op - abs(op - sma) * rr
    return False, sma, 0.0


@njit(cache=True)
def limits(d, k, p, c, period, window, rr):
    """(ok, sl, tp) la deschiderea lui k, direcția d."""
    return limits_from(d, k, sma_window(k, p, period, window), c, rr)


@njit(cache=True)
def _dd(st, o, v):
    """tester_report.Drawdown.update pe o valoare; st[o:o+6] = vârf, minim, max_dd, max_dd_pct, rel_pct, rel_money."""
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
def close(S, t_ms, px, r, reason, value, contract, trades, nt):
    """Închide poziția la prețul px (cursul r al momentului); scrie tranzacția nt, întoarce nt + 1."""
    sign = S[DIR]
    per_price = S[LOTS] * contract * r              # bani pe o unitate de preț
    profit = _round((px - S[PRICE]) * sign * per_price, 100.0)
    S[BAL] = _round(S[BAL] + profit, 100.0)
    _dd(S, BAL_DD, S[BAL])
    fav = (px - S[PRICE]) * sign
    row = trades[nt]
    row[T_DIR], row[T_OPEN_T], row[T_CLOSE_T], row[T_LOTS] = sign, S[OPEN_T], t_ms, S[LOTS]
    row[T_OPEN], row[T_CLOSE], row[T_SL], row[T_TP] = S[PRICE], px, S[SL], S[TP]
    row[T_PROFIT], row[T_BAL_BEFORE], row[T_BAL_AFTER] = profit, S[BAL_BEFORE], S[BAL]
    row[T_MFE] = _round(max(S[MFE], fav) * per_price, 100.0)
    row[T_MAE] = _round(min(S[MAE], fav) * per_price, 100.0)
    row[T_REASON], row[T_VALUE] = reason, value
    S[HAS] = 0.0
    return nt + 1


@njit(cache=True)
def _walk(S, j0, j1, t, bid, ask, rate, contract, leverage, stop_out, fill_at_tick, trades, nt):
    """SL / TP / stop out pe prețurile j0 ... j1-1; actualizează equity-ul și drawdown-ul.
    Întoarce (nt, equity-ul minim văzut; NaN dacă nu era poziție sau prețuri)."""
    if S[HAS] == 0.0 or j1 <= j0:
        return nt, np.nan
    sign, lots, price = S[DIR], S[LOTS], S[PRICE]
    margin_base = lots * contract * price / leverage
    call_min = np.inf
    seen_min = np.inf
    for j in range(j0, j1):
        b, a, r = bid[j], ask[j], rate[j]
        px = b if sign > 0 else a                   # prețul la care s-ar închide poziția
        if sign > 0:
            hit_sl, hit_tp = b <= S[SL], b >= S[TP]
        else:
            hit_sl, hit_tp = a >= S[SL], a <= S[TP]
        # Cu execuția la prețul din ordin (open / ohlc1), poziția se închide la SL / TP înainte ca equity-ul
        # să vadă prețul de dincolo de ele (ca în MT5): altfel un salt peste SL (ex. la știri) ar arăta o
        # pierdere flotantă care nu există, iar drawdown-ul ar putea trece de 100%.
        if (hit_sl or hit_tp) and not fill_at_tick:
            px = S[SL] if hit_sl else S[TP]
        eq = S[BAL] + (px - price) * sign * lots * contract * r
        margin = margin_base * r
        level = eq / margin * 100
        so = level <= stop_out
        _dd(S, EQ_DD, eq)
        call_min = min(call_min, level)
        seen_min = min(seen_min, eq)
        fav = (px - price) * sign
        S[MFE] = max(S[MFE], fav)
        S[MAE] = min(S[MAE], fav)
        S[LOAD] = max(S[LOAD], margin / max(eq, 1e-9) * 100)
        if hit_sl or hit_tp or so:
            S[MIN_LEVEL] = min(S[MIN_LEVEL], call_min)
            if so and not (hit_sl or hit_tp):
                nt = close(S, t[j], px, r, R_SO, call_min, contract, trades, nt)
            else:
                lvl = S[SL] if hit_sl else S[TP]
                nt = close(S, t[j], px if fill_at_tick else lvl, r, R_SL if hit_sl else R_TP, lvl, contract,
                           trades, nt)
            return nt, seen_min
    S[MIN_LEVEL] = min(S[MIN_LEVEL], call_min)
    return nt, seen_min


@njit(cache=True)
def _enter(S, d, k, sma, t0, b0, a0, r0, c, rr, point, price_scale, contract, vol_min, vol_step, vol_max,
           leverage, risk_pct):
    """Intrarea în direcția d la primul preț al lumânării k: SL / TP ca strategy.transaction_limits, lotul ca
    Account.lots_for (pierde risk_pct % la SL, limitat de marja liberă). Semnalele fără tranzacție se numără."""
    ent = a0 if d > 0 else b0
    good, sl, tp = limits_from(d, k, sma, c, rr)
    if good:
        good = (ent - sl >= point and tp > ent) if d > 0 else (sl - ent >= point and tp < ent)
    if not good:
        S[SKIP_SIDE] += 1
        return
    tick_value = contract * point * r0
    per_lot = abs(ent - sl) / point * tick_value
    lots = 0.0
    if per_lot > 0:
        lots = _normalize(S[BAL] * risk_pct / 100.0 / per_lot, vol_min, vol_step, vol_max)
        while lots > 0 and lots * contract * ent / leverage * r0 > S[BAL] * 0.95:
            if lots <= vol_min:
                lots = 0.0
                break
            lots = _normalize(lots * 0.8, vol_min, vol_step, vol_max)
    if lots <= 0:
        S[SKIP_LOTS] += 1
        return
    S[HAS], S[DIR], S[LOTS], S[PRICE] = 1.0, d, lots, ent
    S[SL], S[TP] = _round(sl, price_scale), _round(tp, price_scale)
    S[OPEN_T], S[BAL_BEFORE] = t0, S[BAL]
    S[MFE], S[MAE], S[LOAD] = 0.0, 0.0, 0.0


@njit(cache=True)
def run_bars(S, k0, k1, js, je, bar_t, t, bid, ask, rate, entry, exit_buy, exit_sell, close_on_ma,
             p, c, period, window, rr, point, price_scale, contract, vol_min, vol_step, vol_max,
             leverage, risk_pct, stop_out, fill_at_tick, record):
    """Lumânările k0 ... k1-1 (vezi sus). js / je sunt indexate cu k - k0. Întoarce (tranzacții, grafic)."""
    trades = np.empty((2 * (k1 - k0) + 1, 15))
    graph = np.empty((k1 - k0 if record else 0, 5))
    nt = 0
    ng = 0
    for k in range(k0, k1):
        j0, j1 = js[k - k0], je[k - k0]
        if j1 <= j0:
            continue
        need_all = S[HAS] != 0.0
        # 1. Primul preț: SL / TP / stop out pentru poziția existentă.
        nt, eq_min = _walk(S, j0, j0 + 1, t, bid, ask, rate, contract, leverage, stop_out, fill_at_tick,
                           trades, nt)
        t0, b0, a0, r0 = t[j0], bid[j0], ask[j0], rate[j0]
        # 2. Închiderea pe MA.
        if S[HAS] != 0.0 and close_on_ma:
            if (S[DIR] > 0 and exit_buy[k]) or (S[DIR] < 0 and exit_sell[k]):
                nt = close(S, t0, b0 if S[DIR] > 0 else a0, r0, R_MA, 0.0, contract, trades, nt)
        # 3. Intrarea, doar fără poziție.
        if S[HAS] == 0.0 and entry[k] != 0:
            _enter(S, entry[k], k, sma_window(k, p, period, window), t0, b0, a0, r0, c, rr, point, price_scale,
                   contract, vol_min, vol_step, vol_max, leverage, risk_pct)
        # 4. Restul prețurilor lumânării, cât timp există o poziție.
        full = need_all or S[HAS] != 0.0
        nt, m = _walk(S, j0 + 1, j1, t, bid, ask, rate, contract, leverage, stop_out, fill_at_tick, trades, nt)
        if m == m:
            eq_min = m if eq_min != eq_min else min(eq_min, m)
        last = j1 - 1 if full else j0
        S[LAST_OK], S[LAST_T], S[LAST_B], S[LAST_A], S[LAST_R] = 1.0, t[last], bid[last], ask[last], rate[last]
        if record:
            # Balanța, equity-ul minim din lumânare, equity-ul la sfârșitul ei, încărcarea depozitului.
            if S[HAS] != 0.0:
                px = S[LAST_B] if S[DIR] > 0 else S[LAST_A]
                eq_end = S[BAL] + (px - S[PRICE]) * S[DIR] * S[LOTS] * contract * S[LAST_R]
                load = S[LOAD]
            else:
                eq_end, load = S[BAL], 0.0
            g = graph[ng]
            g[0], g[1] = bar_t[k], S[BAL]
            g[2] = _round(min(eq_min, eq_end) if eq_min == eq_min else eq_end, 100.0)
            g[3], g[4] = _round(eq_end, 100.0), _round(load, 100.0)
            ng += 1
        if S[HAS] != 0.0:
            S[LOAD] = 0.0
    return trades[:nt], graph[:ng]


@njit(cache=True)
def finish(S, contract):
    """„end of test”: poziția rămasă se închide la ultimul preț simulat. Întoarce tranzacția (0 sau 1 rânduri)."""
    trades = np.empty((1, 15))
    if S[HAS] == 0.0 or S[LAST_OK] == 0.0:
        return trades[:0]
    px = S[LAST_B] if S[DIR] > 0 else S[LAST_A]
    close(S, S[LAST_T], px, S[LAST_R], R_END, 0.0, contract, trades, 0)
    return trades


# =====================================================================================
#  Real ticks / 1 minute OHLC pe indexul pe minute (optimizarea și testul complet)
# -------------------------------------------------------------------------------------
#  O simulare pe ticuri reale trece prin zeci de milioane de prețuri, dar o poziție se poate închide doar
#  unde prețul atinge SL-ul, TP-ul sau nivelul de stop out. build_index calculează o singură dată (nu
#  depinde de parametri), pentru fiecare lumânare (întreagă și fără primul tic), pentru fiecare minut din
#  ea și pentru fiecare grup de GROUP lumânări: minimul / maximul bid și ask, cea mai mare cădere a bid-ului
#  și cea mai mare urcare a ask-ului (perechea de prețuri, în ordinea timpului). Nucleele verifică întâi
#  grupul / lumânarea, apoi minutele: unde niciun nivel nu poate fi atins, equity-ul minim, drawdown-ul,
#  marja, MFE / MAE se iau din agregate; doar minutul în care se poate închide poziția se parcurge tic cu
#  tic, exact ca run_bars. Ieșirile (tic, oră, preț, profit) și raportul sunt deci aceleași ca în run_bars.
#  Ticurile nu se copiază: sunt segmente (o lună = fișierele raw/ ale lunii, mapate în memorie), date nucleelor
#  prin adresele lor: segs[sg] = (adresa ms-urilor de la începutul segmentului (uint32), a bid-ului în puncte (int32),
#  a spread-ului în puncte (int16), numărul de ticuri, începutul segmentului în ms). bid = puncte / 10^digits;
#  ask = (puncte + spread) / 10^digits la ticuri, bid + spread × punct la 1 minute OHLC (aceleași operații ca
#  backtester.ohlc_points). Doar pentru cont în USD.
# =====================================================================================
# Coloanele agregatelor (bf[:, F_REST + ...], bf[:, F_ALL + ...], kf[:, ...], gf[:, ...]):
G_MINB, G_MAXB, G_MINA, G_MAXA, G_DH, G_DL, G_RL, G_RH = range(8)
F_B0, F_A0, F_BL, F_AL, F_REST, F_ALL = 0, 1, 2, 3, 4, 12   # bf: primul / ultimul tic, restul, lumânarea întreagă
I_N, I_KB0, I_KB1 = range(3)                             # bi: ticuri în lumânare, minutele restului [kb0, kb1)
K_SEG, K_S, K_E = range(3)                               # ki: segmentul minutului și ticurile lui [s, e) din el
GROUP = 16                                               # lumânări pe grup (gf: agregatele, gl: ultima cu ticuri)


@intrinsic
def _ptr(typingctx, addr):
    """Adresa (int64) ca pointer, pentru carray."""
    def codegen(context, builder, sig, args):
        return builder.inttoptr(args[0], cgutils.voidptr_t)
    return types.voidptr(addr), codegen


@njit(cache=True, inline="always")
def _seg(segs, sg):
    """Tablourile segmentului sg (vezi segs mai sus): (ms de la început, bid în puncte, spread în puncte, începutul)."""
    n = segs[sg, 3]
    return (carray(_ptr(segs[sg, 0]), n, np.uint32), carray(_ptr(segs[sg, 1]), n, np.int32),
            carray(_ptr(segs[sg, 2]), n, np.int16), segs[sg, 4])


@njit(cache=True, inline="always")
def _px(bp, sp, j, scale, point, ask_exact):
    b = bp[j] / scale
    a = (bp[j] + sp[j]) / scale if ask_exact else b + sp[j] * point
    return b, a


@njit(cache=True)
def _merge(acc, x):
    """acc = acc urmat de x (în timp): min / max și cea mai mare cădere / urcare peste ambele."""
    if acc[G_MAXB] - x[G_MINB] > max(acc[G_DH] - acc[G_DL], x[G_DH] - x[G_DL]):
        dh, dl = acc[G_MAXB], x[G_MINB]
    elif x[G_DH] - x[G_DL] > acc[G_DH] - acc[G_DL]:
        dh, dl = x[G_DH], x[G_DL]
    else:
        dh, dl = acc[G_DH], acc[G_DL]
    if x[G_MAXA] - acc[G_MINA] > max(acc[G_RH] - acc[G_RL], x[G_RH] - x[G_RL]):
        rl, rh = acc[G_MINA], x[G_MAXA]
    elif x[G_RH] - x[G_RL] > acc[G_RH] - acc[G_RL]:
        rl, rh = x[G_RL], x[G_RH]
    else:
        rl, rh = acc[G_RL], acc[G_RH]
    acc[G_MINB], acc[G_MAXB] = min(acc[G_MINB], x[G_MINB]), max(acc[G_MAXB], x[G_MAXB])
    acc[G_MINA], acc[G_MAXA] = min(acc[G_MINA], x[G_MINA]), max(acc[G_MAXA], x[G_MAXA])
    acc[G_DH], acc[G_DL], acc[G_RL], acc[G_RH] = dh, dl, rl, rh


@njit(cache=True)
def _block(ki, kf, kb, seg, s, e, minb, maxb, mina, maxa, dh, dl, rl, rh, acc, has_rest):
    """Minutul kb (ticurile [s, e) din segmentul seg, cu agregatele lui), adăugat la restul lumânării (acc)."""
    ki[kb, K_SEG], ki[kb, K_S], ki[kb, K_E] = seg, s, e
    x = kf[kb]
    x[G_MINB], x[G_MAXB], x[G_MINA], x[G_MAXA] = minb, maxb, mina, maxa
    x[G_DH], x[G_DL], x[G_RL], x[G_RH] = dh, dl, rl, rh
    if has_rest:
        _merge(acc, x)
    else:
        acc[:] = x


@njit(cache=True)
def _bar(k, cnt, kb, has_rest, acc, one, b0, a0, bl, al, tl, bi, bt, bf, gf, gl):
    """Lumânarea k, terminată: ticurile, ultimul preț, restul, lumânarea întreagă (primul tic urmat de rest) și grupul."""
    bi[k, I_N], bi[k, I_KB1] = cnt, kb
    bt[k, 1] = tl
    bf[k, F_BL], bf[k, F_AL] = bl, al
    one[G_MINB], one[G_MAXB], one[G_MINA], one[G_MAXA] = b0, b0, a0, a0
    one[G_DH], one[G_DL], one[G_RL], one[G_RH] = b0, b0, a0, a0
    if has_rest:
        bf[k, F_REST:F_ALL] = acc
        _merge(one, acc)
    bf[k, F_ALL:] = one
    g = k // GROUP
    if gl[g] < 0:
        gf[g] = one
    else:
        _merge(gf[g], one)
    gl[g] = k


@njit(cache=True)
def build_index(segs, t0s, t1s, scale, point, ask_exact):
    """Indexul prețurilor pentru lumânările cu începutul t0s / sfârșitul t1s (ms), dintr-o singură trecere peste
    segmente (în ordinea timpului). Întoarce (bi, bt, bf, ki, kf, gf, gl): pe lumânare numărul de ticuri, minutele
    restului, ora primului / ultimului tic și agregatele; pe minut segmentul, ticurile și agregatele; pe grup
    agregatele și ultima lumânare cu ticuri."""
    nb = len(t0s)
    cap = (t1s.max() - t0s[0]) // 60000 + nb + len(segs) + 2 if nb else 1
    bi = np.zeros((nb, 3), np.int64)
    bt = np.zeros((nb, 2), np.int64)
    bf = np.zeros((nb, 4 + 16))
    ki = np.zeros((cap, 3), np.int64)
    kf = np.zeros((cap, 8))
    ng = (nb + GROUP - 1) // GROUP
    gf = np.zeros((ng, 8))
    gl = np.full(ng, -1, np.int64)
    if nb == 0:
        return bi, bt, bf, ki[:0].copy(), kf[:0].copy(), gf, gl
    acc = np.zeros(8)
    one = np.zeros(8)
    k, kb, cnt = 0, 0, 0
    cur_min, cur_seg, s0, e0, has_rest = np.int64(-1), -1, 0, 0, False
    b0 = a0 = bl = al = 0.0
    tl = np.int64(0)
    minb = maxb = mina = maxa = runmax = drop = dh = dl = runmin = rise = rl = rh = 0.0
    for sg in range(len(segs)):
        off, bp, sp, base = _seg(segs, sg)
        for i in range(len(off)):
            tm = base + np.int64(off[i])
            if tm >= t1s[k]:
                # Lumânarea k s-a terminat (și cele fără ticuri de după ea).
                if cnt > 0:
                    if cur_min >= 0:
                        _block(ki, kf, kb, cur_seg, s0, e0, minb, maxb, mina, maxa, dh, dl, rl, rh, acc, has_rest)
                        kb += 1
                        has_rest = True
                    _bar(k, cnt, kb, has_rest, acc, one, b0, a0, bl, al, tl, bi, bt, bf, gf, gl)
                k += 1
                while k < nb and tm >= t1s[k]:
                    bi[k, I_KB0] = bi[k, I_KB1] = kb
                    k += 1
                if k == nb:
                    break
                bi[k, I_KB0] = bi[k, I_KB1] = kb
                cnt, cur_min, has_rest = 0, np.int64(-1), False
            if tm < t0s[k]:
                continue
            b, a = _px(bp, sp, i, scale, point, ask_exact)
            if cnt == 0:                                  # primul tic al lumânării
                b0, a0, bl, al, tl = b, a, b, a, tm
                bt[k, 0] = tm
                bf[k, F_B0], bf[k, F_A0] = b, a
                cnt = 1
                continue
            # Restul, minut cu minut (un minut nou începe și la granița segmentelor).
            mm = tm // 60000
            if mm != cur_min or sg != cur_seg:
                if cur_min >= 0:
                    _block(ki, kf, kb, cur_seg, s0, e0, minb, maxb, mina, maxa, dh, dl, rl, rh, acc, has_rest)
                    kb += 1
                    has_rest = True
                cur_min, cur_seg, s0 = mm, sg, i
                minb, maxb, mina, maxa = b, b, a, a
                runmax, drop, dh, dl = b, 0.0, b, b       # max-ul de până acum, căderea bid-ului și perechea ei
                runmin, rise, rl, rh = a, 0.0, a, a       # min-ul de până acum, urcarea ask-ului și perechea ei
            else:
                minb, maxb, mina, maxa = min(minb, b), max(maxb, b), min(mina, a), max(maxa, a)
                runmax = max(runmax, b)
                if runmax - b > drop:
                    drop, dh, dl = runmax - b, runmax, b
                runmin = min(runmin, a)
                if a - runmin > rise:
                    rise, rl, rh = a - runmin, runmin, a
            bl, al, tl = b, a, tm
            cnt += 1
            e0 = i + 1
        if k == nb:
            break
    if k < nb:
        if cnt > 0:
            if cur_min >= 0:
                _block(ki, kf, kb, cur_seg, s0, e0, minb, maxb, mina, maxa, dh, dl, rl, rh, acc, has_rest)
                kb += 1
                has_rest = True
            _bar(k, cnt, kb, has_rest, acc, one, b0, a0, bl, al, tl, bi, bt, bf, gf, gl)
        for q in range(k + 1, nb):
            bi[q, I_KB0] = bi[q, I_KB1] = kb
    return bi, bt, bf, ki[:kb].copy(), kf[:kb].copy(), gf, gl


@njit(cache=True)
def _tick(S, b, a, contract, leverage, stop_out, fill_at_tick, trades, nt):
    """_walk pe un singur preț (cursul USD = 1). Întoarce (nt, s-a închis poziția)."""
    sign, lots, price = S[DIR], S[LOTS], S[PRICE]
    r = 1.0
    px = b if sign > 0 else a
    if sign > 0:
        hit_sl, hit_tp = b <= S[SL], b >= S[TP]
    else:
        hit_sl, hit_tp = a >= S[SL], a <= S[TP]
    if (hit_sl or hit_tp) and not fill_at_tick:
        px = S[SL] if hit_sl else S[TP]
    eq = S[BAL] + (px - price) * sign * lots * contract * r
    margin = lots * contract * price / leverage * r
    level = eq / margin * 100
    so = level <= stop_out
    _dd(S, EQ_DD, eq)
    S[MIN_LEVEL] = min(S[MIN_LEVEL], level)
    if not (hit_sl or hit_tp or so):
        return nt, False
    if so and not (hit_sl or hit_tp):
        nt = close(S, 0.0, px, r, R_SO, level, contract, trades, nt)
    else:
        lvl = S[SL] if hit_sl else S[TP]
        nt = close(S, 0.0, px, r, R_SL if hit_sl else R_TP, lvl, contract, trades, nt)
    return nt, True


@njit(cache=True)
def _quiet(S, g, contract, leverage, stop_out):
    """Prețurile cu agregatele g nu pot atinge SL / TP / stop out: le aplicăm equity-ul extrem și drawdown-ul,
    fără să le parcurgem. Întoarce False (și nu schimbă nimic) dacă vreun nivel ar putea fi atins."""
    sign, lots, price = S[DIR], S[LOTS], S[PRICE]
    if sign > 0:
        worst, best, hi, lo = g[G_MINB], g[G_MAXB], g[G_DH], g[G_DL]
        if worst <= S[SL] or best >= S[TP]:
            return False
    else:
        worst, best, hi, lo = g[G_MAXA], g[G_MINA], g[G_RL], g[G_RH]
        if worst >= S[SL] or best <= S[TP]:
            return False
    margin = lots * contract * price / leverage * 1.0
    eq_w = S[BAL] + (worst - price) * sign * lots * contract * 1.0
    level = eq_w / margin * 100
    if level <= stop_out:
        return False
    eq_b = S[BAL] + (best - price) * sign * lots * contract * 1.0
    eq_i = S[BAL] + (hi - price) * sign * lots * contract * 1.0      # cea mai mare cădere a equity-ului din g:
    eq_j = S[BAL] + (lo - price) * sign * lots * contract * 1.0      # de la eq_i (mai devreme) la eq_j
    S[MIN_LEVEL] = min(S[MIN_LEVEL], level)
    # Drawdown-ul (ca _dd pe fiecare preț): vârful de dinainte față de minim, sau căderea din interior.
    o = EQ_DD
    peak0 = S[o]
    c1 = peak0 - eq_w
    c2 = eq_i - eq_j
    if c2 > c1 and eq_i > peak0:
        dd, pk = c2, eq_i
    else:
        dd, pk = c1, peak0
    if dd > S[o + 2]:
        S[o + 2] = dd
        S[o + 3] = dd / pk * 100 if pk > 0 else 0.0
    for cand, cpk in ((c1, peak0), (c2, eq_i if eq_i > peak0 else peak0)):
        rel = cand / cpk * 100 if cpk > 0 else 0.0
        if rel > S[o + 4]:
            S[o + 4] = rel
            S[o + 5] = cand
    S[o] = max(peak0, eq_b)
    S[o + 1] = min(S[o + 1], eq_w)
    return True


@njit(cache=True)
def run_blocks(S, first, n, bi, bf, ki, kf, gf, gl, segs, scale, point, ask_exact, entry, exit_cb, exit_cs,
               close_on_ma, smak, p, period, window, c, rr, price_scale, contract, vol_min, vol_step, vol_max,
               leverage, risk_pct, stop_out, fill_at_tick):
    """Aceeași simulare ca run_bars (lumânările first ... n-1, cont în USD), pe indexul build_index, fără raport.
    exit_cb / exit_cs = sumele cumulate ale ieșirilor pe MA (buy / sell): exit_cb[k + 1] - exit_cb[k] = exit_buy[k].
    smak[k] = media pentru SL la lumânarea k (NaN = necalculată încă): se calculează la prima intrare acolo și
    rămâne pentru simulările următoare cu aceleași (MA, preț, TLBars).
    Întoarce profitul fiecărei tranzacții închise (inclusiv „end of test”)."""
    trades = np.empty((2 * (n - first) + 2, 15))
    nt = 0
    last = -1
    nb = n - first
    kk = 0
    while kk < nb:
        k = first + kk
        if S[HAS] != 0.0:
            cum = exit_cb if S[DIR] > 0 else exit_cs
            # Un grup întreg de GROUP lumânări fără ieșire posibilă: dintr-o singură verificare.
            if kk % GROUP == 0 and kk + GROUP <= nb and gl[kk // GROUP] >= 0:
                if not (close_on_ma and cum[k + GROUP] > cum[k]) and _quiet(S, gf[kk // GROUP], contract, leverage,
                                                                             stop_out):
                    last = gl[kk // GROUP]
                    kk += GROUP
                    continue
            # O lumânare întreagă (primul tic + restul) fără ieșire posibilă.
            if bi[kk, I_N] > 0 and not (close_on_ma and cum[k + 1] > cum[k]):
                if _quiet(S, bf[kk, F_ALL:], contract, leverage, stop_out):
                    last = kk
                    kk += 1
                    continue
        cnt = bi[kk, I_N]
        if cnt == 0:
            kk += 1
            continue
        last = kk
        b0, a0 = bf[kk, F_B0], bf[kk, F_A0]
        # 1. Primul preț: SL / TP / stop out pentru poziția existentă.
        if S[HAS] != 0.0:
            nt, _ = _tick(S, b0, a0, contract, leverage, stop_out, fill_at_tick, trades, nt)
        # 2. Închiderea pe MA.
        if S[HAS] != 0.0 and close_on_ma:
            cum = exit_cb if S[DIR] > 0 else exit_cs
            if cum[k + 1] > cum[k]:
                nt = close(S, 0.0, b0 if S[DIR] > 0 else a0, 1.0, R_MA, 0.0, contract, trades, nt)
        # 3. Intrarea, doar fără poziție.
        if S[HAS] == 0.0 and entry[k] != 0:
            if smak[k] != smak[k]:
                smak[k] = sma_window(k, p, period, window)
            _enter(S, entry[k], k, smak[k], 0.0, b0, a0, 1.0, c, rr, point, price_scale, contract, vol_min,
                   vol_step, vol_max, leverage, risk_pct)
        kk += 1
        # 4. Restul lumânării: întreg, apoi minut cu minut, apoi (doar unde trebuie) tic cu tic.
        if S[HAS] == 0.0 or cnt < 2 or _quiet(S, bf[kk - 1, F_REST:F_ALL], contract, leverage, stop_out):
            continue
        for kb in range(bi[kk - 1, I_KB0], bi[kk - 1, I_KB1]):
            if _quiet(S, kf[kb], contract, leverage, stop_out):
                continue
            _, bp, sp, _ = _seg(segs, ki[kb, K_SEG])
            closed = False
            for j in range(ki[kb, K_S], ki[kb, K_E]):
                b, a = _px(bp, sp, j, scale, point, ask_exact)
                nt, closed = _tick(S, b, a, contract, leverage, stop_out, fill_at_tick, trades, nt)
                if closed:
                    break
            if closed:
                break
    if S[HAS] != 0.0 and last >= 0:                       # „end of test”: ultimul tic simulat
        px = bf[last, F_BL] if S[DIR] > 0 else bf[last, F_AL]
        nt = close(S, 0.0, px, 1.0, R_END, 0.0, contract, trades, nt)
    return trades[:nt, T_PROFIT].copy()


# ---------- Testul complet (raportul) pe același index: backtester.run, Real ticks / 1 minute OHLC ----------
@njit(cache=True)
def _quiet_full(S, g, contract, leverage, stop_out):
    """_quiet + MFE / MAE / încărcarea depozitului (pentru raport). Întoarce (aplicat, equity-ul minim, nivelul minim
    al marjei) din g."""
    if not _quiet(S, g, contract, leverage, stop_out):
        return False, np.nan, np.nan
    sign, lots, price = S[DIR], S[LOTS], S[PRICE]
    worst, best = (g[G_MINB], g[G_MAXB]) if sign > 0 else (g[G_MAXA], g[G_MINA])
    margin = lots * contract * price / leverage * 1.0
    eq_w = S[BAL] + (worst - price) * sign * lots * contract * 1.0
    S[MFE] = max(S[MFE], (best - price) * sign)
    S[MAE] = min(S[MAE], (worst - price) * sign)
    S[LOAD] = max(S[LOAD], margin / max(eq_w, 1e-9) * 100)
    return True, eq_w, eq_w / margin * 100


@njit(cache=True)
def _tick_full(S, b, a, t_ms, call_min, contract, leverage, stop_out, fill_at_tick, trades, nt):
    """_walk pe un singur preț, cu tot ce cere raportul (MFE / MAE / încărcarea, ora închiderii). call_min = nivelul
    minim al marjei din apelul _walk curent (comentariul „so …%”). Întoarce (nt, închis, equity, call_min)."""
    sign, lots, price = S[DIR], S[LOTS], S[PRICE]
    r = 1.0
    px = b if sign > 0 else a
    if sign > 0:
        hit_sl, hit_tp = b <= S[SL], b >= S[TP]
    else:
        hit_sl, hit_tp = a >= S[SL], a <= S[TP]
    if (hit_sl or hit_tp) and not fill_at_tick:
        px = S[SL] if hit_sl else S[TP]
    eq = S[BAL] + (px - price) * sign * lots * contract * r
    margin = lots * contract * price / leverage * r
    level = eq / margin * 100
    so = level <= stop_out
    _dd(S, EQ_DD, eq)
    call_min = min(call_min, level)
    S[MIN_LEVEL] = min(S[MIN_LEVEL], level)
    fav = (px - price) * sign
    S[MFE] = max(S[MFE], fav)
    S[MAE] = min(S[MAE], fav)
    S[LOAD] = max(S[LOAD], margin / max(eq, 1e-9) * 100)
    if not (hit_sl or hit_tp or so):
        return nt, False, eq, call_min
    if so and not (hit_sl or hit_tp):
        nt = close(S, t_ms, px, r, R_SO, call_min, contract, trades, nt)
    else:
        lvl = S[SL] if hit_sl else S[TP]
        nt = close(S, t_ms, px, r, R_SL if hit_sl else R_TP, lvl, contract, trades, nt)
    return nt, True, eq, call_min


@njit(cache=True)
def run_blocks_full(S, first, n, bi, bt, bf, ki, kf, segs, scale, point, ask_exact, bar_t,
                    entry, exit_cb, exit_cs, close_on_ma, smak, p, period, window, c, rr, price_scale, contract,
                    vol_min, vol_step, vol_max, leverage, risk_pct, stop_out, fill_at_tick):
    """Ca run_bars (lumânările first ... n-1, cont în USD), cu raportul complet (tranzacții cu orele exacte, MFE / MAE,
    graficul pe lumânare), dar pe indexul build_index: minutele în care nu se poate închide poziția se iau din
    agregate. Lasă în S ultimul preț simulat (pentru finish). Întoarce (tranzacții, grafic) ca run_bars."""
    nb = n - first
    trades = np.empty((2 * nb + 2, 15))
    graph = np.empty((nb, 5))
    nt, ng = 0, 0
    for kk in range(nb):
        k = first + kk
        cnt = bi[kk, I_N]
        if cnt == 0:
            continue
        b0, a0, t0 = bf[kk, F_B0], bf[kk, F_A0], bt[kk, 0]
        need_all = S[HAS] != 0.0
        eq_min = np.nan
        done = False
        if need_all:
            cum = exit_cb if S[DIR] > 0 else exit_cs
            if not (close_on_ma and cum[k + 1] > cum[k]):
                # Lumânarea întreagă fără ieșire posibilă: primul tic + restul, din agregate.
                ok, eq_w, _ = _quiet_full(S, bf[kk, F_ALL:], contract, leverage, stop_out)
                if ok:
                    eq_min = eq_w
                    done = True
        if not done:
            # 1. Primul preț: SL / TP / stop out pentru poziția existentă.
            if need_all:
                nt, _, eq_min, _ = _tick_full(S, b0, a0, t0, np.inf, contract, leverage, stop_out, fill_at_tick,
                                              trades, nt)
            # 2. Închiderea pe MA.
            if S[HAS] != 0.0 and close_on_ma:
                cum = exit_cb if S[DIR] > 0 else exit_cs
                if cum[k + 1] > cum[k]:
                    nt = close(S, t0, b0 if S[DIR] > 0 else a0, 1.0, R_MA, 0.0, contract, trades, nt)
            # 3. Intrarea, doar fără poziție.
            if S[HAS] == 0.0 and entry[k] != 0:
                if smak[k] != smak[k]:
                    smak[k] = sma_window(k, p, period, window)
                _enter(S, entry[k], k, smak[k], t0, b0, a0, 1.0, c, rr, point, price_scale, contract, vol_min,
                       vol_step, vol_max, leverage, risk_pct)
            # 4. Restul lumânării: întreg, apoi minut cu minut, apoi (doar unde trebuie) tic cu tic.
            if S[HAS] != 0.0 and cnt >= 2:
                seen, call_min = np.inf, np.inf
                ok, eq_w, lv = _quiet_full(S, bf[kk, F_REST:F_ALL], contract, leverage, stop_out)
                if ok:
                    seen, call_min = eq_w, lv
                else:
                    for kb in range(bi[kk, I_KB0], bi[kk, I_KB1]):
                        ok, eq_w, lv = _quiet_full(S, kf[kb], contract, leverage, stop_out)
                        if ok:
                            seen, call_min = min(seen, eq_w), min(call_min, lv)
                            continue
                        sg = ki[kb, K_SEG]
                        off, bp, sp, base = _seg(segs, sg)
                        closed = False
                        for j in range(ki[kb, K_S], ki[kb, K_E]):
                            b, a = _px(bp, sp, j, scale, point, ask_exact)
                            nt, closed, eq, call_min = _tick_full(S, b, a, base + np.int64(off[j]), call_min,
                                                                  contract, leverage, stop_out, fill_at_tick,
                                                                  trades, nt)
                            seen = min(seen, eq)
                            if closed:
                                break
                        if closed:
                            break
                eq_min = seen if eq_min != eq_min else min(eq_min, seen)
        # Ultimul preț simulat al lumânării (pentru grafic și „end of test”).
        if need_all or S[HAS] != 0.0:
            S[LAST_T], S[LAST_B], S[LAST_A] = bt[kk, 1], bf[kk, F_BL], bf[kk, F_AL]
        else:
            S[LAST_T], S[LAST_B], S[LAST_A] = t0, b0, a0
        S[LAST_OK], S[LAST_R] = 1.0, 1.0
        # Graficul: balanța, equity-ul minim din lumânare, equity-ul la sfârșitul ei, încărcarea depozitului.
        if S[HAS] != 0.0:
            px = S[LAST_B] if S[DIR] > 0 else S[LAST_A]
            eq_end = S[BAL] + (px - S[PRICE]) * S[DIR] * S[LOTS] * contract * S[LAST_R]
            load = S[LOAD]
        else:
            eq_end, load = S[BAL], 0.0
        g = graph[ng]
        g[0], g[1] = bar_t[k], S[BAL]
        g[2] = _round(min(eq_min, eq_end) if eq_min == eq_min else eq_end, 100.0)
        g[3], g[4] = _round(eq_end, 100.0), _round(load, 100.0)
        ng += 1
        if S[HAS] != 0.0:
            S[LOAD] = 0.0
    return trades[:nt], graph[:ng]
