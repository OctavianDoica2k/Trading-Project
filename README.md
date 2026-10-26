# Nexus Live Chart

Chart live cu prețuri actualizate la nivel de tic (timestamp la milisecundă), cu alarme de preț
care trimit e-mail. Merge pe desktop și pe telefon (din browser).

**Toate datele vin din baza de date QuestDB de pe mașina virtuală (VM)**, nu direct de la broker:

```
cTrader ──► collector.py (VM) ──► QuestDB (VM): ticuri, lumânări oficiale, simboluri
                                        ▲
                                        │ interogare la fiecare 50 ms (prin Tailscale)
PC:  server.py (questfeed.py) ──────────┘ ──► /ws ──► browser
```

- **Pe VM** rulează QuestDB (serviciu Windows) și `collector.py` (task „Nexus Collector”, pornește la boot).
  Colectorul salvează live fiecare tic cTrader al simbolurilor din `COLLECT_SYMBOLS` (acum EURUSD și
  XAUUSD), completează istoricul de ticuri de la `COLLECT_START` (acum 2026-01-01) și ține la zi
  lumânările oficiale M1–W1. Merge și cu PC-ul închis.
- **Pe PC** rulează doar `server.py`. Nu se conectează la cTrader: citește ticurile noi din QuestDB la
  fiecare 50 ms și tot istoricul la cerere. În aplicație apar doar simbolurile colectate pe VM.

## Cerințe
- Python 3.10+
- Tailscale pornit pe PC (VM-ul e accesibil doar prin Tailscale)
- În `.env`: `QUESTDB_HOST` (adresa VM-ului), `QUESTDB_USER`, `QUESTDB_PASSWORD`

```bash
pip install -r requirements.txt
python server.py
```

La pornire, serverul afișează adresele:
- pe acest PC: http://127.0.0.1:8000
- de pe telefon: `http://<IP-ul PC-ului>:8000` (aceeași rețea Wi-Fi; la prima pornire permite accesul în firewall-ul Windows)

**Telefon simulat pe desktop:** în Chrome/Edge apasă `F12`, apoi `Ctrl+Shift+M` și alege un telefon din listă.

## Mașina virtuală (QuestDB + colector)
- Acces de pe PC: `ssh nexus-vm` (cheia `~/.ssh/nexus_vm`; configurarea SSH: `vm_setup/setup_vm_ssh.ps1`).
- QuestDB: `C:\questdb` (date în `C:\questdb\data`), consola web pe `http://<IP-ul VM-ului în Tailscale>:9000`
  (utilizator și parolă din `.env`). Porturile 9000 / 8812 sunt deschise doar pentru Tailscale.
- Colectorul: `C:\nexus` (`collector.py`, `tickdb.py`, `ctrader.py`, `.env`, `ctrader_token.json`), jurnal în
  `C:\nexus\collector.log`. După o modificare: copiezi fișierele cu `scp` și repornești task-ul
  „Nexus Collector”.
- Tokenul cTrader al VM-ului e **separat** de orice alt token (la reînnoire, tokenul vechi devine invalid).
  Un token nou: Playground → Get token, apoi pe PC
  `$env:CTRADER_TOKEN_FILE="ctrader_token_vm.json"; python ctrader_setup.py` și copiezi fișierul pe VM ca
  `C:\nexus\ctrader_token.json`.
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
- Butonul **🔔** din dreapta sus deschide panoul. **Dublu-click pe chart** deschide panoul cu prețul de acolo.
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
- Butonul **💾 Export CSV** din dreapta sus: alegi simbolul, timeframe-ul (sau **ticuri** bid/ask),
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
Starea se vede pe chart, în stânga sus, lângă „ƒx Indicatori”: `💾 M1 · 100% · 10.026 lumânări`.
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

## Fișiere
- `server.py`: WebSocket-ul de prețuri către pagini, istoricul pentru chart, API-ul pentru alarme
- `questfeed.py`: sursa de prețuri a serverului: QuestDB de pe VM (ticuri live la 50 ms, istoric, lumânări)
- `tickdb.py`: legătura cu QuestDB (tabelele, scrierea pe loturi, interogările)
- `collector.py`: colectorul de pe VM (ticuri live, istoric de ticuri, lumânări oficiale, simboluri)
- `ctrader.py`: conexiunea cu cTrader Open API, folosită de colector (prețuri live, istoric, reînnoirea tokenului)
- `ctrader_setup.py`: configurarea inițială a tokenului și a contului cTrader
- `datastore.py`: lumânările închise citite din QuestDB (`load_bars`)
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
- Necesită HTTPS: merge pe `http://localhost:8000` (pe PC-ul cu serverul) și pe
  **https://calculator.tailnet.ts.net** (prin Tailscale, pornit cu `tailscale serve --bg 8000`).
  Adresa `http://100.x.y.z:8000` merge pentru chart, dar fără notificări push.
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
