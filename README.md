# Nexus Live Chart

Chart live cu prețuri actualizate la nivel de tic (timestamp la milisecundă), cu alarme de preț
care trimit e-mail. Merge pe desktop și pe telefon (din browser).

Sursa prețurilor se alege din lista din stânga sus a paginii (**MT5** / **cTrader**), fără repornirea
serverului. Alegerea e aceeași pentru toate paginile deschise și ține până la repornirea serverului;
serverul pornește mereu pe sursa din `PRICE_SOURCE` din `.env` (implicit `ctrader`):
- `ctrader`: **cTrader Open API**, prin internet. Nu are nevoie de niciun program de trading deschis.
- `mt5`: terminalul **MetaTrader 5** (ex. FBS), care trebuie să fie pornit și logat pe PC (doar Windows).

O sursă neconfigurată (ex. cTrader fără token) apare în listă ca „neconfigurat”; motivul se vede
la trecerea mouse-ului peste listă. Dacă sursa din `.env` nu se poate folosi la pornire, serverul
pornește cu cealaltă.

## Cerințe
- Python 3.10+
- Pentru `ctrader`: un cont cTrader (merge și un cont **demo**, gratuit) și o aplicație Open API aprobată
- Pentru `mt5`: Windows, cu terminalul MT5 pornit și logat

```bash
pip install -r requirements.txt
python ctrader_setup.py     # doar pentru ctrader, o singură dată (vezi mai jos)
python server.py
```

Cu `mt5`, orele din MT5 (ora serverului brokerului) sunt convertite în UTC **pentru fiecare lumânare și
fiecare tic în parte**, după regula de fus orar a brokerului: `MT5_SERVER_TZ` în `.env` (implicit
`Europe/Athens` = regula FBS, UTC+2 iarna și UTC+3 vara). Astfel o lumânare din ianuarie și una din
iulie primesc fiecare decalajul corect, oricând ar fi cerute, iar trecerea la ora de vară / iarnă nu
produce dubluri sau goluri. Pentru alt broker: un nume IANA (ex. `America/New_York`) sau un decalaj fix
(ex. `UTC+3`); la pornire serverul avertizează dacă regula nu se potrivește cu ora ultimului tic.

## Configurare cTrader (o singură dată)
1. Intră pe https://openapi.ctrader.com/apps cu cTrader ID-ul tău și creează o aplicație.
   Aprobarea aplicației poate dura puțin.
2. Copiază **Client ID** și **Secret** în `.env` (vezi `.env.example`):
   `CTRADER_CLIENT_ID=...` și `CTRADER_CLIENT_SECRET=...`
3. La aplicație apasă **Playground**, alege scope **accounts** (doar citire) și **Get token**.
4. Rulează `python ctrader_setup.py` și lipește Access token și Refresh token. Scriptul îți arată
   conturile, îl alegi pe cel pentru prețuri și verifică un preț live.
5. În pagină, alege **cTrader** din lista surselor (dacă serverul rula deja, nu trebuie repornit).

Tokenul se salvează în `ctrader_token.json` și **se reînnoiește automat** înainte să expire (~30 de zile).
Dacă vreodată e revocat, repeți pașii 3–4.

La pornire, serverul afișează adresele:
- pe acest PC: http://127.0.0.1:8000
- de pe telefon: `http://<IP-ul PC-ului>:8000` (aceeași rețea Wi-Fi; la prima pornire permite accesul în firewall-ul Windows)

**Telefon simulat pe desktop:** în Chrome/Edge apasă `F12`, apoi `Ctrl+Shift+M` și alege un telefon din listă.

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
- Timeframe-uri: 1s, 5s, 15s, 30s (construite din ticuri), M1, M5, M15, M30, H1, H4, D1, W1.
- De la M1 în sus, chartul se deschide cu ultimele 10.000 de lumânări închise **din datastore-ul local**
  (vezi mai jos) plus lumânarea în curs, de la sursă. Când derulezi chartul spre stânga, lumânările mai
  vechi se încarcă automat: întâi din datastore, apoi de la sursă, până la capătul istoricului brokerului.
- Cu `mt5`, terminalul păstrează cel mult „Max bars in chart” lumânări pe timeframe (implicit 100.000,
  adică ~3 luni de M1). Pentru perioadele mai vechi, lumânările M1/M5 se construiesc din ticuri
  (identice cu cele din MT5, dar mai lent). Ca să le iei direct din MT5: Tools → Options → Charts →
  Max bars in chart = Unlimited, apoi repornești terminalul.
- Butonul **💾 Export CSV** din dreapta sus: alegi simbolul, timeframe-ul (sau **ticuri** bid/ask),
  data de start și data de final (zile întregi, în ora ta locală). Fișierul se scrie pe PC-ul cu serverul,
  în `exporturi_csv/<SIMBOL>/<SIMBOL>_<TIMEFRAME>_<START>_<FINAL>.csv`
  (ex. `exporturi_csv/EURUSD/EURUSD_M1_2025-10-15_2026-10-15.csv`); la final apare și un link de descărcare.
- Coloane: `time_utc,open,high,low,close,volume` (volum = număr de ticuri), la ticuri `time_utc,bid,ask`.
  Orele din CSV sunt în **UTC**.
- Durate orientative (MT5, EURUSD): un an de H1 – o secundă; un an de M1 – câteva minute; o zi de
  ticuri ~220.000 de rânduri (un an de ticuri: zeci de milioane de rânduri, durează mult). Cu cTrader,
  ticurile vin în pagini limitate de server, deci exporturile de ticuri sunt mai lente.

