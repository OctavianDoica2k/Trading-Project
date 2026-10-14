// Chart live: lumânări (lightweight-charts) + chart de ticuri pe canvas.
// =====================================================================================
//  CE FACE ACEST FIȘIER (pe scurt)
// -------------------------------------------------------------------------------------
//  - Desenează chartul de lumânări (sus) cu biblioteca TradingView „lightweight-charts”.
//  - Desenează singur chartul de ticuri (jos), pe un <canvas>, pentru ultimele 600 de cotații.
//  - Afișează cotația curentă: bid, ask, spread, ora ultimului tic, ticuri pe secundă.
//  - Ține deschisă conexiunea WebSocket cu serverul (server.py): primește istoricul la
//    conectare („hello”), apoi fiecare tic nou („ticks”) și evenimentele de alarmă („alert”).
//  - Gestionează butoanele de timeframe și câmpul de simbol.
//
//  Fișierele alerts.js și push.js se încarcă DUPĂ acesta și folosesc direct variabilele
//  definite aici (state, chart, series, $, css, store, symInput).
// =====================================================================================

// Timeframe-urile disponibile: [durata unei lumânări în secunde, eticheta butonului].
const TFS = [
  [1, "1s"], [5, "5s"], [15, "15s"], [30, "30s"],
  [60, "M1"], [300, "M5"], [900, "M15"], [3600, "H1"], [14400, "H4"], [86400, "D1"],
];
// Câte ticuri păstrăm pentru chartul de ticuri (cele mai vechi se elimină).
const MAX_TICKS = 600;

// Scurtătură: $("bid") în loc de document.getElementById("bid").
const $ = (id) => document.getElementById(id);
// Citește o variabilă de culoare definită în style.css (ex. css("--up") => "#26a69a"),
// ca toate culorile să fie definite într-un singur loc.
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

// Memorie locală în browser (localStorage), pentru preferințe: ultimul simbol, timeframe, e-mail.
// try/catch: în unele situații (fereastră privată, setări stricte) accesul e interzis și ar
// arunca o eroare; atunci pur și simplu folosim valoarea implicită `d`.
// (`??` = „dacă rezultatul e null/undefined, folosește valoarea din dreapta”.)
const store = {
  get(k, d) { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* ignorat */ } },
};

// Starea curentă a paginii, într-un singur obiect.
//  - symbol : din adresa paginii (?symbol=XAUUSD, folosit de click-ul pe notificare),
//             altfel ultimul simbol folosit, altfel EURUSD.
//  - tf     : timeframe-ul în secunde.
//  - digits / point : zecimalele și pasul minim de preț ale simbolului (vin de la server).
//  - lastBar: lumânarea curentă (cea care se actualizează la fiecare tic).
//  - prevBid: bid-ul anterior, ca să colorăm prețul verde (a urcat) sau roșu (a scăzut).
let state = {
  symbol: (new URLSearchParams(location.search).get("symbol") || store.get("symbol", "EURUSD")).toUpperCase(),
  tf: Number(store.get("tf", 1)),
  digits: 5,
  point: 0.00001,
  lastBar: null,
  ticks: [],          // [time_msc, bid, ask]
  prevBid: null,
  arrivals: [],       // momente locale de recepție, pentru ticuri/s
};

// ---------- Chart de lumânări ----------
// Creăm chartul în elementul <section id="candles">. autoSize: își ajustează singur mărimea
// când se schimbă dimensiunea ferestrei. Culorile se iau din style.css.
const chart = LightweightCharts.createChart($("candles"), {
  autoSize: true,
  layout: { background: { color: css("--bg") }, textColor: css("--muted"), fontSize: 11 },
  grid: { vertLines: { color: css("--panel") }, horzLines: { color: css("--panel") } },
  rightPriceScale: { borderColor: css("--border") },
  // timeVisible/secondsVisible: axa de timp arată ore, minute și secunde.
  // rightOffset: 8 lumânări de spațiu liber în dreapta ultimei lumânări.
  timeScale: { borderColor: css("--border"), timeVisible: true, secondsVisible: true, rightOffset: 8 },
  crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
});
// Seria de lumânări: verde pentru lumânările care urcă, roșu pentru cele care coboară.
const series = chart.addCandlestickSeries({
  upColor: css("--up"), downColor: css("--down"),
  wickUpColor: css("--up"), wickDownColor: css("--down"), borderVisible: false,
});
// Linia punctată galbenă cu prețul ask (lumânările sunt desenate pe bid, ca în MetaTrader).
const askLine = series.createPriceLine({
  price: 0, color: css("--ask"), lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted,
  axisLabelVisible: true, title: "ask",
});

