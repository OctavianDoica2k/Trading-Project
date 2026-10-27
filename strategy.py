from analysis import medie_mobila, trace

def verify_entry(df, period, price):
    sma = medie_mobila(df, period, price)
    sma_direction = 'stay'
    if df.iloc[len(df)-2]['close'] < sma[-2]: sma_direction = 'sell'
    elif df.iloc[len(df)-2]['close'] > sma[-2]: sma_direction = 'buy'
    df2 = df.drop(index=[len(df)-1])
    
    dr_max_valid, dr_min_valid = trace(df)
    dr_max_valid2, dr_min_valid2 = trace(df2)
    
    dr_max_valid2_str = ','.join(map(str, dr_max_valid2))
    dr_max_valid_str = ','.join(map(str, dr_max_valid))
    
    dr_min_valid2_str = ','.join(map(str, dr_min_valid2))
    dr_min_valid_str = ','.join(map(str, dr_min_valid))
    
    trend_direction = 'stay'
    if dr_max_valid_str.find(dr_max_valid2_str) == -1: trend_direction = 'buy'
    elif dr_min_valid_str.find(dr_min_valid2_str) == -1: trend_direction = 'sell'
    
    if trend_direction == sma_direction: return trend_direction
    return 'stay'


def transaction_limits(df, period, price, direction, tp_factor):
    sma = medie_mobila(df, period, price)
    sl = sma[-1]
    op = df.iloc[-1]['close']
    if direction == 'buy': tp = op + abs(op-sl) * tp_factor
    else: tp = op - abs(op-sl) * tp_factor
    return tp, sl, op