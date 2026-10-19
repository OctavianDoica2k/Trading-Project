// Indicatorul „Linii de trend”: desenează liniile de trend valide calculate de analysis.py.
// =====================================================================================
//  CE FACE ACEST FIȘIER (pe scurt)
// -------------------------------------------------------------------------------------
//  - Se înregistrează în meniul de indicatori (indicators.js). Când e atașat, trimite
//    serverului ultimele TREND_BARS lumânări de pe chart (POST /api/trendlines), indiferent de
//    zoom; dacă chartul are mai puține, cere întâi istoricul mai vechi. Serverul rulează pe ele
//    partea a 2-a din analysis.py și întoarce dreptele valide: prin maxime (rezistențe, roșu)
//    și prin minime (suporturi, verde).
//  - Fiecare dreaptă e desenată ca o serie de linie, de la primul pivot, prin al doilea,
//    până la ultima lumânare analizată.
//  - În dreapta sus pe chart apare controlul indicatorului: sliderul de apropiere, câte linii
//    se văd, ↻ (recalculează cu lumânările de acum) și ✕ (detașează).
//  - Sliderul arată doar liniile apropiate de prețul curent: la N%, o linie e vizibilă dacă
//    distanța dintre capătul ei și prețul curent e cel mult N% din intervalul de preț al
//    lumânărilor analizate (maximul − minimul). 100% = toate. La prima atașare pornește de la 50%.
//  - La schimbarea simbolului / timeframe-ului liniile se recalculează singure.
//
//  Folosește variabilele din app.js (chart, series, state, $, css, store), toast() din
//  alerts.js și registerIndicator() din indicators.js.
// =====================================================================================

// Valoarea sliderului e salvată în browser; dacă lipsește, e prima atașare: 50%.
const TREND_PCT_KEY = "trend.pct";
const TREND_DEFAULT_PCT = 50;
// Câte lumânări în spate analizează indicatorul (aceeași limită e și pe server: TREND_MAX_BARS).
const TREND_BARS = 1000;

// Liniile desenate acum pe chart: { series, end, visible } (end = prețul liniei la ultima
// lumânare). `trendSpan` = maximul − minimul lumânărilor analizate. `trendJob` crește la
// fiecare reset, ca un răspuns sosit după schimbarea simbolului / detașare să fie ignorat.
let trendLines = [];
let trendSpan = 0;
let trendJob = 0;
let trendWidget = null;   // controlul din dreapta sus (există doar cât e atașat indicatorul)

function buildTrendWidget() {
  const w = document.createElement("div");
  w.className = "indwidget";
  w.innerHTML = `
    <span class="indname" title="Linii de trend (analysis.py)">📈 Trend</span>
    <input type="range" min="0" max="100" step="1" aria-label="Apropierea liniilor de trend de prețul curent"
      title="Arată liniile de trend apropiate de prețul curent (100% = toate)">
    <label class="indpct" title="Scrie procentul (0–100)"><input type="number" min="0" max="100" step="1"
      inputmode="numeric" aria-label="Procentul de apropiere">%</label>
    <span class="badge indcount" title="Linii vizibile / linii valide">0</span>
    <button type="button" class="iconbtn indrefresh" title="Recalculează pe ultimele ${TREND_BARS} de lumânări">↻</button>
    <button type="button" class="iconbtn indremove" title="Detașează indicatorul">✕</button>`;
  const range = w.querySelector("input[type=range]");
  const box = w.querySelector("input[type=number]");
  range.value = box.value = store.get(TREND_PCT_KEY, null) ?? TREND_DEFAULT_PCT;
  store.set(TREND_PCT_KEY, range.value);
  // Sliderul și căsuța de text arată mereu aceeași valoare; oricare se schimbă, filtrăm din nou.
  const setPct = (v) => {
    range.value = box.value = v;
    store.set(TREND_PCT_KEY, v);
    applyTrendFilter();
  };
  range.addEventListener("input", () => setPct(range.value));
  // În căsuță: filtrăm pe măsură ce scrii (doar valori întregi 0–100); la Enter sau la ieșirea
  // din căsuță, o valoare goală / greșită revine la ultima valoare bună.
  box.addEventListener("input", () => {
    const v = Number(box.value);
    if (box.value !== "" && Number.isInteger(v) && v >= 0 && v <= 100) setPct(v);
  });
  box.addEventListener("change", () => {
    const v = Math.round(Number(box.value));
    setPct(box.value === "" || Number.isNaN(v) ? range.value : Math.min(100, Math.max(0, v)));
  });
  box.addEventListener("keydown", (e) => { if (e.key === "Enter") box.blur(); });
  w.querySelector(".indrefresh").onclick = () => loadTrendLines();
  w.querySelector(".indremove").onclick = () => detachIndicator("trend");
  $("indWidgets").appendChild(w);
  return w;
}

