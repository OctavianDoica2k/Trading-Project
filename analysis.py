# =====================================================================================
#  CE FACE ACEST FIȘIER (pe scurt)
# -------------------------------------------------------------------------------------
#  Aici sunt calculele „de analiză” scrise de mână, folosite de indicatorii din pagină.
#  Fișierul are DOUĂ PĂRȚI, despărțite de linia #----# de mai jos (indicators.py o caută
#  exact, deci linia aceea NU se șterge și NU se modifică):
#
#  Partea 1 (până la #----#) -> indicatorul „Trend multi-timeframe” (1D · 1W · 1M · 1Y)
#    - timeframe_trends_indicator(df): cât de puternic a crescut / scăzut prețul în ultima
#      zi, săptămână, lună și în tot anul, ca procent din intervalul (maxim - minim).
#    - Pe server, pd.read_csv(...) de mai jos e înlocuit automat cu lumânările D1 ale anului
#      curent din datastore (vezi run_mtf din indicators.py); calea de fișier contează doar
#      când rulezi fișierul de mână, pe calculatorul tău.
#
#  Partea 2 (după #----#) -> indicatorii „Linii de trend” și „Medie mobilă” (și strategy.py)
#    - trace(df): găsește vârfurile / văile (pivoturile) și dreptele de trend care le unesc
#      fără să fie străpunse de prețul de închidere. Folosește pivoti(high, low) și
#      drepte_valide(puncte, close, rezistenta), pe care le folosește și strategy.py.
#    - medie_mobila(df, period, price): media mobilă simplă (SMA) pe `period` lumânări.
#    - coloana(df, nume): o coloană din df ca vector numpy (citită o singură dată, rapid).
#
#  df = tabelul cu lumânări (o lumânare pe rând), cu coloanele open / high / low / close
#  (plus time_utc în partea 1). Rândul 0 = cea mai veche lumânare, ultimul = cea mai nouă.
#  analysis.py e citit din nou la fiecare calcul, deci o modificare aici se vede imediat.
# =====================================================================================
import pandas as pd
from datetime import datetime

# Lumânările zilnice (D1) EURUSD din anul curent, citite dintr-un export CSV.
# Pe server rândul acesta e înlocuit cu datele din datastore (vezi comentariul de sus).
df = pd.read_csv(r'C:\Users\nume\Documents\Nexus\proiect\Trading-Project\exporturi_csv\EURUSD\EURUSD_D1_2026-01-01_2026-10-15.csv')

