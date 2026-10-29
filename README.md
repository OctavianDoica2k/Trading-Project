# Verax

Platformă de trading în browser, cu date la nivel de tic:

- **Chart live** actualizat la fiecare tic (timestamp la milisecundă), de la lumânări de 1 secundă până la W1.
- **Indicatori** calculați pe server (linii de trend, medie mobilă, trend multi-timeframe).
- **Alarme de preț** verificate pe server la fiecare tic, cu e-mail și notificări push.
- **Strategy Tester** ca în MetaTrader 5: backtest pe ticuri reale, optimizare completă sau genetică, test forward.
- Merge pe desktop și pe telefon, direct din browser.

Aplicația se numea **Nexus**. Infrastructura păstrează numele vechi: task-urile „Nexus Collector” și
„Nexus Server” de pe VM, folderul `C:\Nexus Trading`, `ssh nexus-vm`, EA-urile și indicatorii MT5 (`Nexus_*`).

## Cum circulă datele

**Toate datele vin din baza de date QuestDB**, nu direct de la broker:

```
cTrader ──► collector.py ──► QuestDB: ticuri, lumânări oficiale, simboluri
                                 ▲
                                 │ interogare la fiecare 50 ms
            server.py ───────────┘ ──► WebSocket ──► browser
```

- `collector.py` salvează live fiecare tic cTrader al simbolurilor din `COLLECT_SYMBOLS`, completează istoricul
  de ticuri de la `COLLECT_START` și ține la zi lumânările oficiale M1–W1.
- `server.py` nu se conectează la cTrader: citește ticurile noi din QuestDB la fiecare 50 ms și tot istoricul
  la cerere. În aplicație apar doar simbolurile salvate de colector.
