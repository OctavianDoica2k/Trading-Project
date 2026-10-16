import pandas as pd
from datetime import datetime

df = pd.read_csv(r'C:\Users\nume\Documents\Nexus\proiect\Trading-Project\exporturi_csv\EURUSD\EURUSD_D1_2026-01-01_2026-10-15.csv')

def timeframe_trends_indicator(df):
    
    yhigh = df.sort_values(by= 'high', ascending= False).reset_index(drop = True).iloc[0]['high']
    ylow = df.sort_values(by= 'low', ascending= True).reset_index(drop = True).iloc[0]['low']
    trendlastyear = round((df.iloc[len(df)-1]['close'] - df.iloc[0]['open']) / (yhigh - ylow) *100, 2)

    tsmonth = datetime(pd.to_datetime(df.iloc[len(df)-1]['time_utc']).year, pd.to_datetime(df.iloc[len(df)-1]['time_utc']).month, 1)
    lastmonth = df[pd.to_datetime(df['time_utc']) >= tsmonth].reset_index(drop = True)

    mhigh = lastmonth.sort_values(by= 'high', ascending= False).reset_index(drop = True).iloc[0]['high']
    mlow = lastmonth.sort_values(by= 'low', ascending= True).reset_index(drop = True).iloc[0]['low']

    trendlastmonth = round((lastmonth.iloc[len(lastmonth)-1]['close'] - lastmonth.iloc[0]['open']) / (mhigh - mlow) *100, 2)

    for i in range(len(lastmonth)-1, len(lastmonth)-6, -1):
        if pd.to_datetime(lastmonth.iloc[i]['time_utc']) - pd.to_datetime(lastmonth.iloc[i-1]['time_utc']) > pd.Timedelta(days= 1) or (pd.to_datetime(lastmonth.iloc[i]['time_utc']).month ==1 and pd.to_datetime(lastmonth.iloc[i]['time_utc']).day ==1): break
    
    lastweek = lastmonth[lastmonth.index >= i].reset_index(drop = True)

    whigh = lastmonth.sort_values(by= 'high', ascending= False).reset_index(drop = True).iloc[0]['high']
    wlow = lastmonth.sort_values(by= 'low', ascending= True).reset_index(drop = True).iloc[0]['low']

    trendlastweek = round((lastweek.iloc[len(lastweek)-1]['close'] - lastweek.iloc[0]['open']) / (whigh - wlow) *100, 2)

    trendlastday = round((df.iloc[len(df)-1]['close'] - df.iloc[len(df)-1]['open']) / (df.iloc[len(df)-1]['high'] - df.iloc[len(df)-1]['low']) *100, 2)

    return trendlastday,  trendlastweek, trendlastmonth, trendlastyear

#a,b,c,d = timeframe_trends_indicator(df)
#print(a,b,c,d)
#-------------------------------------------------------------------------------#
maxim = []
minim = []
i = 3
while i < len(df)-3:
    if df.iloc[i]['high'] >= df.iloc[i+1]['high'] and df.iloc[i]['high'] >= df.iloc[i+2]['high'] and df.iloc[i]['high'] >= df.iloc[i+3]['high'] and df.iloc[i]['high'] > df.iloc[i-1]['high'] and df.iloc[i]['high'] > df.iloc[i-2]['high'] and df.iloc[i]['high'] > df.iloc[i-3]['high']:
        maxim.append((i, df.iloc[i]['high']))
        
    if df.iloc[i]['low'] <= df.iloc[i+1]['low'] and df.iloc[i]['low'] <= df.iloc[i+2]['low'] and df.iloc[i]['low'] <= df.iloc[i+3]['low'] and df.iloc[i]['low'] < df.iloc[i-1]['low'] and df.iloc[i]['low'] < df.iloc[i-2]['low'] and df.iloc[i]['low'] < df.iloc[i-3]['low']:
        minim.append((i, df.iloc[i]['low']))
    i+=1

dr_max_valid = []
for i in range(len(maxim)):
    for j in range(i+1, len(maxim), 1):
        n = maxim[i][1]
        m = (maxim[j][0] - maxim[i][0]) / (maxim[j][1] - maxim[i][1])
        k = maxim[i][0]
        valid = 1
        for l in range(j+1, len(df), 1):
            if (l-k)/m + n < df.iloc[l]['close']: valid = 0
        if valid == 1: dr_max_valid.append((i,j))
        

dr_min_valid = []
for i in range(len(minim)):
    for j in range(i+1, len(minim), 1):
        n = minim[i][1]
        m = (minim[j][0] - minim[i][0]) / (minim[j][1] - minim[i][1])
        k = minim[i][0]
        valid = 1
        for l in range(j+1, len(df), 1):
            if (l-k)/m + n > df.iloc[l]['close']: valid = 0
        if valid == 1: dr_min_valid.append((i,j))