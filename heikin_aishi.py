# =====================================================================================
#  CE FACE ACEST FIȘIER (pe scurt)
# -------------------------------------------------------------------------------------
#  Transformă lumânările obișnuite în lumânări Heikin-Ashi („bară medie” în japoneză).
#  Heikin-Ashi netezește graficul: fiecare lumânare e calculată și din cea de dinainte,
#  deci trendurile se văd mai clar (șiruri lungi de lumânări de aceeași culoare).
#
#  Formulele (HA = Heikin-Ashi, lumânarea i):
#    close HA = (open + high + low + close) / 4          media celor 4 prețuri reale
#    open  HA = (open HA + close HA) ale lumânării i-1, / 2  mijlocul lumânării HA dinainte
#               (prima lumânare: (open + close) / 2 din prețurile ei reale)
#    high  HA = cel mai mare dintre high real, open HA și close HA
#    low   HA = cel mai mic dintre low real, open HA și close HA
#
#  Serverul o apelează prin fastframe.run(), care rulează exact acest cod, dar mult mai
#  repede (vezi fastframe.py), când chartul e pe modul Heikin-Ashi.
# =====================================================================================
import pandas as pd


# Primește df cu coloanele open / high / low / close / time_utc (o lumânare pe rând, de la
# cea mai veche la cea mai nouă) și întoarce un tabel nou cu lumânările Heikin-Ashi.
def heikin_ashi(df):
    df_h = pd.DataFrame()   # tabelul rezultat, gol la început

    # close HA pentru toate lumânările deodată: media celor 4 prețuri.
    df_h['close'] = (df['open']+df['close']+df['high']+df['low'])/4

    # open / high / low HA se calculează lumânare cu lumânare, pentru că open-ul depinde de
    # lumânarea HA dinainte.
    for i in range(len(df)):
        # open HA: prima lumânare pornește din prețurile ei reale; restul, din mijlocul
        # lumânării HA anterioare.
        if i == 0: df_h.loc[i,['open']] = (df.iloc[i]['open']+df.iloc[i]['close'])/2
        else: df_h.loc[i,['open']] = (df_h.iloc[i-1]['open']+df_h.iloc[i-1]['close'])/2

        # high / low HA: fitilul trebuie să cuprindă și corpul HA, nu doar prețurile reale.
        df_h.loc[i,['high']] = max(df.iloc[i]['high'], df_h.iloc[i]['close'], df_h.iloc[i]['open'])
        df_h.loc[i,['low']] = min(df.iloc[i]['low'], df_h.iloc[i]['close'], df_h.iloc[i]['open'])

    # Ora lumânărilor rămâne aceeași ca la lumânările reale.
    df_h['time_utc'] = df['time_utc']

    return df_h