// Spune chartului câte zecimale să afișeze pe axa de preț (5 la EURUSD, 2 la aur etc.).
function applyDigits() {
  series.applyOptions({
    priceFormat: { type: "price", precision: state.digits, minMove: state.point },
  });
}

// Actualizează lumânarea curentă cu un tic nou (doar bid-ul contează pentru lumânări).
function onTick(msc, bid, ask) {
  // Începutul intervalului din care face parte ticul. Ex. tf=60 (M1): ticul de la 10:00:37
  // aparține lumânării care începe la 10:00:00.
  const sec = Math.floor(msc / 1000);
  const t = Math.floor(sec / state.tf) * state.tf;
  const b = state.lastBar;
  if (!b || t > b.time) {
    // Ticul deschide o lumânare nouă: toate cele 4 prețuri pornesc de la el.
    state.lastBar = { time: t, open: bid, high: bid, low: bid, close: bid };
  } else if (t === b.time) {
    // Ticul aparține lumânării curente: actualizăm maximul, minimul și închiderea.
    b.high = Math.max(b.high, bid);
    b.low = Math.min(b.low, bid);
    b.close = bid;
  } else {
    return; // tic mai vechi decât ultima lumânare
  }
  // series.update redesenează doar ultima lumânare (rapid). `{ ...obj }` = o copie a obiectului.
  series.update({ ...state.lastBar });
}

