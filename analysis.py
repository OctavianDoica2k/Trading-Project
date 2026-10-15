import pandas as pd
from datetime import datetime, date


# zi lu claudiu ca atunci cand cerem analiza sa salveze 
# un csv pe zile de pe 1 ianuarie an curent pana la data curenta
# in acelasi folder cu analysis.py

df = pd.read_csv(r'C:\Users\nume\Documents\Nexus\proiect\Trading-Project\exporturi_csv\EURUSD\EURUSD_D1_2026-01-01_2026-10-15.csv')

yhigh = df.sort_values(by= 'high', ascending= False).reset_index(drop = True).iloc[0]['high']
ylow = df.sort_values(by= 'low', ascending= True).reset_index(drop = True).iloc[0]['low']
print('trend last year: ', round((df.iloc[len(df)-1]['close'] - df.iloc[0]['open']) / (yhigh - ylow) *100, 2),'%')

tsmonth = datetime(pd.to_datetime(df.iloc[len(df)-1]['time_utc']).year, pd.to_datetime(df.iloc[len(df)-1]['time_utc']).month, 1)
lastmonth = df[pd.to_datetime(df['time_utc']) >= tsmonth].reset_index(drop = True)

mhigh = lastmonth.sort_values(by= 'high', ascending= False).reset_index(drop = True).iloc[0]['high']
mlow = lastmonth.sort_values(by= 'low', ascending= True).reset_index(drop = True).iloc[0]['low']

print('trend last month: ', round((lastmonth.iloc[len(lastmonth)-1]['close'] - lastmonth.iloc[0]['open']) / (mhigh - mlow) *100, 2),'%')

for i in range(len(lastmonth)-1, len(lastmonth)-6, -1):
    if pd.to_datetime(lastmonth.iloc[i]['time_utc']) - pd.to_datetime(lastmonth.iloc[i-1]['time_utc']) > pd.Timedelta(days= 1): break
    
lastweek = lastmonth[lastmonth.index >= i].reset_index(drop = True)

whigh = lastmonth.sort_values(by= 'high', ascending= False).reset_index(drop = True).iloc[0]['high']
wlow = lastmonth.sort_values(by= 'low', ascending= True).reset_index(drop = True).iloc[0]['low']

print('trend last week: ', round((lastweek.iloc[len(lastweek)-1]['close'] - lastweek.iloc[0]['open']) / (whigh - wlow) *100, 2),'%')

print('trend last day: ', round((df.iloc[len(df)-1]['close'] - df.iloc[len(df)-1]['open']) / (df.iloc[len(df)-1]['high'] - df.iloc[len(df)-1]['low']) *100, 2),'%')    