# Calculează trendul pe 4 perioade: ultima zi, ultima săptămână, luna curentă și tot anul.
# Pentru fiecare perioadă formula e aceeași:
#     trend = (închiderea de la final - deschiderea de la început) / (maxim - minim) * 100
# Rezultatul e un procent între -100 și +100:
#   +100 = prețul a urcat tot intervalul (a deschis la minim și a închis la maxim),
#   -100 = a coborât tot intervalul, în jur de 0 = s-a întors cam de unde a plecat.
# Întoarce: (trendul zilei, al săptămânii, al lunii, al anului), rotunjite la 2 zecimale.
def timeframe_trends_indicator(df):

    # ---- ANUL: toate lumânările din df ----
    # Cel mai mare high din an (sortăm descrescător după high și luăm primul rând).
    yhigh = df.sort_values(by= 'high', ascending= False).reset_index(drop = True).iloc[0]['high']
    # Cel mai mic low din an (sortăm crescător după low și luăm primul rând).
    ylow = df.sort_values(by= 'low', ascending= True).reset_index(drop = True).iloc[0]['low']
    # Trendul anului: ultima închidere minus prima deschidere, raportat la intervalul anului.
    trendlastyear = round((df.iloc[len(df)-1]['close'] - df.iloc[0]['open']) / (yhigh - ylow) *100, 2)

    # ---- LUNA: lumânările din luna ultimei lumânări ----
    # Prima zi a lunii în care e ultima lumânare (ex. ultima e 15 oct. -> 1 oct., ora 00:00).
    tsmonth = datetime(pd.to_datetime(df.iloc[len(df)-1]['time_utc']).year, pd.to_datetime(df.iloc[len(df)-1]['time_utc']).month, 1)
    # Păstrăm doar lumânările de la începutul lunii încoace, renumerotate de la 0.
    lastmonth = df[pd.to_datetime(df['time_utc']) >= tsmonth].reset_index(drop = True)

    # Maximul și minimul lunii (la fel ca la an, dar doar pe lumânările lunii).
    mhigh = lastmonth.sort_values(by= 'high', ascending= False).reset_index(drop = True).iloc[0]['high']
    mlow = lastmonth.sort_values(by= 'low', ascending= True).reset_index(drop = True).iloc[0]['low']

    # Trendul lunii: ultima închidere a lunii minus prima deschidere a lunii.
    trendlastmonth = round((lastmonth.iloc[len(lastmonth)-1]['close'] - lastmonth.iloc[0]['open']) / (mhigh - mlow) *100, 2)

    # ---- SĂPTĂMÂNA: căutăm prima zi a săptămânii curente ----
    # Mergem înapoi de la ultima lumânare a lunii (cel mult 5 lumânări) și ne oprim la ziua i
    # care începe săptămâna: fie între ea și lumânarea dinainte e o pauză de peste o zi
    # (weekendul), fie e prima zi a lunii (săptămâna nu se întinde în luna trecută).
    # Atenție: dacă luna are mai puțin de 5–6 lumânări, iloc[i-1] poate ieși în afara lunii
    # (IndexError); pe server asta face doar valoarea săptămânii să apară „—”.
    for i in range(len(lastmonth)-1, len(lastmonth)-6, -1):
        if pd.to_datetime(lastmonth.iloc[i]['time_utc']) - pd.to_datetime(lastmonth.iloc[i-1]['time_utc']) > pd.Timedelta(days= 1) or pd.to_datetime(lastmonth.iloc[i]['time_utc']).day ==1: break

    # Lumânările săptămânii: de la ziua i găsită mai sus până la final.
    lastweek = lastmonth[lastmonth.index >= i].reset_index(drop = True)

    # Maximul și minimul folosite pentru săptămână.
    # Atenție: sunt calculate pe `lastmonth` (toată luna), nu pe `lastweek`, deci împărțim la
    # intervalul lunii. Dacă vrei intervalul săptămânii, înlocuiește lastmonth cu lastweek aici.
    whigh = lastmonth.sort_values(by= 'high', ascending= False).reset_index(drop = True).iloc[0]['high']
    wlow = lastmonth.sort_values(by= 'low', ascending= True).reset_index(drop = True).iloc[0]['low']

    # Trendul săptămânii: ultima închidere minus deschiderea primei zile din săptămână.
    trendlastweek = round((lastweek.iloc[len(lastweek)-1]['close'] - lastweek.iloc[0]['open']) / (whigh - wlow) *100, 2)

    # ---- ZIUA: doar ultima lumânare ----
    # (close - open) / (high - low) al ultimei zile: cât din intervalul zilei a fost mișcare
    # într-o singură direcție (pozitiv = zi de creștere, negativ = zi de scădere).
    trendlastday = round((df.iloc[len(df)-1]['close'] - df.iloc[len(df)-1]['open']) / (df.iloc[len(df)-1]['high'] - df.iloc[len(df)-1]['low']) *100, 2)

    # Ordinea contează: pagina citește valorile ca zi, săptămână, lună, an.
    return trendlastday,  trendlastweek, trendlastmonth, trendlastyear

# Test de mână: scoate # din fața celor două rânduri de mai jos și rulează fișierul.
#a,b,c,d = timeframe_trends_indicator(df)
#print(a,b,c,d)
#-------------------------------------------------------------------------------#
# ^ Linia de mai sus desparte partea 1 de partea 2. NU o șterge și NU o modifica.
#
# Partea 2 e scrisă pentru viteză: lumânările se citesc o singură dată ca vectori numpy (coloana),
# iar comparațiile se fac pe vectori întregi. Rezultatele sunt aceleași ca la varianta lumânare cu
# lumânare (aceleași formule, aceleași operații în virgulă mobilă).
import numpy as np


# Coloana `nume` ('open', 'high', 'low', 'close' ...) din df, ca vector numpy de numere.
# Merge pe un DataFrame pandas (df[nume]) și pe tabelele simple ale serverului / testerului, care
# știu doar len(df) și df.iloc[i][nume].
def coloana(df, nume):
    if hasattr(df, 'columns'):
        return df[nume].to_numpy(dtype=np.float64)
    return np.array([df.iloc[i][nume] for i in range(len(df))], dtype=np.float64)


# Pasul 1 - pivoturile:
#   - un MAXIM (vârf) e o lumânare al cărei high e cel puțin la fel de mare ca high-ul
#     celor 3 lumânări de după și strict mai mare decât al celor 3 lumânări de dinainte;
#   - un MINIM (vale) e la fel, dar cu low: cel mult cât celelalte 3 de după și strict mai
#     mic decât cele 3 de dinainte.
#   Se verifică lumânările 3 ... n-4 (fiecare are 3 vecine în stânga și 3 în dreapta), toate
#   deodată: pentru fiecare distanță d = 1, 2, 3 comparăm vectorul cu el însuși decalat cu d.
# Întoarce (maxim, minim): liste de (poziția lumânării, prețul), în ordinea pozițiilor.
def pivoti(high, low):
    n = len(high)
    if n < 7:
        return [], []
    g = np.arange(3, n - 3)
    e_max = np.ones(len(g), dtype=bool)
    e_min = np.ones(len(g), dtype=bool)
    for d in (1, 2, 3):
        e_max &= (high[g] >= high[g + d]) & (high[g] > high[g - d])
        e_min &= (low[g] <= low[g + d]) & (low[g] < low[g - d])
    maxim = [(int(i), high[i]) for i in g[e_max]]
    minim = [(int(i), low[i]) for i in g[e_min]]
    return maxim, minim