// ---------- Panou de cotații ----------
// Completează cu zerouri în față: pad(7) => "07", pad(5, 3) => "005".
const pad = (n, w = 2) => String(n).padStart(w, "0");
// Transformă un moment în milisecunde în text „HH:MM:SS.mmm”.
function fmtTime(msc) {
  // Timpul serverului MT5 e trimis „ca UTC”, deci îl afișăm cu getUTC*.
  const d = new Date(msc);
  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}.${pad(d.getUTCMilliseconds(), 3)}`;
}

// Actualizează zona de sus: bid, ask, spread, ora ultimului tic și linia ask de pe chart.
function updateQuote(msc, bid, ask) {
  const bidEl = $("bid"), askEl = $("ask");
  // toFixed(n) = afișare cu exact n zecimale.
  bidEl.textContent = bid.toFixed(state.digits);
  askEl.textContent = ask.toFixed(state.digits);
  // Colorăm prețurile verde/roșu după cum bid-ul a urcat sau a scăzut față de ticul anterior.
  if (state.prevBid !== null && bid !== state.prevBid) {
    const cls = bid > state.prevBid ? "up" : "down";
    bidEl.className = askEl.className = cls;
  }
  state.prevBid = bid;
  // Spread-ul în puncte = (ask − bid) / pasul minim de preț.
  $("spread").textContent = Math.round((ask - bid) / state.point);
  $("ticktime").textContent = fmtTime(msc);
  askLine.applyOptions({ price: ask });
}

// De 4 ori pe secundă recalculăm „ticuri/s”: numărăm ticurile sosite în ultima secundă.
// performance.now() = ceas precis al browserului, în milisecunde.
setInterval(() => {
  const now = performance.now();
  state.arrivals = state.arrivals.filter((t) => now - t < 1000);
  $("tps").textContent = state.arrivals.length;
}, 250);

// ---------- Chart de ticuri (canvas) ----------
// <canvas> = o suprafață pe care desenăm direct linii și text, prin „contextul 2D” (ctx).
const canvas = $("ticks");
const ctx = canvas.getContext("2d");
// dirty = „există deja o redesenare programată”; evită redesenări multiple în același cadru.
let dirty = false;

// Desenează întregul chart de ticuri: grila, axa de preț, orele, liniile bid/ask și ultimul punct.
function drawTicks() {
  dirty = false;
  // Pe ecranele cu scalare (ex. 125% în Windows), canvasul trebuie să aibă mai mulți pixeli
  // reali decât dimensiunea afișată, altfel desenul ar fi neclar. dpr = factorul de scalare.
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
    canvas.width = w * dpr; canvas.height = h * dpr;
  }
  // După setTransform putem desena în pixeli „CSS” obișnuiți; scalarea se face automat.
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const ticks = state.ticks;
  $("tickcount").textContent = ticks.length;
  if (ticks.length < 2) return;

  // Margini: 74px în dreapta pentru axa de preț, 24px sus pentru titlu, 20px jos pentru ore.
  const axisW = 74, top = 24, bottom = 20;
  const plotW = w - axisW - 8, plotH = h - top - bottom;
  // Găsim prețul minim (din bid) și maxim (din ask), ca liniile să încapă pe verticală.
  // (`[, b, a]` = ignoră primul element al ticului, timpul, și ia bid și ask.)
  let lo = Infinity, hi = -Infinity;
  for (const [, b, a] of ticks) { if (b < lo) lo = b; if (a > hi) hi = a; }
  // 8% spațiu liber sus și jos. Dacă prețul n-a variat deloc, folosim 5 puncte.
  const padY = (hi - lo) * 0.08 || state.point * 5;
  lo -= padY; hi += padY;
  // Funcții de conversie: indexul ticului -> poziția X; prețul -> poziția Y (sus = preț mare).
  const x = (i) => 8 + (i / (ticks.length - 1)) * plotW;
  const y = (p) => top + (1 - (p - lo) / (hi - lo)) * plotH;

  // grilă + axa de preț
  // 5 linii orizontale egal distanțate, cu prețul scris în dreapta fiecăreia.
  ctx.font = "11px ui-monospace, Consolas, monospace";
  ctx.textBaseline = "middle";
  ctx.strokeStyle = css("--panel");
  ctx.fillStyle = css("--muted");
  for (let k = 0; k <= 4; k++) {
    const p = lo + ((hi - lo) * k) / 4, yy = y(p);
    ctx.beginPath(); ctx.moveTo(8, yy); ctx.lineTo(8 + plotW, yy); ctx.stroke();
    ctx.fillText(p.toFixed(state.digits), w - axisW + 4, yy);
  }
  // etichete de timp (cu milisecunde)
  ctx.textBaseline = "alphabetic";
  ctx.textAlign = "center";
  const nLabels = Math.max(1, Math.floor(plotW / 130));  // ~130px per etichetă, fără suprapuneri
  for (let k = 0; k <= nLabels; k++) {
    const i = Math.round(((ticks.length - 1) * k) / nLabels);
    // Ținem eticheta la cel puțin 50px de margini, ca să nu fie tăiată.
    const xx = Math.min(Math.max(x(i), 50), 8 + plotW - 50);
    ctx.fillText(fmtTime(ticks[i][0]), xx, h - 5);
  }
  ctx.textAlign = "left";

  // Desenează o linie prin toate ticurile. idx: 1 = bid, 2 = ask.
  // Primul punct cu moveTo (mută „creionul”), restul cu lineTo (trage linia).
  const line = (idx, color) => {
    ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.beginPath();
    ticks.forEach((t, i) => (i ? ctx.lineTo(x(i), y(t[idx])) : ctx.moveTo(x(i), y(t[idx]))));
    ctx.stroke();
  };
  line(2, css("--ask"));
  line(1, css("--accent"));

  // Câte un punct la capătul fiecărei linii, pentru ultimul bid și ultimul ask.
  const [, lb, la] = ticks[ticks.length - 1];
  ctx.fillStyle = css("--accent");
  ctx.beginPath(); ctx.arc(x(ticks.length - 1), y(lb), 3, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = css("--ask");
  ctx.beginPath(); ctx.arc(x(ticks.length - 1), y(la), 3, 0, Math.PI * 2); ctx.fill();
}
// Programează o redesenare la următorul cadru al ecranului (requestAnimationFrame).
// Chiar dacă vin 50 de ticuri într-un cadru, desenăm o singură dată.
const requestDraw = () => { if (!dirty) { dirty = true; requestAnimationFrame(drawTicks); } };
// Redesenăm și când se schimbă mărimea canvasului (ex. redimensionarea ferestrei).
new ResizeObserver(requestDraw).observe(canvas);

// ---------- WebSocket ----------
// ws = conexiunea curentă; retry = numărul de reconectări eșuate la rând (pentru pauza dintre ele).
let ws = null, retry = 0, retryTimer = null;

// Afișează starea conexiunii („live” verde / „deconectat” roșu) în colțul de sus.
function setStatus(on, text) {
  const el = $("status");
  el.className = on ? "on" : "off";
  el.textContent = text;
}

// Deschide (sau redeschide) conexiunea WebSocket pentru simbolul și timeframe-ul curente.
// Se apelează la pornire, la schimbarea simbolului/timeframe-ului și la reconectare.
function connect() {
  clearTimeout(retryTimer);
  // Închidem conexiunea veche fără să declanșăm reconectarea ei automată (onclose = null).
  if (ws) { ws.onclose = null; ws.close(); }
  // Pe o pagină HTTPS (ex. prin Tailscale) browserul cere conexiune securizată: wss://.
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const url = `${proto}://${location.host}/ws?symbol=${encodeURIComponent(state.symbol)}&tf=${state.tf}`;
  const sock = new WebSocket(url);
  ws = sock;
  setStatus(false, "conectare...");

  // Fiecare mesaj de la server e un text JSON cu un câmp `type` care spune ce conține.
  sock.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "hello") {
      // Primul mesaj după conectare: informațiile simbolului + istoricul.
      retry = 0;
      state.symbol = msg.symbol;
      state.digits = msg.digits;
      state.point = msg.point;
      state.ticks = [];
      state.prevBid = null;
      applyDigits();
      // Înlocuim toate lumânările din chart cu istoricul primit.
      series.setData(msg.bars);
      state.lastBar = msg.bars.length ? { ...msg.bars[msg.bars.length - 1] } : null;
      chart.timeScale().scrollToRealTime();
      document.title = `${msg.symbol} · Nexus Live Chart`;
      // Chartul de ticuri pornește cu ultimele ticuri din istoric (sau doar cu ultimul tic).
      state.ticks = msg.recent.length ? msg.recent : (msg.last ? [msg.last] : []);
      // `...msg.last` desface lista [timp, bid, ask] în trei argumente separate.
      if (msg.last) updateQuote(...msg.last);
      setStatus(true, "live");
      requestDraw();
      // Anunțăm alerts.js că s-a încărcat un simbol (dacă a definit funcția; `?.` = doar dacă există).
      window.onSymbolLoaded?.();
    } else if (msg.type === "alert") {
      // O alarmă s-a declanșat sau s-a actualizat: o tratează alerts.js.
      window.onAlertEvent?.(msg.alert);
    } else if (msg.type === "ticks") {
      // Unul sau mai multe ticuri noi.
      const now = performance.now();
      for (const [msc, bid, ask] of msg.d) {
        if (bid > 0) onTick(msc, bid, ask);
        state.ticks.push([msc, bid, ask]);
        state.arrivals.push(now);
      }
      // Păstrăm doar ultimele MAX_TICKS ticuri (splice elimină cele mai vechi de la început).
      if (state.ticks.length > MAX_TICKS) state.ticks.splice(0, state.ticks.length - MAX_TICKS);
      const [msc, bid, ask] = msg.d[msg.d.length - 1];
      updateQuote(msc, bid, ask);
      requestDraw();
    } else if (msg.type === "error") {
      // Ex. simbol inexistent: afișăm mesajul și NU mai încercăm reconectarea.
      setStatus(false, msg.msg);
      sock.onclose = null;
    }
  };
  // Conexiunea a căzut (server oprit, internet întrerupt): reîncercăm cu pauze tot mai mari –
  // 1s, 2s, 4s, 8s, apoi maximum 10s – ca să nu bombardăm serverul.
  sock.onclose = () => {
    setStatus(false, "deconectat — reîncerc");
    retryTimer = setTimeout(connect, Math.min(1000 * 2 ** retry++, 10000));
  };
}

