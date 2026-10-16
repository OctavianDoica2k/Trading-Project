# Nexus Live Chart

Chart live cu prețuri actualizate la nivel de tic (timestamp la milisecundă), cu alarme de preț
care trimit e-mail. Merge pe desktop și pe telefon (din browser).

Sursa prețurilor se alege cu `PRICE_SOURCE` în `.env`:
- `ctrader`: **cTrader Open API**, prin internet. Nu are nevoie de niciun program de trading deschis.
- `mt5`: terminalul **MetaTrader 5** (ex. FBS), care trebuie să fie pornit și logat pe PC (doar Windows).

## Cerințe
- Python 3.10+
- Pentru `ctrader`: un cont cTrader (merge și un cont **demo**, gratuit) și o aplicație Open API aprobată
- Pentru `mt5`: Windows, cu terminalul MT5 pornit și logat

```bash
pip install -r requirements.txt
python ctrader_setup.py     # doar pentru ctrader, o singură dată (vezi mai jos)
python server.py
```

Cu `mt5`, orele din MT5 (ora serverului brokerului) sunt convertite automat în UTC, comparând
cel mai recent tic cu ora reală. Dacă piața e închisă la pornire, ora serverului se poate fixa
cu `MT5_UTC_OFFSET_HOURS` în `.env` (ex. `3` pentru FBS vara, `2` iarna).

## Configurare cTrader (o singură dată)
1. Intră pe https://openapi.ctrader.com/apps cu cTrader ID-ul tău și creează o aplicație.
   Aprobarea aplicației poate dura puțin.
2. Copiază **Client ID** și **Secret** în `.env` (vezi `.env.example`):
   `CTRADER_CLIENT_ID=...` și `CTRADER_CLIENT_SECRET=...`
3. La aplicație apasă **Playground**, alege scope **accounts** (doar citire) și **Get token**.
4. Rulează `python ctrader_setup.py` și lipește Access token și Refresh token. Scriptul îți arată
   conturile, îl alegi pe cel pentru prețuri și verifică un preț live.

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
- De la M1 în sus, chartul se deschide cu cel puțin un an de lumânări (cel mult 10.000; la M1–M30
  asta înseamnă mai puțin de un an). Când derulezi chartul spre stânga, lumânările mai vechi se
  încarcă automat, până la capătul istoricului brokerului.
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
De fiecare dată când alegi un simbol sau un timeframe (de la M1 în sus), lumânările se salvează
automat pe PC-ul cu serverul, în `datastore/`:

```
datastore/
  mt5/                  <- sursa prețurilor (ctrader sau mt5; prețurile și orele diferă între ele)
    EURUSD/             <- simbolul
      D1/               <- timeframe-ul
        EURUSD_D1.csv   <- lumânările: time_utc,open,high,low,close,volume (UTC, ca la export)
        info.json       <- ce conține fișierul: prima și ultima lumânare, câte sunt, ultima actualizare
      H1/
        EURUSD_H1.csv
        info.json
```

- Prima deschidere salvează istoricul încărcat de chart (cel puțin un an, cel mult 10.000 de lumânări).
- La următoarele se adaugă doar lumânările noi, până la zi. Dacă a trecut mult de la ultima salvare,
  lumânările lipsă se cer separat de la sursă, ca fișierul să nu aibă goluri.
- Se salvează doar lumânările închise; cea în curs apare la următoarea deschidere.
- Timeframe-urile sub un minut (1s–30s) nu se salvează (sunt construite din ticuri, doar ultimele minute).

## Indicatori
- Butonul **ƒx Indicatori** din stânga sus a chartului deschide meniul: cauți indicatorul, îl selectezi
  și apeși **Atașează** (sau dublu-click pe el). Un indicator atașat rămâne pe chart și după reîncărcarea
  paginii sau schimbarea simbolului / timeframe-ului.
- **Linii de trend**: rulează partea a 2-a din `analysis.py` pe lumânările vizibile (cel mult 300) și desenează
  dreptele valide. În dreapta sus apare sliderul de apropiere (la prima atașare 50%): la N% se văd doar
  liniile aflate la cel mult N% din intervalul de preț analizat față de prețul curent; 100% = toate.
  ↻ recalculează pe lumânările vizibile acum, ✕ detașează indicatorul.
- Un indicator nou se adaugă într-un fișier propriu din `static/`, cu `registerIndicator({...})`.

## Fișiere
- `server.py`: WebSocket-ul de prețuri către pagini, istoricul pentru chart, API-ul pentru alarme
- `ctrader.py`: conexiunea cu cTrader Open API (prețuri live, istoric, reconectare, reînnoirea tokenului)
- `mt5feed.py`: sursa de prețuri din terminalul MetaTrader 5 (aceleași funcții ca `ctrader.py`)
- `ctrader_setup.py`: configurarea inițială a tokenului și a contului
- `alerts.py`: salvarea și verificarea alarmelor, trimiterea e-mailurilor
- `push.py`: notificările push (cheia VAPID în `vapid_private.pem`, dispozitivele în `push_subscriptions.json`)
- `static/`: interfața (chart de lumânări, chart de ticuri, panoul de alarme, exportul CSV în `export.js`,
  meniul de indicatori în `indicators.js`, indicatorul „Linii de trend” în `trend.js`)
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