## Datastore local (automat)
Simbolul și timeframe-ul deschise în pagină (de la M1 la W1) au o bază de date locală cu lumânările
închise, pe PC-ul cu serverul, în `datastore/`. Chartul (lumânările închise) și indicatorii citesc
din ea, deci văd garantat aceleași date:

```
datastore/
  mt5/                  <- sursa prețurilor (ctrader sau mt5; prețurile și orele diferă între ele)
    EURUSD/             <- simbolul
      D1/               <- timeframe-ul
        EURUSD_D1.csv   <- lumânările: time_utc,open,high,low,close,volume (UTC, ca la export)
        info.json       <- ce conține fișierul: prima și ultima lumânare, câte sunt, dacă e tot istoricul
      H1/
        EURUSD_H1.csv
        info.json
```

- **Prima deschidere** a unui simbol / timeframe descarcă ultimele **10.000** de lumânări închise (dacă
  sursa are mai puține, toate câte are). Chartul apare când descărcarea e gata.
- **Progresul** se vede live pe chart, în stânga sus, lângă „ƒx Indicatori”: `💾 Descarc M1: 40% · 4.000 / 10.000`
  cu bară de progres; apoi `💾 M1 · 100% · 10.000 lumânări` (la trecerea mouse-ului: fișierul, prima și
  ultima lumânare). 100% = baza de date e completă și indicatorii rulează pe ea.
- **Actualizare**: cât timp perechea e deschisă într-o pagină, la închiderea fiecărei lumânări (+3 s)
  serverul cere lumânarea oficială de la broker și o **adaugă la final**. Lumânările vechi nu se șterg,
  deci fișierul crește peste 10.000 (numărul de pe chart crește). La redeschidere, golul de când a fost
  închisă se completează dintr-o dată, deci fișierul nu are goluri.
- **Verificare**: la fiecare actualizare, ultimele 5 lumânări din fișier se compară cu sursa: o lumânare
  corectată de broker se rescrie, iar un fișier cu ore decalate se reface de la zero.
- **Completare spre trecut**: un fișier cu mai puțin de 10.000 de lumânări se completează spre trecut,
  dacă sursa are istoric mai vechi.
- Se salvează doar lumânările închise; nu se salvează ticuri (rămân doar în memorie, pentru chartul live).
- Timeframe-urile sub un minut (1s–30s) nu se salvează (sunt construite din ticuri), deci pe ele
  indicatorii nu rulează.

## Indicatori
- Butonul **ƒx Indicatori** din stânga sus a chartului deschide meniul: cauți indicatorul, îl selectezi
  și apeși **Atașează** (sau dublu-click pe el). Un indicator atașat rămâne pe chart și după reîncărcarea
  paginii sau schimbarea simbolului / timeframe-ului.
- Indicatorii se calculează **pe server, din datastore-ul local**, doar pe lumânări închise (nu se
  redesenează), într-un proces separat (prețurile live nu încetinesc), cu rezultatul ținut în cache.
  Pagina trimite doar simbolul, timeframe-ul și parametrii; la fiecare lumânare închisă serverul
  recalculează și trimite singur rezultatul nou. Pe 1s–30s indicatorii nu rulează.
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
- `ctrader.py`: conexiunea cu cTrader Open API (prețuri live, istoric, reconectare, reînnoirea tokenului)
- `mt5feed.py`: sursa de prețuri din terminalul MetaTrader 5 (aceleași funcții ca `ctrader.py`)
- `datastore.py`: baza de date locală cu lumânări (descărcare, actualizare, verificare, `load_bars`)
- `indicators.py`: calculul indicatorilor pe server (rulează în procese separate)
- `mtf.py`: zilele anului din datastore-ul D1, pentru indicatorul „Trend multi-timeframe”
- `ctrader_setup.py`: configurarea inițială a tokenului și a contului
- `alerts.py`: salvarea și verificarea alarmelor, trimiterea e-mailurilor
- `push.py`: notificările push (cheia VAPID în `vapid_private.pem`, dispozitivele în `push_subscriptions.json`)
- `static/`: interfața (chart de lumânări, chart de ticuri, panoul de alarme, exportul CSV în `export.js`,
  meniul de indicatori în `indicators.js`, indicatorul „Linii de trend” în `trend.js`, „Trend
  multi-timeframe” în `mtf.js`, starea
  datastore-ului de pe chart în `datastore.js`)
- `exporturi_csv/`: fișierele CSV exportate (câte un subfolder pentru fiecare simbol)
- `datastore/`: baza de date locală cu lumânări, pe sursă / simbol / timeframe (vezi mai sus)

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
- Prețurile sunt cele ale brokerului sursei alese (cTrader sau MT5), deci pot diferi ușor între ele.
- Rata de actualizare e cea a feed-ului brokerului: un tic nou apare doar când brokerul trimite o cotație nouă.
- Orele sunt afișate în ora locală a dispozitivului.
- La timeframe-urile sub un minut (1s–30s), istoricul se construiește din ticuri. Pe 15s/30s, într-o piață
  foarte activă, pot apărea mai puține lumânări la deschidere; restul se completează live.
- Dacă se pierde legătura cu sursa de prețuri (cTrader sau terminalul MT5), serverul se reconectează
  singur, iar pagina afișează starea și își reîncarcă istoricul la revenire.
- Cu `HOST=0.0.0.0` (implicit), oricine din rețeaua ta locală poate deschide pagina și seta alarme.
  Pune `HOST=127.0.0.1` în `.env` dacă vrei acces doar de pe PC.
