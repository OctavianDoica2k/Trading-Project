// Chart live: lumânări (lightweight-charts) + chart de ticuri pe canvas.
// =====================================================================================
//  CE FACE ACEST FIȘIER (pe scurt)
// -------------------------------------------------------------------------------------
//  - Desenează chartul de lumânări (sus) cu biblioteca TradingView „lightweight-charts”.
//  - Desenează singur chartul de ticuri (jos), pe un <canvas>, pentru ultimele 600 de cotații.
//  - Afișează cotația curentă: bid, ask, spread, ora ultimului tic, ticuri pe secundă.
//  - Ține deschisă conexiunea WebSocket cu serverul (server.py): primește istoricul la
//    conectare („hello”), apoi fiecare tic nou („ticks”) și evenimentele de alarmă („alert”).
//    De la M1 în sus, înainte de hello vine progresul datastore-ului local („store”, afișat de
//    datastore.js), iar indicatorii primesc rezultatele calculate pe server („indicator”).
//  - Gestionează listele de timeframe și de tip de afișare (lumânări, Heikin-Ashi, linie...).
//  - Timeframe-ul „Ticuri” (tf = 0): chartul de sus arată fiecare tic ca o lumânare (bid), plus
//    linia ask. Lumânarea unui tic merge de la bid-ul ticului anterior (open) la bid-ul lui (close),
//    deci corpul arată mișcarea adusă de acel tic. Lightweight-charts lucrează doar cu secunde
//    întregi, deci fiecare tic primește un „timp” fals = numărul lui de ordine (TICK_BASE + i),
//    iar ora reală, cu milisecunde, se ia din tickMs la afișare (axa de timp, crosshair).
//
//  Fișierele alerts.js și push.js se încarcă DUPĂ acesta și folosesc direct variabilele
//  definite aici (state, chart, series, $, css, store, selectSymbol). Meniul de simboluri
//  e în symbols.js (loadSymbols, apelată aici la schimbarea sursei de prețuri).
// =====================================================================================

// Timeframe-urile disponibile: [durata unei lumânări în secunde, eticheta butonului]. 0 = ticuri.
const TFS = [
  [0, "Ticuri"], [1, "1s"], [5, "5s"], [15, "15s"], [30, "30s"],
  [60, "M1"], [300, "M5"], [900, "M15"], [1800, "M30"], [3600, "H1"], [14400, "H4"], [86400, "D1"],
  [604800, "W1"],
];
// Tipurile de afișare a chartului: [id, eticheta din listă].
const CHART_TYPES = [
  ["candles", "Lumânări japoneze"], ["heikin", "Heikin-Ashi"], ["bars", "Bare OHLC"],
  ["line", "Linie"], ["area", "Arie"],
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
  source: null,       // sursa prețurilor ("mt5" / "ctrader"), din ultimul hello
  symbol: (new URLSearchParams(location.search).get("symbol") || store.get("symbol", "EURUSD")).toUpperCase(),
  tf: Number(store.get("tf", 1)),
  chartType: store.get("chartType", "candles"),   // unul dintre CHART_TYPES
  digits: 5,
  point: 0.00001,
  lastBar: null,
  loaded: false,      // a sosit istoricul (hello) pe conexiunea curentă
  ticks: [],          // [time_msc, bid, ask]
  prevBid: null,
  arrivals: [],       // momente locale de recepție, pentru ticuri/s
};

// Completează cu zerouri în față: pad(7) => "07", pad(5, 3) => "005".
const pad = (n, w = 2) => String(n).padStart(w, "0");

// ---------- Timeframe-ul „Ticuri” ----------
// Pe „Ticuri”, „timpul” unui punct de pe chart e numărul de ordine al ticului (de la TICK_BASE);
// tickMs dă ora lui reală (ms UTC), msTick invers. Ticurile mai vechi (la derulare) primesc numere
// mai mici. Serverul (indicatorii) folosește ora reală; msTick o traduce în poziția de pe chart.
const TICK_BASE = 1_000_000_000;
const tickMs = new Map();
const msTick = new Map();
const setTick = (t, ms) => { tickMs.set(t, ms); msTick.set(ms, t); };
let askData = [];          // linia ask: [{time, value}], aceleași numere de ordine ca rawBars
let askSeries = null;
const tickMode = () => state.tf === 0;
// Tipul de afișare (lumânări, Heikin-Ashi, bare, linie, arie), același și pe „Ticuri”.
const chartType = () => state.chartType;
// Lumânarea unui tic: de la bid-ul anterior (prev) la bid-ul lui; primul tic, fără anterior, e o liniuță.
const tickBar = (t, prev, bid) => ({
  time: t, open: prev ?? bid, high: Math.max(prev ?? bid, bid), low: Math.min(prev ?? bid, bid), close: bid,
});