// ---------- Controale ----------
// Creăm câte un buton pentru fiecare timeframe din TFS. La click: îl reținem, îl marcăm
// ca selectat și ne reconectăm (serverul trimite istoricul pentru noul timeframe).
const tfBox = $("tfs");
for (const [tf, label] of TFS) {
  const b = document.createElement("button");
  b.textContent = label;
  b.dataset.tf = tf;
  b.onclick = () => { state.tf = tf; store.set("tf", tf); markTf(); connect(); };
  tfBox.appendChild(b);
}
// Evidențiază (clasa CSS "on") butonul timeframe-ului curent.
function markTf() {
  for (const b of tfBox.children) b.classList.toggle("on", Number(b.dataset.tf) === state.tf);
}

// Câmpul de simbol: la schimbare (Enter sau click în afara lui) trecem pe noul simbol.
const symInput = $("symbol");
symInput.value = state.symbol;
symInput.addEventListener("change", () => {
  const s = symInput.value.trim().toUpperCase();
  if (s && s !== state.symbol) { state.symbol = s; store.set("symbol", s); connect(); }
});
// Enter scoate cursorul din câmp, ceea ce declanșează evenimentul "change" de mai sus.
symInput.addEventListener("keydown", (e) => { if (e.key === "Enter") symInput.blur(); });

// Cerem lista de simboluri și o punem ca sugestii (<datalist>) pentru câmpul de simbol:
// întâi cele din Market Watch, apoi restul.
fetch("/api/symbols").then((r) => r.json()).then(({ visible, all }) => {
  const dl = $("symbols");
  for (const s of [...visible, ...all.filter((x) => !visible.includes(x))]) {
    const o = document.createElement("option"); o.value = s; dl.appendChild(o);
  }
}).catch(() => {});

// Pornirea: marcăm timeframe-ul salvat și ne conectăm la server.
markTf();
connect();