# Pasul 2 - dreptele:
#   Luăm fiecare pereche de pivoturi (i, j) și ducem dreapta prin cele două puncte, scrisă ca:
#     preț(l) = (l - k) / m + n,  k = poziția primului pivot, n = prețul lui,
#     m = câte lumânări „costă” o unitate de preț (inversul pantei: Δpoziție / Δpreț).
#   Dacă două pivoturi au exact același preț, m = inf și dreapta e orizontală (preț(l) = n).
#   rezistenta=True (vârfuri): dreapta e VALIDĂ dacă, de la primul pivot până la ultima lumânare,
#   nicio închidere nu trece PESTE ea. rezistenta=False (văi): nicio închidere nu coboară SUB ea.
#   Pentru un pivot i, toate dreptele (i, j) se verifică deodată, pe bucăți de lumânări tot mai
#   lungi; o dreaptă spartă nu se mai verifică mai departe (cele mai multe se sparg repede).
# Întoarce lista perechilor (i, j) valide (indici în `puncte`), în ordinea (i, apoi j).
def drepte_valide(puncte, close, rezistenta):
    valide = []
    N = len(close)
    x = np.array([p[0] for p in puncte], dtype=np.int64)
    y = np.array([p[1] for p in puncte], dtype=np.float64)
    with np.errstate(divide='ignore', invalid='ignore'):
        for i in range(len(puncte) - 1):
            k, n = x[i], y[i]
            m = (x[i + 1:] - k) / (y[i + 1:] - n)
            vii = np.arange(len(m))              # dreptele (i, i+1+vii) încă nesparte
            start, pas = k + 1, 32
            while len(vii) and start < N:
                l = np.arange(start, min(N, start + pas))
                v = (l[None, :] - k) / m[vii, None] + n
                spart = (v < close[l]) if rezistenta else (v > close[l])
                vii = vii[~spart.any(axis=1)]
                start, pas = start + pas, pas * 2
            valide += [(i, i + 1 + int(j)) for j in vii]
    return valide


# Găsește liniile de trend „valide” pe lumânările din df (pivoturile de la pasul 1, dreptele de
# la pasul 2). Întoarce două liste de perechi (i, j): indicii pivoturilor din `maxim` / `minim`
# care formează drepte valide. indicators.py citește și listele interne maxim / minim, ca să
# știe unde sunt punctele pe chart (de aceea au aceste nume și funcția are un singur return).
def trace(df):
    high, low, close = coloana(df, 'high'), coloana(df, 'low'), coloana(df, 'close')
    maxim, minim = pivoti(high, low)
    dr_max_valid = drepte_valide(maxim, close, True)     # rezistențe (prin două vârfuri)
    dr_min_valid = drepte_valide(minim, close, False)    # suporturi (prin două văi)
    return dr_max_valid, dr_min_valid


# Media mobilă simplă (SMA): pentru fiecare lumânare, media prețului `price` ('open', 'high',
# 'low' sau 'close') pe ultimele `period` lumânări (inclusiv ea).
# Întoarce o listă cu o valoare pentru fiecare lumânare din df; primele period-1 lumânări nu
# au destule lumânări în spate, deci primesc None (nu se desenează).
def medie_mobila(df, period, price):
    x = coloana(df, price).tolist()          # numere Python: citire rapidă, aceleași calcule
    sma_vals = []
    for i in range(len(x)):

        # Prea devreme: nu avem încă `period` lumânări.
        if i < period-1: sma_vals.append(None)

        # Prima medie: adunăm primele `period` prețuri și împărțim la period.
        elif i == period-1:
            val = 0
            for j in range(period):
                val += x[j]
            sma_vals.append(val/period)

        # Următoarele medii, fără să adunăm din nou tot: pornim de la suma anterioară
        # (media anterioară * period), adăugăm prețul nou și scădem prețul care a ieșit din
        # fereastră (cel de acum `period` lumânări), apoi împărțim din nou la period.
        else: sma_vals.append((sma_vals[-1]*period+x[i]-x[i-period])/period)

    return sma_vals