function clearTrendLines() {
  for (const l of trendLines) chart.removeSeries(l.series);
  trendLines = [];
  applyTrendFilter();
}

// Ascunde / arată liniile după distanța față de prețul curent (bid, ca lumânările).
function applyTrendFilter() {
  if (!trendWidget) return;
  const pct = Number(trendWidget.querySelector("input[type=range]").value);
  const price = state.prevBid ?? state.lastBar?.close;
  const limit = (pct / 100) * trendSpan;
  let shown = 0;
  for (const l of trendLines) {
    const visible = pct >= 100 || price == null || Math.abs(l.end - price) <= limit;
    if (visible !== l.visible) { l.series.applyOptions({ visible }); l.visible = visible; }
    if (visible) shown++;
  }
  trendWidget.querySelector(".indcount").textContent =
    shown === trendLines.length ? shown : `${shown}/${trendLines.length}`;
}

function drawTrendLine(line, color) {
  const s = chart.addLineSeries({
    color, lineWidth: 1,
    // Liniile nu au etichetă pe axa de preț și nu schimbă scara chartului.
    lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
    autoscaleInfoProvider: () => null,
  });
  s.setData([line.from, line.via, line.to]);
  trendLines.push({ series: s, end: line.to.value, visible: true });
}

// Încarcă istoric mai vechi (loadOlder din app.js) până când chartul are cel puțin `n`
// lumânări sau sursa nu mai are istoric. Se oprește și dacă o cerere nu aduce nimic nou.
async function ensureBars(n) {
  for (let k = 0; k < 20 && series.data().length < n && !older.done; k++) {
    const before = series.data().length;
    if (older.loading) { await new Promise((r) => setTimeout(r, 300)); continue; }
    await loadOlder();
    if (series.data().length === before) break;
  }
}

async function loadTrendLines() {
  if (!trendWidget) return;
  const job = ++trendJob;
  const w = trendWidget;
  w.classList.add("busy");
  w.querySelector(".indcount").textContent = "…";
  try {
    await ensureBars(TREND_BARS);
    if (job !== trendJob) return;
    // Ultimele TREND_BARS lumânări de pe chart, inclusiv cea în curs.
    const bars = series.data().slice(-TREND_BARS)
      .map(({ time, open, high, low, close }) => ({ time, open, high, low, close }));
    if (!bars.length) { applyTrendFilter(); return; }
    const r = await fetch("/api/trendlines", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ bars }),
    });
    const res = await r.json();
    if (!r.ok) throw new Error(res.detail || r.statusText);
    if (job !== trendJob) return;
    clearTrendLines();
    for (const l of res.max) drawTrendLine(l, css("--down"));
    for (const l of res.min) drawTrendLine(l, css("--up"));
    trendSpan = res.range[1] - res.range[0];
    applyTrendFilter();
    w.title = `${res.max.length} prin maxime, ${res.min.length} prin minime ` +
      `(${res.pivots.max} maxime, ${res.pivots.min} minime în ${res.bars} lumânări)` +
      (res.bars < TREND_BARS ? ` — istoricul disponibil are doar ${res.bars} lumânări` : "");
  } catch (e) {
    if (job === trendJob) { toast("Linii de trend", e.message); applyTrendFilter(); }
  } finally {
    if (job === trendJob) w.classList.remove("busy");
  }
}

registerIndicator({
  id: "trend",
  name: "Linii de trend",
  icon: "📈",
  desc: `Drepte valide prin maxime și minime — rulează analysis.py pe ultimele ${TREND_BARS} de lumânări`,
  note: `Se calculează pe ultimele ${TREND_BARS} de lumânări ale timeframe-ului curent. Sliderul din dreapta sus ascunde liniile depărtate de prețul curent.`,
  attach() {
    if (trendWidget) return;
    trendWidget = buildTrendWidget();
    applyTrendFilter();
    // Dacă istoricul e deja pe chart calculăm acum; altfel, când sosește (onHistoryLoaded).
    if (series.data().length) loadTrendLines();
  },
  detach() {
    trendJob++;
    clearTrendLines();
    trendWidget?.remove();
    trendWidget = null;
  },
});

// Prețul se mișcă: reverificăm de două ori pe secundă care linii sunt destul de aproape.
setInterval(() => { if (trendLines.length) applyTrendFilter(); }, 500);
// Apelată de app.js când sosește istoricul pentru un simbol / timeframe nou: liniile vechi
// nu mai sunt valabile; dacă indicatorul e atașat, le recalculăm după ce chartul se așază.
window.onHistoryLoaded = () => {
  trendJob++;
  clearTrendLines();
  renderIndicators();
  if (trendWidget) setTimeout(loadTrendLines, 300);
};