// Timpii vin de la server în UTC; îi afișăm în ora locală a dispozitivului.
// Pentru chartul de lumânări, `t` e în secunde (cum îl folosește lightweight-charts).
const MONTHS = ["ian", "feb", "mar", "apr", "mai", "iun", "iul", "aug", "sep", "oct", "nov", "dec"];
function fmtBarTime(t) {
  if (tickMode()) {
    const ms = tickMs.get(t);
    if (ms === undefined) return "";
    const d = new Date(ms);
    return `${pad(d.getDate())} ${MONTHS[d.getMonth()]} ${fmtTime(ms)}`;
  }
  const d = new Date(t * 1000);
  return `${pad(d.getDate())} ${MONTHS[d.getMonth()]} ${d.getFullYear()} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}
// Etichetele de pe axa de timp. `type` spune ce fel de reper e (vezi TickMarkType în
// documentația lightweight-charts): 0 = an, 1 = lună, 2 = zi, 3 = oră, 4 = oră cu secunde.
function fmtTickMark(t, type) {
  if (tickMode()) {
    const ms = tickMs.get(t);
    return ms === undefined ? "" : fmtTime(ms);
  }
  const d = new Date(t * 1000);
  switch (type) {
    case 0: return String(d.getFullYear());
    case 1: return MONTHS[d.getMonth()];
    case 2: return String(d.getDate());
    case 3: return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
    default: return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  }
}

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
  timeScale: {
    borderColor: css("--border"), timeVisible: true, secondsVisible: true, rightOffset: 8,
    tickMarkFormatter: fmtTickMark,
  },
  // Ora din dreptul cursorului (crosshair), tot în ora locală.
  localization: { timeFormatter: fmtBarTime },
  crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
});
// Seria de lumânări: verde pentru lumânările care urcă, roșu pentru cele care coboară.
const series = chart.addCandlestickSeries({
  upColor: css("--up"), downColor: css("--down"),
  wickUpColor: css("--up"), wickDownColor: css("--down"), borderVisible: false,
});
// Linia punctată galbenă cu prețul ask (lumânările sunt desenate pe bid, ca în cTrader și MetaTrader).
const askLine = series.createPriceLine({
  price: 0, color: css("--ask"), lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted,
  axisLabelVisible: true, title: "ask",
});

// Spune chartului câte zecimale să afișeze pe axa de preț (5 la EURUSD, 2 la aur etc.).
function applyDigits() {
  const opts = { priceFormat: { type: "price", precision: state.digits, minMove: state.point } };
  series.applyOptions(opts);
  overlay?.applyOptions(opts);
}

// ---------- Tipul de afișare ----------
// rawBars = lumânările reale (bid). `series` le conține mereu pe aceleași momente de timp, așa că
// alarmele, liniile de trend și indicatorii (care lucrează cu `series`) merg la fel la orice tip:
//  - Lumânări japoneze: `series` desenează lumânările reale.
//  - Heikin-Ashi: `series` desenează lumânările calculate pe server de heikin_ashi() din
//    heikin_aishi.py (haBars). Cât timp nu le avem (sau funcția dă eroare), apar cele reale.
//  - Bare / Linie / Arie: `series` rămâne cu lumânările reale, dar transparentă, iar o a doua
//    serie (overlay) le desenează în forma aleasă.
let rawBars = [];
let haBars = null;
let overlay = null;
// haGen crește la fiecare istoric / tip nou: un răspuns Heikin-Ashi pentru date vechi e ignorat.
// haBusy / haAgain: o singură cerere odată (încă una la final, dacă între timp au venit ticuri).
// haFailed: funcția a dat eroare; nu mai cerem până la următorul istoric sau tip ales.
let haGen = 0, haBusy = false, haAgain = false, haFailed = false, haTimer = null;

// O lumânare în forma cerută de seria overlay: întreagă pentru bare OHLC, doar închiderea
// (time + value) pentru linie și arie.
const overlayPoint = (b) => (chartType() === "bars" ? { ...b } : { time: b.time, value: b.close });

// Redesenează toate lumânările după tipul de afișare curent.
function showBars() {
  series.setData(chartType() === "heikin" && haBars ? haBars : rawBars);
  overlay?.setData(rawBars.map(overlayPoint));
  askSeries?.setData(askData);
}

// Cere serverului lumânările Heikin-Ashi pentru lumânările date.
async function fetchHeikin(bars) {
  const r = await fetch("/api/heikin_ashi", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ bars }),
  });
  const data = await r.json();
  if (!r.ok) throw new Error(data.detail || r.statusText);
  return data.bars;
}

// Câte lumânări trimitem după primul calcul. Open-ul Heikin-Ashi e media dintre open-ul și
// close-ul lumânării anterioare, deci influența lumânărilor mai vechi se înjumătățește la fiecare
// lumânare: după 64 de lumânări e sub precizia numerelor (2^-64), iar rezultatul e același ca pe tot
// istoricul. Așa, la fiecare secundă trimitem serverului 64 de lumânări, nu toate.
const HA_TAIL = 64;

// Calculează (pe server) Heikin-Ashi și desenează rezultatul. Prima dată pe tot istoricul de pe
// chart; apoi doar pe ultimele HA_TAIL lumânări, din care înlocuim lumânările noi / în curs.
async function refreshHeikin() {
  if (chartType() !== "heikin" || !rawBars.length || haFailed) return;
  if (haBusy) { haAgain = true; return; }
  haBusy = true;
  const gen = haGen;   // dacă haGen se schimbă cât așteptăm, răspunsul e pentru date vechi
  const prevLen = haBars?.length ?? 0;
  // Dacă rămânem în urmă cu mai mult de HA_TAIL lumânări (sau la început), recalculăm tot.
  const from = prevLen && rawBars.length - HA_TAIL <= prevLen - 1 ? Math.max(0, rawBars.length - HA_TAIL) : 0;
  try {
    const part = await fetchHeikin(rawBars.slice(from));
    if (gen !== haGen || !part.length) return;
    // Calcul complet (sau istoric schimbat): înlocuim toate lumânările Heikin-Ashi.
    if (from === 0 && !(prevLen && haBars[0].time === part[0].time && part.length >= prevLen)) {
      haBars = part;
      series.setData(haBars);
    } else {
      // part[j] corespunde lumânării rawBars[from + j]; redesenăm de la ultima lumânare afișată.
      const start = Math.max(prevLen - 1, from);
      haBars = haBars.slice(0, start).concat(part.slice(start - from));
      for (let i = start; i < haBars.length; i++) series.update(haBars[i]);
      if (haBars.length > prevLen) window.onNewBar?.();
    }
  } catch (e) {
    if (gen !== haGen) return;
    haFailed = true;
    toast("Heikin-Ashi indisponibil", `${e.message}. Afișez lumânările japoneze.`);
  } finally {
    haBusy = false;
    if (haAgain) { haAgain = false; scheduleHeikin(); }
  }
}
// La ticuri cerem Heikin-Ashi cel mult o dată pe secundă.
function scheduleHeikin() {
  if (!haTimer) haTimer = setTimeout(() => { haTimer = null; refreshHeikin(); }, 1000);
}

// Aplică tipul de afișare ales: culorile lui `series`, seria overlay și datele.
function applyChartType() {
  if (overlay) { chart.removeSeries(overlay); overlay = null; }
  if (askSeries) { chart.removeSeries(askSeries); askSeries = null; }
  // Pe „Ticuri”, linia ask se desenează peste lumânările bid.
  if (tickMode()) {
    askSeries = chart.addLineSeries({
      color: css("--ask"), lineWidth: 1, lastValueVisible: false, priceLineVisible: false,
    });
  }
  const t = chartType();
  // La bare / linie / arie, lumânările din `series` devin transparente (le desenează overlay-ul).
  const hidden = t === "bars" || t === "line" || t === "area";
  const up = hidden ? "rgba(0,0,0,0)" : css("--up"), down = hidden ? "rgba(0,0,0,0)" : css("--down");
  series.applyOptions({ upColor: up, downColor: down, wickUpColor: up, wickDownColor: down, lastValueVisible: !hidden });
  if (t === "bars") {
    overlay = chart.addBarSeries({ upColor: css("--up"), downColor: css("--down"), thinBars: false });
  } else if (t === "line") {
    overlay = chart.addLineSeries({ color: css("--accent"), lineWidth: 2 });
  } else if (t === "area") {
    overlay = chart.addAreaSeries({
      lineColor: css("--accent"), lineWidth: 2,
      topColor: "rgba(76, 141, 255, .35)", bottomColor: "rgba(76, 141, 255, 0)",
    });
  }
  applyDigits();
  haBars = null; haGen++; haFailed = false;
  showBars();
  refreshHeikin();
}

// Ultima lumânare s-a schimbat (tic nou): o salvăm în rawBars și o redesenăm.
function drawLastBar(isNew) {
  const b = { ...state.lastBar };
  if (isNew) rawBars.push(b); else rawBars[rawBars.length - 1] = b;
  if (chartType() === "heikin" && haBars) scheduleHeikin();
  else series.update({ ...b });
  overlay?.update(overlayPoint(b));
}

// Pe „Ticuri”: fiecare tic e o lumânare nouă (bid), iar ask-ul un punct nou pe linia galbenă.
function onTickPoint(msc, bid, ask) {
  const prev = rawBars[rawBars.length - 1];
  const t = prev ? prev.time + 1 : TICK_BASE;
  setTick(t, msc);
  state.lastBar = tickBar(t, prev?.close, bid);
  drawLastBar(true);
  // Indicatorii își prelungesc liniile până la noua lumânare (vezi indicators.js).
  if (prev) window.onNewBar?.();
  const a = { time: t, value: ask };
  askData.push(a);
  askSeries?.update(a);
}

// Actualizează lumânarea curentă cu un tic nou (doar bid-ul contează pentru lumânări).
function onTick(msc, bid, ask) {
  if (tickMode()) { onTickPoint(msc, bid, ask); return; }
  // Începutul intervalului din care face parte ticul. Ex. tf=60 (M1): ticul de la 10:00:37
  // aparține lumânării care începe la 10:00:00. Numărăm intervalele de la ultima lumânare
  // primită de la server, ca să respectăm alinierea ei (ex. D1 care începe la ora brokerului).
  const sec = Math.floor(msc / 1000);
  const b = state.lastBar;
  const base = b ? b.time : 0;
  const t = base + Math.floor((sec - base) / state.tf) * state.tf;
  if (!b || t > b.time) {
    // Ticul deschide o lumânare nouă: toate cele 4 prețuri pornesc de la el.
    state.lastBar = { time: t, open: bid, high: bid, low: bid, close: bid };
    drawLastBar(true);
    // Indicatorii își prelungesc liniile până la noua lumânare (vezi indicators.js).
    if (b) window.onNewBar?.();
    return;
  } else if (t === b.time) {
    // Ticul aparține lumânării curente: actualizăm maximul, minimul și închiderea.
    b.high = Math.max(b.high, bid);
    b.low = Math.min(b.low, bid);
    b.close = bid;
  } else {
    return; // tic mai vechi decât ultima lumânare
  }
  // Redesenăm doar ultima lumânare (rapid).
  drawLastBar(false);
}

// Pe „Ticuri”, serverul trimite rezultatele indicatorilor cu ora reală a ticurilor (ms); le
// trecem pe pozițiile de pe chart (msTick). Punctele / liniile cu ticuri care nu sunt pe chart
// (mai vechi decât ce s-a încărcat) se lasă deoparte.
function tickTimes(msg) {
  const r = msg.result;
  if (!r) return msg;
  const out = { ...r };
  if (r.points) {
    out.points = r.points.filter((p) => msTick.has(p.time)).map((p) => ({ ...p, time: msTick.get(p.time) }));
  }
  const pt = (p) => ({ ...p, time: msTick.get(p.time) });
  const line = (l) => ({ ...l, from: pt(l.from), via: pt(l.via), to: pt(l.to) });
  const known = (l) => [l.from, l.via, l.to].every((p) => msTick.has(p.time));
  if (r.max) out.max = r.max.filter(known).map(line);
  if (r.min) out.min = r.min.filter(known).map(line);
  if (r.last !== undefined && msTick.has(r.last)) out.last = msTick.get(r.last);
  return { ...msg, result: out };
}

// ---------- Panou de cotații ----------
// Transformă un moment în milisecunde în text „HH:MM:SS.mmm”.
function fmtTime(msc) {
  // Timpul vine în UTC; get* (fără UTC) îl afișează în ora locală.
  const d = new Date(msc);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${pad(d.getMilliseconds(), 3)}`;
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
let ws = null, retry = 0, retryTimer = null, helloTimer = null;

// Trimite un mesaj serverului pe conexiunea curentă (ex. cererea unui indicator). Întoarce false
// dacă nu există încă o conexiune deschisă (indicatorii își retrimit cererea la următorul hello).
function sendWS(obj) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return false;
  ws.send(JSON.stringify(obj));
  return true;
}

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
  state.loaded = false;
  setStatus(false, "conectare...");
  // Indicatorul datastore-ului de pe chart: „se verifică” până la primul mesaj (datastore.js).
  window.onStoreStatus?.({ state: "pending" });
  // Plasă de siguranță: dacă istoricul nu vine în 40 s (serverul are propria limită de 30 s),
  // închidem conexiunea; onclose de mai jos reîncearcă, deci pagina nu rămâne în „conectare...”.
  // Fiecare mesaj de progres al datastore-ului repornește termenul (o descărcare mare durează).
  const armHelloTimer = () => {
    clearTimeout(helloTimer);
    helloTimer = setTimeout(() => {
      if (ws === sock && !sock.gotHello) { sock.errorMsg = "fără răspuns de la server"; sock.close(); }
    }, 40000);
  };
  armHelloTimer();

  // Fiecare mesaj de la server e un text JSON cu un câmp `type` care spune ce conține.
  sock.onmessage = (ev) => {
    if (ws !== sock) return;   // mesaj întârziat de pe o conexiune veche
    const msg = JSON.parse(ev.data);
    if (msg.type === "hello") {
      // Primul mesaj după conectare: informațiile simbolului + istoricul.
      retry = 0;
      sock.gotHello = true;
      clearTimeout(helloTimer);
      // Altă sursă decât la conexiunea anterioară: alt broker, deci alte simboluri sugerate.
      if (msg.source !== state.source) { state.source = msg.source; loadSymbols(); loadSources(); }
      state.symbol = msg.symbol;
      $("symName").textContent = msg.symbol;
      $("symBtn").title = msg.description || msg.symbol;
      state.digits = msg.digits;
      state.point = msg.point;
      state.ticks = [];
      state.prevBid = null;
      applyDigits();
      // Înlocuim toate lumânările din chart cu istoricul primit.
      // Istoric nou: istoricul mai vechi se încarcă din nou, de la zero, la derulare.
      // (Înainte de setData, care poate cere imediat lumânări mai vechi.)
      older = { loading: false, done: false, retryAt: 0 };
      tickMs.clear(); msTick.clear();
      askData = [];
      if (tickMode()) {
        // „Ticuri”: fiecare tic devine o lumânare (bid), cu numărul de ordine drept „timp”.
        rawBars = msg.ticks.map(([ms, bid, ask], i) => {
          setTick(TICK_BASE + i, ms);
          askData.push({ time: TICK_BASE + i, value: ask });
          return tickBar(TICK_BASE + i, i ? msg.ticks[i - 1][1] : undefined, bid);
        });
      } else {
        rawBars = msg.bars.map((b) => ({ ...b }));
      }
      haBars = null; haGen++; haFailed = false;
      showBars();
      refreshHeikin();
      state.lastBar = rawBars.length ? { ...rawBars[rawBars.length - 1] } : null;
      // Sărim direct la prezent (rightOffset lumânări libere în dreapta). Fără animație: scrollToRealTime
      // animează ~1 s, iar liniile indicatorilor sosite între timp ar opri derularea la jumătate.
      chart.timeScale().scrollToPosition(chart.timeScale().options().rightOffset, false);
      document.title = `${msg.symbol} · Nexus Live Chart`;
      // Chartul de ticuri pornește cu ultimele ticuri din istoric (sau doar cu ultimul tic).
      state.ticks = msg.recent.length ? msg.recent : (msg.last ? [msg.last] : []);
      // `...msg.last` desface lista [timp, bid, ask] în trei argumente separate.
      if (msg.last) updateQuote(...msg.last);
      setStatus(true, "live");
      requestDraw();
      state.loaded = true;
      // Starea datastore-ului pentru perechea deschisă (datastore.js).
      window.onStoreStatus?.(msg.store);
      // Recuperarea ticurilor lipsă de pe VM (sync.js).
      window.onSyncStatus?.(msg.sync);
      // Anunțăm alerts.js că s-a încărcat un simbol (dacă a definit funcția; `?.` = doar dacă există).
      window.onSymbolLoaded?.();
      // Indicatorii atașați își cer din nou rezultatele, pentru noul istoric (indicators.js).
      window.onHistoryLoaded?.(msg);
    } else if (msg.type === "store") {
      // Progresul / starea datastore-ului local (descărcare, actualizare la lumânare închisă).
      if (!sock.gotHello) armHelloTimer();
      window.onStoreStatus?.(msg);
    } else if (msg.type === "sync") {
      // Progresul recuperării ticurilor lipsă de către colectorul de pe VM (sync.js).
      window.onSyncStatus?.(msg);
    } else if (msg.type === "indicator") {
      // Rezultatul unui indicator, calculat pe server din datastore (indicators.js).
      window.onIndicator?.(tickMode() ? tickTimes(msg) : msg);
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
    } else if (msg.type === "feed") {
      // Starea conexiunii server ↔ sursa de prețuri. La revenire ne reconectăm, ca să primim
      // și istoricul din perioada în care conexiunea a lipsit.
      if (msg.up) connect();
      else setStatus(false, "sursa de prețuri deconectată — reîncerc");
    } else if (msg.type === "source") {
      // Sursa prețurilor s-a schimbat (din această pagină sau din alta): cerem istoricul de la ea.
      // Meniul de simboluri trece imediat pe simbolurile noii surse (chiar dacă simbolul curent
      // nu există acolo și chartul nu se poate încărca).
      sourceSel.value = state.source = msg.source;
      loadSymbols();
      connect();
      setStatus(false, `conectare la ${msg.name}...`);
    } else if (msg.type === "error") {
      // Afișăm mesajul. Pentru erori definitive (ex. simbol inexistent) NU mai încercăm
      // reconectarea; pentru cele temporare (msg.retry, ex. sursa de prețuri indisponibilă), da.
      setStatus(false, msg.msg);
      sock.errorMsg = msg.msg;
      if (!msg.retry) { sock.onclose = null; clearTimeout(helloTimer); }
      // Simbolul nu există la sursa activă (de obicei după schimbarea sursei): anunțăm
      // și deschidem meniul de simboluri, ca să se poată alege direct altul.
      if (msg.code === "unknown_symbol") {
        toast("Simbol indisponibil", `${msg.symbol} nu există la ${msg.source}. Alege alt simbol din meniu.`);
        window.openSymbols?.();
      }
    }
  };
  // Conexiunea a căzut (server oprit, internet întrerupt): reîncercăm cu pauze tot mai mari –
  // 1s, 2s, 4s, 8s, apoi maximum 10s – ca să nu bombardăm serverul.
  sock.onclose = () => {
    setStatus(false, `${sock.errorMsg || "deconectat"} — reîncerc`);
    retryTimer = setTimeout(connect, Math.min(1000 * 2 ** retry++, 10000));
  };
}

