from analysis import coloana, drepte_valide, medie_mobila, pivoti

import numpy as np


# df = doar lumânări ÎNCHISE; ultimul rând = lumânarea precedentă (shift 1 în EA-ul MT5).
def verify_entry(df, period, price):
    sma = medie_mobila(df, period, price)
    if sma[-1] is None: return 'stay'
    close = coloana(df, 'close')
    sma_direction = 'stay'
    if close[-1] < sma[-1]: sma_direction = 'sell'
    elif close[-1] > sma[-1]: sma_direction = 'buy'

    # O linie validă înainte de ultima lumânare și spartă de închiderea ei = semnal.
    # Liniile valide pe df fără ultima lumânare (df2) sunt cele din trace(df2). Pe df complet,
    # pivoturile sunt aceleași (plus, eventual, unul nou la final), deci o linie din df2 dispare
    # din trace(df) doar dacă ultima închidere o trece: ajunge un singur trace, pe df2, și
    # verificarea ultimei închideri (același rezultat ca set(trace(df2)) - set(trace(df))).
    high, low = coloana(df, 'high')[:-1], coloana(df, 'low')[:-1]
    maxim, minim = pivoti(high, low)
    last = len(close) - 1

    def sparta(puncte, perechi, rezistenta):
        with np.errstate(divide='ignore'):
            for i, j in perechi:
                (k, n), (x2, y2) = puncte[i], puncte[j]
                v = (last - k) / ((x2 - k) / (y2 - n)) + n      # dreapta la ultima lumânare
                if (v < close[-1]) if rezistenta else (v > close[-1]): return True
        return False

    trend_direction = 'stay'
    if sparta(maxim, drepte_valide(maxim, close[:-1], True), True): trend_direction = 'buy'
    elif sparta(minim, drepte_valide(minim, close[:-1], False), False): trend_direction = 'sell'

    if trend_direction == sma_direction: return trend_direction
    return 'stay'


def transaction_limits(df, period, price, direction, tp_factor):
    sma = medie_mobila(df, period, price)
    sl = sma[-1]
    op = df.iloc[-1]['close']
    if direction == 'buy' and op > sl: tp = op + abs(op-sl) * tp_factor
    elif direction == 'sell' and op < sl: tp = op - abs(op-sl) * tp_factor
    else: return None
    return tp, sl, op


# Închiderea pe MA (InpCloseOnMA): True dacă lumânarea precedentă s-a închis de partea cealaltă a MA.
def verify_exit(df, period, price, direction):
    sma = medie_mobila(df, period, price)
    if direction == 'buy': return df.iloc[-1]['close'] < sma[-1]
    if direction == 'sell': return df.iloc[-1]['close'] > sma[-1]
    return False
