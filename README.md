# Nexus Live Chart

Chart live cu prețurile din MetaTrader 5 (FBS), actualizat la nivel de tic (timestamp la milisecundă),
cu alarme de preț care trimit e-mail. Merge pe desktop și pe telefon (din browser).

## Cerințe
- Windows, cu terminalul **FBS MT5** pornit și logat
- Python 3.10+

```bash
pip install -r requirements.txt
python server.py
```

La pornire, serverul afișează adresele:
- pe acest PC: http://127.0.0.1:8000
- de pe telefon: `http://<IP-ul PC-ului>:8000` (aceeași rețea Wi-Fi; la prima pornire permite accesul în firewall-ul Windows)

**Telefon simulat pe desktop:** în Chrome/Edge apasă `F12`, apoi `Ctrl+Shift+M` și alege un telefon din listă.

## Alarme de preț
- Butonul **🔔** din dreapta sus deschide panoul. **Dublu-click pe chart** deschide panoul cu prețul de acolo.
- Alegi prețul țintă, bid sau ask, adresa de e-mail și o notă. Direcția (peste/sub) se stabilește automat
  față de prețul curent.
- Alarmele sunt verificate **pe server, la fiecare tic**, deci funcționează și cu pagina închisă,
  cât timp `server.py` și MT5 rulează. Se salvează în `alerts.json`.
- La declanșare: e-mail, plus notificare în pagină (cu sunet) dacă pagina e deschisă.

### Configurare e-mail
1. Copiază `.env.example` ca `.env`.
2. Completează datele SMTP. Pentru Gmail ai nevoie de o **Parolă pentru aplicații**
   (https://myaccount.google.com/apppasswords), nu de parola contului.
3. Repornește serverul și apasă „Trimite e-mail de test” din panou.

Fără `.env`, alarmele apar în pagină, dar e-mailul nu se trimite (panoul te avertizează).

## Fișiere
- `server.py`: conexiunea MT5, WebSocket-ul de prețuri, API-ul pentru alarme
- `alerts.py`: salvarea și verificarea alarmelor, trimiterea e-mailurilor
- `push.py`: notificările push (cheia VAPID în `vapid_private.pem`, dispozitivele în `push_subscriptions.json`)
- `static/`: interfața (chart de lumânări, chart de ticuri, panoul de alarme)

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
- Rata de actualizare e cea a feed-ului FBS: un tic nou apare doar când brokerul trimite o cotație nouă.
- Cu `HOST=0.0.0.0` (implicit), oricine din rețeaua ta locală poate deschide pagina și seta alarme.
  Pune `HOST=127.0.0.1` în `.env` dacă vrei acces doar de pe PC.