// ---------- Istoric mai vechi ----------
// La conectare primim cel puțin un an de lumânări (de la M1 în sus, cel mult 10.000). Când
// chartul e derulat aproape de prima lumânare, cerem de la server lumânările de dinainte.
// `older` se înlocuiește la fiecare istoric nou, deci un răspuns pentru un simbol / timeframe
// vechi e ignorat. done = sursa nu mai are istoric; retryAt = pauză după o eroare.
let older = { loading: false, done: false, retryAt: 0 };

// Cere serverului lumânările dinaintea primei lumânări de pe chart și le lipește în față.
async function loadOlder() {
  const o = older;
  // O cerere deja în curs, istoric terminat, chart gol sau pauză după eroare: nu facem nimic.
  if (o.loading || o.done || !rawBars.length || Date.now() < o.retryAt) return;
  o.loading = true;
  try {
    if (tickMode()) { await loadOlderTicks(o); return; }
    const q =`symbol=${encodeURIComponent(state.symbol)}&tf=${state.tf}&before=${rawBars[0].time}`;
    const r = await fetch(`/api/bars?${q}`);
    if (!r.ok) throw new Error(r.statusText);
    const { bars } = await r.json();
    if (o !== older) return;   // între timp s-a încărcat alt simbol / timeframe
    // Doar lumânările strict mai vechi decât prima de pe chart (fără dubluri).
    const add = bars.filter((b) => b.time < rawBars[0].time);
    if (!add.length) { o.done = true; return; }
    // La Heikin-Ashi istoricul începe acum mai devreme: calculăm lumânările noi plus primele
    // HA_TAIL de după ele (acestea se schimbă puțin, pentru că pornesc acum din alt punct; mai
    // departe rezultatul rămâne același, vezi HA_TAIL), înainte de a redesena.
    // (haGen++ anulează și un calcul în curs pentru istoricul de dinainte; reluăm mai jos.)
    let ha = null;
    if (chartType() === "heikin") {
      const gen = ++haGen;
      if (haBars) {
        const part = await fetchHeikin([...add, ...rawBars.slice(0, HA_TAIL)]).catch(() => null);
        if (o !== older || gen !== haGen) return;
        if (part && haBars) ha = part.concat(haBars.slice(part.length - add.length));
      }
    }
    // Adăugăm lumânările în față și păstrăm pe ecran exact aceeași porțiune de chart.
    rawBars = [...add, ...rawBars];
    haBars = ha;
    const range = chart.timeScale().getVisibleLogicalRange();
    showBars();
    if (chartType() === "heikin") scheduleHeikin();
    if (range) {
      chart.timeScale().setVisibleLogicalRange({ from: range.from + add.length, to: range.to + add.length });
    }
  } catch {
    o.retryAt = Date.now() + 5000;   // eroare: reîncercăm peste 5 secunde
  } finally {
    o.loading = false;
  }
}
// „Ticuri”: cere ticurile dinaintea primului de pe chart și le lipește în față, cu numere de
// ordine mai mici (ora lor reală intră în tickMs).
async function loadOlderTicks(o) {
  const first = rawBars[0].time;
  const q = `symbol=${encodeURIComponent(state.symbol)}&before=${tickMs.get(first)}`;
  const r = await fetch(`/api/ticks?${q}`);
  if (!r.ok) throw new Error(r.statusText);
  const { ticks } = await r.json();
  if (o !== older) return;
  if (!ticks.length) { o.done = true; return; }
  const n = ticks.length;
  const bars = [], asks = [];
  ticks.forEach(([ms, bid, ask], i) => {
    const t = first - n + i;
    setTick(t, ms);
    bars.push(tickBar(t, i ? ticks[i - 1][1] : undefined, bid));
    asks.push({ time: t, value: ask });
  });
  // Lumânarea care era prima pe chart pornește acum din ultimul tic adus.
  rawBars[0] = tickBar(first, ticks[n - 1][1], rawBars[0].close);
  rawBars = [...bars, ...rawBars];
  askData = [...asks, ...askData];
  const range = chart.timeScale().getVisibleLogicalRange();
  showBars();
  if (range) chart.timeScale().setVisibleLogicalRange({ from: range.from + n, to: range.to + n });
}

