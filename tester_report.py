"""
Statisticile raportului de backtest, ca în Strategy Tester-ul MT5 (tab-ul Backtest).
"""
# =====================================================================================
#  CE FACE ACEST FIȘIER (pe scurt)
# -------------------------------------------------------------------------------------
#  Formulele (help-ul MT5, „Testing Report”):
#   - Drawdown absolut = depozitul inițial − minimul (balanței / equity-ului), dacă e sub depozit;
#     maximal = cea mai mare scădere de la un vârf local la minimul următor (bani, și % din vârf);
#     relativ = cea mai mare scădere în % din vârful local (și banii ei).
#   - Profit factor = profit brut / |pierdere brută|; Expected payoff = profit net / tranzacții;
#     Recovery factor = profit net / drawdown-ul maximal al equity-ului.
#   - HPR al unei tranzacții = balanța după / balanța dinainte; AHPR = media lor, GHPR = media
#     geometrică (balanța finală / inițială)^(1/n); Sharpe = (AHPR − 1) / abaterea standard a HPR
#     (rata fără risc = 0, ca în tester).
#   - Z-Score (testul seriilor): Z = (N·(R − 0,5) − P) / √(P·(P − N) / (N − 1)), P = 2·W·L,
#     R = numărul de serii de câștiguri / pierderi; în paranteză, încrederea în %.
#   - LR Correlation / LR Standard Error: balanța după fiecare tranzacție față de dreapta de
#     regresie (cele mai mici pătrate) pe numărul tranzacției.
#   - MFE / MAE: cel mai bun / cel mai rău profit flotant al poziției, în bani.
# =====================================================================================
import math

import numpy as np


class Drawdown:
    """Urmărește vârful, minimul și drawdown-urile unei serii (balanță sau equity)."""

    def __init__(self, initial: float):
        self.initial = initial
        self.peak = initial
        self.min = initial
        self.max_dd = 0.0          # maximal: bani (și % la acel moment)
        self.max_dd_pct = 0.0
        self.rel_pct = 0.0         # relativ: % (și banii la acel moment)
        self.rel_money = 0.0

    def update(self, values: np.ndarray):
        if not len(values):
            return
        values = np.asarray(values, dtype=np.float64)
        peaks = np.maximum.accumulate(np.r_[self.peak, values])[1:]
        dd = peaks - values
        i = int(np.argmax(dd))
        if dd[i] > self.max_dd:
            self.max_dd, self.max_dd_pct = float(dd[i]), float(dd[i] / peaks[i] * 100) if peaks[i] > 0 else 0.0
        rel = np.where(peaks > 0, dd / np.where(peaks > 0, peaks, 1) * 100, 0)
        j = int(np.argmax(rel))
        if rel[j] > self.rel_pct:
            self.rel_pct, self.rel_money = float(rel[j]), float(dd[j])
        self.peak = float(peaks[-1])
        self.min = min(self.min, float(values.min()))

    def absolute(self) -> float:
        return max(0.0, self.initial - self.min)


def _streaks(results: list[float]):
    """Seriile de câștiguri / pierderi: listă de (semn, număr, sumă)."""
    out = []
    for p in results:
        s = 1 if p > 0 else -1 if p < 0 else 0
        if s == 0:
            continue
        if out and out[-1][0] == s:
            out[-1] = (s, out[-1][1] + 1, out[-1][2] + p)
        else:
            out.append((s, 1, p))
    return out


def _corr(x, y):
    if len(x) < 2:
        return None
    x, y = np.asarray(x, float), np.asarray(y, float)
    if x.std() == 0 or y.std() == 0:
        return None
    return float(np.corrcoef(x, y)[0, 1])


