from analysis import medie_mobila, trace

# df = doar lumânări ÎNCHISE; ultimul rând = lumânarea precedentă (shift 1 în EA-ul MT5).
def verify_entry(df, period, price):
    sma = medie_mobila(df, period, price)
    if sma[-1] is None: return 'stay'
    sma_direction = 'stay'
    if df.iloc[len(df)-1]['close'] < sma[-1]: sma_direction = 'sell'
    elif df.iloc[len(df)-1]['close'] > sma[-1]: sma_direction = 'buy'
    df2 = df.iloc[:-1]

    dr_max_valid, dr_min_valid = trace(df)
    dr_max_valid2, dr_min_valid2 = trace(df2)

    # O linie validă înainte de ultima lumânare și dispărută după ea = spartă de închiderea ei.
    trend_direction = 'stay'
    if set(dr_max_valid2) - set(dr_max_valid): trend_direction = 'buy'
    elif set(dr_min_valid2) - set(dr_min_valid): trend_direction = 'sell'

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