// La fiecare derulare / zoom: dacă se vede una dintre primele 200 de lumânări, cerem mai vechi.
chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
  if (range && range.from < 200) loadOlder();
});

// ---------- Controale ----------
// Lista de timeframe-uri: câte o opțiune pentru fiecare din TFS. La alegere îl reținem și ne
// reconectăm (serverul trimite istoricul pentru noul timeframe).
const tfSel = $("tf");
for (const [tf, label] of TFS) tfSel.add(new Option(label, tf));
if (!TFS.some(([tf]) => tf === state.tf)) state.tf = TFS[0][0];
tfSel.value = state.tf;
tfSel.addEventListener("change", () => {
  const wasTicks = tickMode();
  state.tf = Number(tfSel.value); store.set("tf", state.tf);
  // Intrare în / ieșire din „Ticuri”: alt fel de date pe chart (puncte numerotate / lumânări).
  if (wasTicks !== tickMode()) {
    rawBars = []; askData = []; tickMs.clear(); msTick.clear(); state.lastBar = null;
    applyChartType();
  }
  connect();
});

// Lista tipurilor de afișare (lumânări japoneze, Heikin-Ashi, bare, linie, arie).
const typeSel = $("chartType");
for (const [id, label] of CHART_TYPES) typeSel.add(new Option(label, id));
if (!CHART_TYPES.some(([id]) => id === state.chartType)) state.chartType = "candles";
typeSel.value = state.chartType;
typeSel.addEventListener("change", () => {
  state.chartType = typeSel.value; store.set("chartType", state.chartType); applyChartType();
});