def _hms(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def stats(initial: float, trades: list[dict], deals: list[dict], eq: Drawdown, bal: Drawdown, min_margin_level,
          bars: int, ticks: int, quality: float) -> dict:
    profits = [t["profit"] for t in trades]
    n = len(trades)
    gross_profit = sum(p for p in profits if p > 0)
    gross_loss = sum(p for p in profits if p < 0)
    net = gross_profit + gross_loss
    wins = [p for p in profits if p > 0]
    losses = [p for p in profits if p < 0]
    shorts = [t for t in trades if t["dir"] < 0]
    longs = [t for t in trades if t["dir"] > 0]

    hpr = np.array([t["balance_after"] / t["balance_before"] for t in trades if t["balance_before"] > 0])
    ahpr = float(hpr.mean()) if len(hpr) else None
    final = initial + net
    ghpr = (final / initial) ** (1 / n) if n and final > 0 and initial > 0 else None
    sharpe = (ahpr - 1) / float(hpr.std()) if len(hpr) > 1 and hpr.std() > 0 else None

    # Z-Score
    z = z_conf = None
    w, l_ = len(wins), len(losses)
    nz = w + l_
    if w and l_ and nz > 2:
        r = len(_streaks(profits))
        p = 2 * w * l_
        den = math.sqrt(p * (p - nz) / (nz - 1)) if p > nz else 0
        if den > 0:
            z = (nz * (r - 0.5) - p) / den
            z_conf = math.erf(abs(z) / math.sqrt(2)) * 100

    # Regresia balanței pe numărul tranzacției
    lr_corr = lr_err = None
    if n >= 2:
        y = np.r_[initial, [t["balance_after"] for t in trades]]
        x = np.arange(len(y))
        lr_corr = _corr(x, y)
        a, b = np.polyfit(x, y, 1)
        resid = y - (a * x + b)
        lr_err = float(math.sqrt((resid ** 2).sum() / max(1, len(y) - 2)))

    streaks = _streaks(profits)
    win_streaks = [s for s in streaks if s[0] > 0]
    loss_streaks = [s for s in streaks if s[0] < 0]
    longest_win = max(win_streaks, key=lambda s: (s[1], s[2]), default=None)
    longest_loss = max(loss_streaks, key=lambda s: (s[1], -s[2]), default=None)
    richest_win = max(win_streaks, key=lambda s: s[2], default=None)
    worst_loss = min(loss_streaks, key=lambda s: s[2], default=None)

    mfe = [t["mfe"] for t in trades]
    mae = [t["mae"] for t in trades]
    holds = [(t["close_time"] - t["open_time"]) / 1000 for t in trades]

    def pct(a, b):
        return round(a / b * 100, 2) if b else 0.0

    return {
        "history_quality": quality, "bars": bars, "ticks": ticks, "symbols": 1,
        "initial_deposit": round(initial, 2),
        "net_profit": round(net, 2), "gross_profit": round(gross_profit, 2), "gross_loss": round(gross_loss, 2),
        "profit_factor": round(gross_profit / -gross_loss, 2) if gross_loss else None,
        "expected_payoff": round(net / n, 2) if n else 0.0,
        "recovery_factor": round(net / eq.max_dd, 2) if eq.max_dd else None,
        "sharpe": round(sharpe, 2) if sharpe is not None else None,
        "z_score": round(z, 2) if z is not None else None, "z_conf": round(z_conf, 2) if z_conf is not None else None,
        "ahpr": round(ahpr, 4) if ahpr is not None else None,
        "ghpr": round(ghpr, 4) if ghpr is not None else None,
        "lr_correlation": round(lr_corr, 2) if lr_corr is not None else None,
        "lr_std_error": round(lr_err, 2) if lr_err is not None else None,
        "bal_dd_abs": round(bal.absolute(), 2), "bal_dd_max": round(bal.max_dd, 2),
        "bal_dd_max_pct": round(bal.max_dd_pct, 2), "bal_dd_rel_pct": round(bal.rel_pct, 2),
        "bal_dd_rel": round(bal.rel_money, 2),
        "eq_dd_abs": round(eq.absolute(), 2), "eq_dd_max": round(eq.max_dd, 2), "eq_dd_max_pct": round(eq.max_dd_pct, 2),
        "eq_dd_rel_pct": round(eq.rel_pct, 2), "eq_dd_rel": round(eq.rel_money, 2),
        "margin_level": round(min_margin_level, 2) if min_margin_level is not None else None,
        "total_trades": n, "total_deals": len(deals),
        "short_trades": len(shorts), "short_won_pct": pct(sum(1 for t in shorts if t["profit"] > 0), len(shorts)),
        "long_trades": len(longs), "long_won_pct": pct(sum(1 for t in longs if t["profit"] > 0), len(longs)),
        "profit_trades": len(wins), "profit_trades_pct": pct(len(wins), n),
        "loss_trades": len(losses), "loss_trades_pct": pct(len(losses), n),
        "largest_profit": round(max(wins), 2) if wins else 0.0, "largest_loss": round(min(losses), 2) if losses else 0.0,
        "average_profit": round(sum(wins) / len(wins), 2) if wins else 0.0,
        "average_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
        "max_consec_wins": longest_win[1] if longest_win else 0,
        "max_consec_wins_money": round(longest_win[2], 2) if longest_win else 0.0,
        "max_consec_losses": longest_loss[1] if longest_loss else 0,
        "max_consec_losses_money": round(longest_loss[2], 2) if longest_loss else 0.0,
        "maximal_consec_profit": round(richest_win[2], 2) if richest_win else 0.0,
        "maximal_consec_profit_count": richest_win[1] if richest_win else 0,
        "maximal_consec_loss": round(worst_loss[2], 2) if worst_loss else 0.0,
        "maximal_consec_loss_count": worst_loss[1] if worst_loss else 0,
        "avg_consec_wins": round(sum(s[1] for s in win_streaks) / len(win_streaks)) if win_streaks else 0,
        "avg_consec_losses": round(sum(s[1] for s in loss_streaks) / len(loss_streaks)) if loss_streaks else 0,
        "corr_profit_mfe": _r2(_corr(profits, mfe)),
        "corr_profit_mae": _r2(_corr(profits, mae)),
        "corr_mfe_mae": _r2(_corr(mfe, mae)),
        "hold_min": _hms(min(holds)) if holds else "", "hold_max": _hms(max(holds)) if holds else "",
        "hold_avg": _hms(sum(holds) / len(holds)) if holds else "",
    }


def _r2(x):
    return round(x, 2) if x is not None else None


def downsample(graph: list[list], max_points: int) -> list[list]:
    """Cel mult max_points puncte pe grafic: din fiecare grup păstrăm equity-ul minim și ultimul punct."""
    if len(graph) <= max_points:
        return graph
    step = math.ceil(len(graph) / max_points)
    out = []
    for i in range(0, len(graph), step):
        chunk = graph[i:i + step]
        last = list(chunk[-1])
        last[2] = min(p[2] for p in chunk)
        last[4] = max(p[4] for p in chunk)
        out.append(last)
    return out