- Toate trei pot rula pe același calculator (varianta cea mai simplă, descrisă mai jos). La noi, QuestDB și
  colectorul rulează non-stop pe o mașină virtuală, iar `server.py` pe PC (vezi
  [Mașina virtuală](#mașina-virtuală-questdb--colector)).

## Instalare și configurare, pas cu pas

Pașii de mai jos pun totul pe un singur calculator. Comenzile sunt pentru **Windows (PowerShell)**; pe macOS /
Linux folosește `python3` în loc de `py` și `.venv/bin/python` în loc de `.venv\Scripts\python`.

### Ce îți trebuie
- **Python 3.10 – 3.14** ([python.org](https://www.python.org/downloads/)). La instalarea pe Windows bifează
  „Add python.exe to PATH”.
- **Git** ([git-scm.com](https://git-scm.com/downloads)).
- **QuestDB** ([questdb.com/download](https://questdb.com/download/)): baza de date pentru ticuri.
- Un **cont cTrader** (un cont demo e suficient) și o aplicație cTrader Open API, de unde vin prețurile.
- Spațiu pe disc: o zi de ticuri EURUSD + XAUUSD ocupă ~9 MB în QuestDB (~1,5 MB după compresia automată);
  Strategy Tester-ul păstrează în plus ~1,2 GB pentru 5 ani de ticuri EURUSD.

### Pasul 1 – Descarcă proiectul și instalează bibliotecile
```powershell
git clone https://github.com/OctavianDoica2k/Trading-Project.git
cd Trading-Project
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```
`.venv` este un mediu Python separat, doar pentru acest proiect. Toate comenzile de mai jos îl folosesc.

### Pasul 2 – Pornește QuestDB
1. Descarcă QuestDB de pe [questdb.com/download](https://questdb.com/download/) și pornește-l (de ex. pe Windows:
   dezarhivezi și rulezi `questdb.exe start` din folderul `bin`; cu Docker:
   `docker run -d -p 9000:9000 -v questdb-data:/var/lib/questdb questdb/questdb`).
2. Deschide [http://localhost:9000](http://localhost:9000): trebuie să apară consola web QuestDB.

Tabelele (`ticks`, `bars` etc.) le creează automat colectorul la prima pornire; nu trebuie create de mână.

### Pasul 3 – Creează fișierul `.env`
```powershell
copy .env.example .env
```
Deschide `.env` într-un editor de text. Pentru început ajung:

| Variabilă | Ce pui |
|---|---|
| `QUESTDB_HOST` | `localhost` (sau adresa calculatorului cu QuestDB) |
| `QUESTDB_USER`, `QUESTDB_PASSWORD` | goale pe un QuestDB nou; altfel utilizatorul / parola HTTP din QuestDB |
| `CTRADER_CLIENT_ID`, `CTRADER_CLIENT_SECRET` | datele aplicației cTrader de la Pasul 4 |

Restul variabilelor (e-mail, parolă pentru pagină, simboluri) sunt explicate chiar în fișier și sunt opționale.
`.env` conține parole: e în `.gitignore`, deci rămâne doar pe calculatorul tău.

### Pasul 4 – Conectează contul cTrader (o singură dată)
1. Intră pe [openapi.ctrader.com/apps](https://openapi.ctrader.com/apps) cu cTrader ID-ul tău și creează o
   aplicație. Copiază **Client ID** și **Secret** în `.env`.
2. La aplicație, deschide **Playground**, autorizează contul (demo sau real) și copiază **Access token** și
   **Refresh token**.
3. Rulează:
   ```powershell
   .venv\Scripts\python ctrader_setup.py
   ```
   Lipești cele două tokenuri când ți se cer și alegi contul folosit pentru prețuri. Scriptul afișează un preț
   live ca verificare și salvează totul în `ctrader_token.json` (tot privat, în `.gitignore`). De acum tokenul
   se reînnoiește singur.

### Pasul 5 – Pornește colectorul de ticuri
```powershell
.venv\Scripts\python collector.py
```
Lasă fereastra deschisă: colectorul salvează ticurile live și descarcă istoricul de la `COLLECT_START`
(implicit 2026-01-01; simbolurile din `COLLECT_SYMBOLS`, implicit EURUSD și XAUUSD). Un istoric de câțiva ani
durează câteva ore la prima rulare; chart-ul se poate folosi între timp.

### Pasul 6 – Pornește aplicația
Într-o fereastră nouă de PowerShell, din același folder:
```powershell
.venv\Scripts\python server.py
```
Deschide [http://127.0.0.1:8000](http://127.0.0.1:8000). Gata!

- **De pe telefon:** `http://<IP-ul calculatorului>:8000`, în aceeași rețea Wi-Fi (serverul afișează adresa la
  pornire; la prima pornire Windows cere permisiunea în firewall).
- **Telefon simulat pe desktop:** în Chrome/Edge apasă `F12`, apoi `Ctrl+Shift+M` și alege un telefon din listă.

### Opțional
- **E-mail la alarme:** completează `SMTP_*` în `.env` (pentru Gmail ai nevoie de o „Parolă pentru aplicații”),
  repornește serverul și apasă „Trimite e-mail de test” în fereastra de alarme. Detalii:
  [Configurare e-mail](#configurare-e-mail).
- **Notificări push:** cer HTTPS (sau `localhost`); vezi [Notificări push](#notificări-push-windows--android).
  Cheia de semnare se generează singură la prima pornire (`vapid_private.pem`, privată).
- **Parolă pentru pagină:** pune `APP_PASSWORD` în `.env` dacă aplicația e accesibilă de pe internet.
- **Alte simboluri sau alt istoric:** `COLLECT_SYMBOLS` și `COLLECT_START` în `.env`, apoi repornești colectorul.

### Probleme frecvente
| Ce vezi | Ce faci |
|---|---|
| „QuestDB indisponibil” / pagina nu încarcă prețuri | QuestDB nu rulează sau `QUESTDB_HOST` e greșit: verifică `http://<QUESTDB_HOST>:9000` în browser. |
| „utilizator / parolă greșite” | `QUESTDB_USER` / `QUESTDB_PASSWORD` din `.env` nu se potrivesc cu setările QuestDB. |
| Lista de simboluri e goală | Colectorul n-a salvat încă nimic: pornește `collector.py` și așteaptă primele ticuri. |
| `ctrader_setup.py` cere `CTRADER_CLIENT_ID` | Completează Client ID și Secret în `.env` (Pasul 4). |
| Colectorul nu se mai conectează la cTrader | Tokenul a fost invalidat (de ex. folosit și pe alt calculator): repetă Pasul 4. |
| De pe telefon pagina nu se deschide | `HOST=0.0.0.0` în `.env`, același Wi-Fi, permite Python în firewall-ul Windows. |
| Primul test din Strategy Tester durează mult | Normal: se compilează funcțiile numba și se descarcă ticurile lunilor testate; rulările următoare durează secunde. |

## Aplicația publică prin ngrok (opțional)
La noi, un al doilea `server.py` rulează pe VM și e publicat prin **ngrok** (HTTPS, domeniu static gratuit):
merge din orice browser, fără nimic instalat. La prima vizită, ngrok (planul gratuit) arată o pagină de
avertizare: se apasă **Visit Site**. Adresa publică e domeniul din contul ngrok (și din `C:\ngrok\ngrok.yml`).
- ngrok rulează pe VM ca serviciu Windows „ngrok” (pornire automată), configurat în `C:\ngrok\ngrok.yml`
  (tokenul contului ngrok + domeniul). Oprire: `C:\ngrok\ngrok.exe service stop`.
- Windows Defender poate șterge `ngrok.exe` (alarmă falsă „Trojan:Win32/Kepavll!rfn”, s-a întâmplat pe 27.10.2026),
  așa că `C:\ngrok` e exclus din Defender. Dacă link-ul apare „offline” (ERR_NGROK_3200), verifici întâi
  `Get-MpThreatDetection`; reinstalare: zip-ul oficial de pe ngrok.com în `C:\ngrok`, apoi
  `ngrok.exe service install --config C:\ngrok\ngrok.yml` și `ngrok.exe service start`.
- Tailscale Funnel a fost încercat întâi, dar Tailscale nu a publicat adresa în DNS-ul public, așa că e oprit
  (`tailscale funnel reset`). Tailscale rămâne pentru administrare: SSH, QuestDB, `server.py` local.
- Cere **parolă** (`APP_PASSWORD` din `.env`-ul de pe VM). După login, sesiunea ține 30 de zile; schimbarea
  parolei delogează pe toată lumea. După 10 parole greșite de pe același IP, acel IP e blocat 15 minute.
  Delogare: `/logout`.
- Fără `APP_PASSWORD` în `.env` (cum e pe PC), serverul nu cere login.
- Pe VM serverul ascultă doar pe `127.0.0.1:8000`; din afară se ajunge la el numai prin ngrok (HTTPS).
- Task-ul „Nexus Server” îl pornește la boot (ca SYSTEM, prioritate BelowNormal, ca și colectorul);
  jurnal în `C:\Nexus Trading\server_stdout.log`.
- După o modificare a serverului sau a paginii: copiezi fișierele (`*.py`, `static/`) în `C:\Nexus Trading`
  și repornești task-ul „Nexus Server”. Nu suprascrie `.env`-ul de pe VM.
- Oprirea accesului public: pe VM, `C:\ngrok\ngrok.exe service stop`.

## Mașina virtuală (QuestDB + colector)
- Acces de pe PC: `ssh nexus-vm` (cheia `~/.ssh/nexus_vm`; configurarea SSH: `vm_setup/setup_vm_ssh.ps1`).
- QuestDB: `C:\questdb` (date în `C:\questdb\data`), consola web pe `http://<IP-ul VM-ului în Tailscale>:9000`
  (utilizator și parolă din `.env`). Porturile 9000 / 8812 sunt deschise doar pentru Tailscale.
- Colectorul: `C:\Nexus Trading` (`collector.py`, `tickdb.py`, `ctrader.py`, `.env`, `ctrader_token.json`), jurnal în
  `C:\Nexus Trading\collector.log`. După o modificare: copiezi fișierele cu `scp` și repornești task-ul
  „Nexus Collector”.
- Tokenul cTrader al VM-ului e **separat** de orice alt token (la reînnoire, tokenul vechi devine invalid).
  Un token nou: Playground → Get token, apoi pe PC
  `$env:CTRADER_TOKEN_FILE="ctrader_token_vm.json"; python ctrader_setup.py` și copiezi fișierul pe VM ca
  `C:\Nexus Trading\ctrader_token.json`.
- Tabelele: `ticks` (un rând pe tic), `tick_coverage` (zilele complete), `bars` (lumânările oficiale),
  `bar_status`, `symbols`. Interogări utile:
  ```sql
  SELECT symbol, count(), min(ts), max(ts) FROM ticks GROUP BY symbol;
  SELECT symbol, status, count(), min(day) FROM tick_coverage GROUP BY symbol, status;
  ```
- **Compresie**: zilele de ticuri mai vechi de 7 zile, în care ambele simboluri sunt complete, se convertesc
  automat (după fiecare completare a colectorului) în Parquet comprimat ZSTD nivel 3 (setat în `server.conf`):
  fără pierderi, ~6 octeți pe tic în loc de ~40 (de ~6 ori mai puțin). Zilele rămân în tabelul `ticks` și se
  citesc cu același SQL; doar interogările pe ele sunt puțin mai lente (zeci de ms). Zilele recente rămân
  necomprimate. Pragul se schimbă cu `COLLECT_COMPRESS_DAYS` în `.env`-ul de pe VM.
- Dacă VM-ul a fost oprit, la pornire colectorul completează singur golul din istoricul brokerului.
  Sub 3 GB liberi pe disc, completarea spre trecut se oprește (ticurile live continuă).
- VM-ul are un singur nucleu: QuestDB e reglat (`shared.worker.*`, `wal.apply.worker.*` în `server.conf`)
  ca firele să nu se învârtă în așteptare; altfel interogările la 50 ms ar ocupa tot procesorul.

## Alarme de preț
- Butonul **Alarme** din dreapta sus deschide panoul. **Dublu-click pe chart** deschide panoul cu prețul de acolo.
- Alegi prețul țintă, bid sau ask, adresa de e-mail și o notă. Direcția (peste/sub) se stabilește automat
  față de prețul curent.
- Alarmele sunt verificate **pe server, la fiecare tic**, deci funcționează și cu pagina închisă,
  cât timp `server.py` rulează. Se salvează în `alerts.json`.
- La declanșare: e-mail, plus notificare în pagină (cu sunet) dacă pagina e deschisă.

### Configurare e-mail
1. Copiază `.env.example` ca `.env`.
2. Completează datele SMTP. Pentru Gmail ai nevoie de o **Parolă pentru aplicații**
   (https://myaccount.google.com/apppasswords), nu de parola contului.
3. Repornește serverul și apasă „Trimite e-mail de test” din panou.

Fără `.env`, alarmele apar în pagină, dar e-mailul nu se trimite (panoul te avertizează).

## Istoric și export CSV
- Timeframe-uri: **Ticuri**, 1s, 5s, 15s, 30s (lumânări calculate în QuestDB din ticuri), M1, M5, M15, M30, H1, H4, D1, W1.
- **Ticuri**: chartul de sus arată fiecare tic ca o lumânare (bid), plus linia galbenă a ask-ului, cu ora la
  milisecundă pe axa de timp și în dreptul cursorului. Lumânarea unui tic merge de la bid-ul ticului anterior
  (deschidere) la bid-ul lui (închidere): verde = bid-ul a urcat, roșu = a coborât, liniuță = n-a schimbat
  (s-a schimbat doar ask-ul). Merge orice tip de afișare și indicatorii (o lumânare = un tic). Pornește cu ultimele
  5.000 de ticuri; la derularea spre stânga se încarcă câte 5.000 mai vechi.
- De la M1 în sus, chartul se deschide cu lumânările închise salvate de colector (lumânările oficiale cTrader)
  plus lumânarea în curs, calculată din ticuri. Când derulezi chartul spre stânga, lumânările mai vechi se
  încarcă automat, până la începutul celor salvate.
- Butonul **Export CSV** din dreapta sus: alegi simbolul, timeframe-ul (sau **ticuri** bid/ask),
  data de start și data de final (zile întregi, în ora ta locală). Fișierul se scrie pe PC-ul cu serverul,
  în `exporturi_csv/<SIMBOL>/<SIMBOL>_<TIMEFRAME>_<START>_<FINAL>.csv`; la final apare și un link de descărcare.
  Datele vin din QuestDB, deci acoperă doar perioada colectată.
- Coloane: `time_utc,open,high,low,close,volume` (volum = număr de ticuri), la ticuri `time_utc,bid,ask`.
  Orele din CSV sunt în **UTC**.

## Lumânările (datastore)
Lumânările închise M1–W1 le salvează colectorul în tabelul `bars` din QuestDB, după regulile:
- **prima dată**: ultimele **10.000** de lumânări închise (toate, dacă brokerul are mai puține);
- **actualizare**: la închiderea fiecărei lumânări (+2 s), lumânările noi se adaugă, iar ultimele 5 se
  rescriu (o lumânare corectată de broker se înlocuiește); cele vechi nu se șterg, deci seria crește;
- **completare spre trecut**: sub 10.000 de lumânări, până la capătul istoricului brokerului.

Serverul (`datastore.py`) le citește de acolo: chartul și indicatorii văd garantat aceleași lumânări.
Starea se vede pe chart, în stânga sus, lângă „ƒx Indicatori”: `M1 · 100% · 10.026 lumânări`.
Sub un minut (1s–30s) lumânările nu sunt salvate separat: le calculează QuestDB din ticuri (`SAMPLE BY`), ultimele
10.000 închise, iar chartul și indicatorii le folosesc la fel ca pe celelalte timeframe-uri.
Vechiul folder `datastore/` (CSV-uri pe PC) nu mai e folosit și poate fi șters.

## Indicatori
- Butonul **ƒx Indicatori** din stânga sus a chartului deschide meniul: cauți indicatorul, îl selectezi
  și apeși **Atașează** (sau dublu-click pe el). Un indicator atașat rămâne pe chart și după reîncărcarea
  paginii sau schimbarea simbolului / timeframe-ului.
- Indicatorii se calculează **pe server, din datastore-ul local**, doar pe lumânări închise (nu se
  redesenează), într-un proces separat (prețurile live nu încetinesc), cu rezultatul ținut în cache.
  Pagina trimite doar simbolul, timeframe-ul și parametrii; la fiecare lumânare închisă serverul
  recalculează și trimite singur rezultatul nou (sub un minut și pe „Ticuri”, cel mult o dată la 5 secunde).
- **Linii de trend**: rulează partea a 2-a din `analysis.py` pe ultimele 1000 de lumânări închise din
  datastore și desenează dreptele valide, prelungite până la lumânarea în curs. În dreapta sus apare
  sliderul de apropiere (la prima atașare 50%): la N% se văd doar liniile aflate la cel mult N% din
  intervalul de preț analizat față de prețul curent; 100% = toate. ↻ cere din nou rezultatul,
  ✕ detașează indicatorul.
- **Trend multi-timeframe** (1D · 1W · 1M · 1Y): rulează `timeframe_trends_indicator()` din partea 1 a
  `analysis.py`, neschimbată, pe **datastore-ul D1** al simbolului (`datastore/<sursă>/<SIMBOL>/D1/`), oricare
  ar fi timeframe-ul chartului; nu folosește niciodată prețul live. La atașare, la schimbarea simbolului și la
  închiderea fiecărei zile:
  1. caută datastore-ul D1; dacă nu există îl creează, dacă e învechit îl aduce la zi, după aceleași reguli ca
     restul datastore-ului (vezi mai sus);
  2. ia zilele închise din anul curent și le dă lui `analysis.py` în locul fișierului din `pd.read_csv`
     (aceleași coloane);
  3. afișează cele patru procente în dreapta sus.

  `analysis.py` primește **data zilei de tranzacționare** (`2026-10-01 00:00:00`) în loc de ora UTC a deschiderii
  din datastore (21:00 în ziua anterioară), altfel prima zi a lunii ar cădea în luna trecută; fișierul din
  datastore rămâne neschimbat. 1D = ultima zi închisă; 1Y = de la 1 ianuarie. Culoarea casetei: verde aprins
  spre +100%, roșu aprins spre −100%, stinsă spre 0. Cu mouse-ul peste o casetă (sau o atingere pe telefon) vezi
  detaliile calculului; sub casete scrie până la ce zi sunt datele. Marcajul din stânga arată alinierea „4/4”
  (toate de aceeași culoare, opțional peste un prag). O valoare pe care `analysis.py` nu o poate calcula (ex.
  `IndexError` în primele zile ale lunii, sau maxim = minim) apare „—”, cu motivul în detalii.
- Un indicator nou: calculul în `indicators.py` (o funcție + o intrare în `SPECS`) și desenul într-un
  fișier propriu din `static/`, cu `registerIndicator({...})`.

## Strategy Tester (backtest, ca în MetaTrader 5)
Butonul **Tester** din dreapta sus deschide Strategy Tester-ul într-o fereastră peste chart (pagina `/tester`): backtest-ul strategiei din `strategy.py`
(Nexus MA Trend, aceleași reguli ca `mt5/MQL5/Experts/Nexus/Nexus_MATrend_EA.mq5`), cu tab-urile din MT5:
- **Setări**: simbol (cele cu ticuri în QuestDB), timeframe M1–H4, perioada (start inclus, final exclus, UTC),
  forward (1/2, 1/3, 1/4, personalizat: deocamdată doar marcat pe grafic), modelling, depozit, monedă (USD / EUR),
  levier. Execuție ideală, fără comision și swap, stop out la 50%.
- **Parametri**: aceiași ca inputurile EA-ului (fără tipul MA: doar Simple, și fără desenarea liniilor).
- **Grafic** (balanță, equity, încărcarea depozitului), **Backtest** (statisticile raportului MT5),
  **Tranzacții** (deal-urile), **Jurnal**.

Modelling, ca în MT5:
- **Every tick based on real ticks**: toate ticurile reale bid / ask; SL / TP la prețul ticului care le atinge.
- **1 minute OHLC**: 4 prețuri pe fiecare M1 (urcătoare O→L→H→C, coborâtoare O→H→L→C, doji invers față de
  lumânarea anterioară; 1 tic = doar Close, 2 ticuri = Open, Close); ask = bid + spread-ul minim al minutului;
  SL / TP la prețul din ordin.
- **Open prices only**: strategia rulează doar la Open; SL / TP / stop out se verifică pe OHLC-ul lumânării
  testate, la prețul din ordin.

Cum lucrează (`backtester.py`, într-un proces separat):
- Ticurile vin din QuestDB și se păstrează pe disc în `tester_cache/<sursă>/<simbol>/` (o lună = un fișier;
  doar lunile terminate). Prima rulare pe o perioadă nouă descarcă ~20 s pe lună, următoarele durează secunde.
  Lumânările M1 și cele ale timeframe-ului testat se fac din ticuri (H4 aliniat ca la cTrader, de la 17:00 New York).
- Înainte de start se încarcă `InpTLBars` lumânări de încălzire (fereastra liniilor de trend).
- Semnalele: `strategy_fast.py`, copia rapidă a lui `verify_entry` / `verify_exit` (aceleași calcule, făcute
  incremental pentru toate lumânările deodată). SL / TP: aceleași calcule ca `transaction_limits`.
- Bucla de lumânări e compilată cu numba (`simcore.py`): 5 ani H1 „Open prices only” ~1 s (înainte ~65 s).
  Procesul testerului își încarcă funcțiile compilate când se deschide pagina /tester.
- Butonul **Verifică** din tab-ul Backtest rulează funcțiile ORIGINALE pe lumânări alese din perioadă și le compară
  cu copia rapidă.
- Ca EA-ul: o dată pe lumânare, la primul tic: SL / TP / stop out, apoi închiderea pe MA, apoi intrarea (o singură
  poziție); lotul pierde `InpRiskPct` % la SL, limitat de marja liberă; la final poziția rămasă se închide („end of test”).
- Ticurile lunilor terminate stau și necomprimate în `tester_cache/<sursă>/<simbol>/raw/` (bid și spread în puncte,
  10 octeți pe tic, ~1,2 GB pe 5 ani EURUSD): se citesc fără decomprimare.
- **Real ticks** / **1 minute OHLC** (optimizarea și testul complet, cont în USD) folosesc un index pe minute
  (`tester_cache/index/`, refolosit pentru perioadele încheiate, iar pentru cele până azi o oră): pentru fiecare
  lumânare și minut, minimul / maximul bid-ask și cea mai mare cădere / urcare. Un test sare peste minutele în care SL,
  TP sau stop out-ul nu pot fi atinse și citește tic cu tic doar minutul în care se închide poziția: aceleași tranzacții
  și același raport ca tic cu tic. Indexul nu copiază ticurile (le citește din `raw/`) și se construiește dintr-o
  singură trecere: test complet Real ticks EURUSD H4 2020 → 2026 (137 mil. de ticuri) ~5 s prima dată pe o perioadă,
  apoi ~1–2 s (înainte ~13 s); în optimizare ~5–15 ms pe test pe 5 ani (~2 800 de teste genetice în ~5–10 s).
  Lunile M1 terminate rămân în memoria procesului testerului între teste.
- **Optimizarea** (ca în MT5, criteriul Balance max): în Setări → Optimizare alegi *Completă* (toate combinațiile) sau
  *Genetică rapidă*; în Parametri bifezi parametrii și le dai Start / Pas / Stop. Rulează pe „Open prices only”, în
  paralel pe toate nucleele, pe modelling-ul din Setări (`optimizer.py`; fiecare test e `fastsim.simulate`).
  Algoritmul genetic: populația √N (64–256), elită 10%, turneu din 3, încrucișare uniformă, mutație 15%, inversie 5%,
  cache; cel puțin log2(N) generații (15–31), apoi oprire după o treime din ele fără un maxim nou. Pe 34 398 de
  combinații (EURUSD H1, 1 an) a găsit maximul global în 10 rulări din 10, cu ~8% din teste, în ~5 s; completă: ~40 s.
  Cu Forward, optimizarea folosește doar perioada de dinainte, iar toate seturile profitabile se rulează și pe forward.
  Tab-ul Optimizare: toate testele (sortabile, filtrabile, CSV) și graficul lor; clic pe un rând = testul complet cu
  acei parametri (grafic, statistici, tranzacții) pe toată perioada, pe perioada optimizată sau doar pe forward.
  Butoanele care apar peste rândul de sub mouse (și în cardul „Cel mai bun set”) rulează același set pe **Real ticks**
  sau pe 1 minute OHLC; rezultatele apar ca etichete OP / M1 / RT pe rând și se compară în bara din Grafic / Backtest.
- Cu „Open prices only” și „1 minute OHLC”, SL / TP se execută la prețul din ordin, iar equity-ul se calculează la
  acel preț (ca în MT5), nu la prețul de dincolo de stop: altfel un salt la știri ar arăta un drawdown peste 100%.

## Fișiere
- `server.py`: WebSocket-ul de prețuri către pagini, istoricul pentru chart, API-ul pentru alarme
- `questfeed.py`: sursa de prețuri a serverului: QuestDB de pe VM (ticuri live la 50 ms, istoric, lumânări)
- `tickdb.py`: legătura cu QuestDB (tabelele, scrierea pe loturi, interogările)
- `collector.py`: colectorul de pe VM (ticuri live, istoric de ticuri, lumânări oficiale, simboluri)
- `ctrader.py`: conexiunea cu cTrader Open API, folosită de colector (prețuri live, istoric, reînnoirea tokenului)
- `ctrader_setup.py`: configurarea inițială a tokenului și a contului cTrader
- `datastore.py`: lumânările închise citite din QuestDB (`load_bars`)
- `backtester.py`: Strategy Tester-ul (datele, modurile de modelling, simularea); `tester_report.py`: statisticile
  raportului; `strategy_fast.py`: copia rapidă a semnalelor din `strategy.py`; `simcore.py`: bucla simulării (numba);
  `fastsim.py`: simularea rapidă pentru optimizare;
  `optimizer.py`: optimizarea (completă / genetică);
  pagina: `static/tester.*`
- `indicators.py`: calculul indicatorilor pe server (rulează în procese separate)
- `mtf.py`: zilele anului din lumânările D1, pentru indicatorul „Trend multi-timeframe”
- `alerts.py`: salvarea și verificarea alarmelor, trimiterea e-mailurilor
- `push.py`: notificările push (cheia VAPID în `vapid_private.pem`, dispozitivele în `push_subscriptions.json`)
- `vm_setup/setup_vm_ssh.ps1`: configurarea SSH + Tailscale pe VM (rulat o dată, ca Administrator)
- `static/`: interfața (chart de lumânări, chart de ticuri, panoul de alarme, exportul CSV în `export.js`,
  meniul de indicatori în `indicators.js`, indicatorul „Linii de trend” în `trend.js`, „Trend
  multi-timeframe” în `mtf.js`, starea lumânărilor de pe chart în `datastore.js`)
- `exporturi_csv/`: fișierele CSV exportate (câte un subfolder pentru fiecare simbol)

## Notificări push (Windows / Android)
- În fereastra de alarme: **Notificări pe acest dispozitiv → Activează notificări** și permite în browser.
- Notificarea apare în centrul de notificări chiar dacă tab-ul e închis (browserul trebuie să ruleze în fundal).
- Fiecare dispozitiv primește alarmele setate pentru adresa de e-mail completată în fereastră
  (fără e-mail: toate alarmele).
- Necesită HTTPS: merge pe `http://localhost:8000` (pe calculatorul cu serverul) și pe o adresă HTTPS, de ex.
  prin Tailscale (`tailscale serve --bg 8000` dă o adresă `https://<calculator>.<tailnet>.ts.net`) sau ngrok.
  Pune adresa în `.env` la `HTTPS_URL`: pagina deschisă fără HTTPS (ex. `http://<IP>:8000`, unde chart-ul
  merge, dar push-ul nu) o recomandă automat.
- iPhone: întâi Share → Add to Home Screen, apoi activezi notificările din aplicația de pe ecranul principal.

## De reținut
- Prețurile sunt cele ale contului cTrader al colectorului de pe VM.
- Rata de actualizare e cea a feed-ului brokerului: un tic nou apare doar când brokerul trimite o cotație nouă.
  Pe pagină, un tic ajunge de obicei la ~50–150 ms după ora brokerului (colector → QuestDB → interogarea la 50 ms).
- Orele sunt afișate în ora locală a dispozitivului.
- La timeframe-urile sub un minut (1s–30s), lumânările se calculează din ticurile din QuestDB.
- Dacă se pierde legătura cu QuestDB (VM oprit, Tailscale oprit), serverul se reconectează
  singur, iar pagina afișează starea și își reîncarcă istoricul la revenire.
- Cu `HOST=0.0.0.0` (implicit), oricine din rețeaua ta locală poate deschide pagina și seta alarme.
  Pune `HOST=127.0.0.1` în `.env` dacă vrei acces doar de pe PC.