// Trece chartul pe alt simbol (din meniul de simboluri, symbols.js, sau dintr-o alarmă).
function selectSymbol(s) {
  s = s.trim().toUpperCase();
  if (s && s !== state.symbol) { state.symbol = s; store.set("symbol", s); $("symName").textContent = s; connect(); }
}
$("symName").textContent = state.symbol;

// Lista sursei de prețuri (MT5 / cTrader). Sursa e aceeași pentru tot serverul: schimbarea ei
// de aici se vede în toate paginile deschise. O sursă neconfigurată apare dezactivată, iar
// motivul se vede la trecerea mouse-ului peste listă.
const sourceSel = $("source");
// Reface lista de surse cu datele de la server (/api/sources).
async function loadSources() {
  try {
    const { active, sources } = await (await fetch("/api/sources")).json();
    sourceSel.replaceChildren();   // golim lista și o completăm din nou
    for (const s of sources) {
      const o = document.createElement("option");
      o.value = s.id;
      o.textContent = s.problem ? `${s.name} (neconfigurat)` : s.name;
      o.disabled = Boolean(s.problem) && s.id !== active;
      sourceSel.appendChild(o);
    }
    sourceSel.value = active;
    sourceSel.title = ["Sursa prețurilor", ...sources.filter((s) => s.problem)
      .map((s) => `${s.name}: ${s.problem}`)].join("\n");
  } catch { /* serverul nu răspunde încă; lista se completează la următorul hello */ }
}
// Alegerea unei alte surse: o cerem serverului (POST /api/source); lista e blocată până răspunde.
sourceSel.addEventListener("change", async () => {
  const want = sourceSel.value;
  const name = sourceSel.selectedOptions[0].textContent;
  sourceSel.disabled = true;
  setStatus(false, `conectare la ${name}...`);
  try {
    const r = await fetch("/api/source", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source: want }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || r.statusText);
    // De obicei pagina află de schimbare prin mesajul "source" de pe WebSocket. Dacă însă nu are
    // nicio conexiune deschisă (ex. după „simbolul nu există la sursa X”), mesajul nu ajunge la
    // ea, așa că trecem singuri pe noua sursă și ne reconectăm.
    if (data.active !== state.source) { state.source = data.active; loadSymbols(); }
    if (!ws || ws.readyState === WebSocket.CLOSING || ws.readyState === WebSocket.CLOSED) connect();
    if (!data.connected) setStatus(false, `${name} încă neconectat — reîncerc`);
  } catch (e) {
    setStatus(false, `nu pot trece pe ${name}: ${e.message}`);
    sourceSel.value = state.source;
    // Chartul a rămas pe sursa veche și merge mai departe: după câteva secunde revenim la „live”.
    setTimeout(() => { if (ws?.readyState === WebSocket.OPEN) setStatus(true, "live"); }, 6000);
  } finally {
    sourceSel.disabled = false;
  }
});

// Pornirea: aplicăm tipul de afișare salvat și ne conectăm la server.
applyChartType();
loadSources();
connect();
