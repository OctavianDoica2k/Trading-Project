import pandas as pd


def heikin_ashi(df):
    df_h = pd.DataFrame()
    
    df_h['close'] = (df['open']+df['close']+df['high']+df['low'])/4
    
    for i in range(len(df)):
        if i == 0: df_h.loc[i,['open']] = (df.iloc[i]['open']+df.iloc[i]['close'])/2
        else: df_h.loc[i,['open']] = (df_h.iloc[i-1]['open']+df_h.iloc[i-1]['close'])/2
    
        df_h.loc[i,['high']] = max(df.iloc[i]['high'], df_h.iloc[i]['close'], df_h.iloc[i]['open'])
        df_h.loc[i,['low']] = min(df.iloc[i]['low'], df_h.iloc[i]['close'], df_h.iloc[i]['open'])
        
    df_h['time_utc'] = df['time_utc']
    
    return df_h