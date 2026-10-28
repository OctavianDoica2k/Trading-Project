// Indicatorul „Linii de trend”: desenează liniile de trend valide calculate de analysis.py.
// =====================================================================================
//  CE FACE ACEST FIȘIER (pe scurt)
// -------------------------------------------------------------------------------------
//  - Se înregistrează în meniul de indicatori (indicators.js). Când e atașat, cere serverului
//    (pe WebSocket) indicatorul „trend” cu parametrul bars = TREND_BARS. Serverul rulează partea
//    a 2-a din analysis.py pe ultimele TREND_BARS lumânări ÎNCHISE din datastore-ul local și
//    întoarce dreptele valide: prin maxime (rezistențe, roșu) și prin minime (suporturi, verde).
//    La fiecare lumânare închisă, serverul recalculează și trimite singur rezultatul nou.
//  - Fiecare dreaptă e desenată ca o serie de linie, de la primul pivot, prin al doilea, până
//    la ultima lumânare închisă, apoi prelungită (cu aceeași pantă) până la lumânarea în curs.
//  - În dreapta sus pe chart apare controlul indicatorului: sliderul de apropiere, câte linii
//    se văd, ↻ (cere din nou rezultatul) și ✕ (detașează).
//  - Sliderul arată doar liniile apropiate de prețul curent: la N%, o linie e vizibilă dacă
//    distanța dintre capătul ei și prețul curent e cel mult N% din intervalul de preț al
//    lumânărilor analizate (maximul − minimul). 100% = toate. La prima atașare pornește de la 50%.
//  - Rulează pe orice timeframe, inclusiv pe „Ticuri” (o lumânare pe tic).
//
//  Folosește variabilele din app.js (chart, series, state, $, css, store, sendWS), toast() din
//  alerts.js și registerIndicator() din indicators.js.
// =====================================================================================

// Valoarea sliderului e salvată în browser; dacă lipsește, e prima atașare: 50%.
const TREND_PCT_KEY = "trend.pct";
const TREND_DEFAULT_PCT = 50;
// Câte lumânări închise în spate analizează indicatorul (parametrul trimis serverului).
const TREND_BARS = 1000;

// Liniile desenate acum pe chart: { series, to, slope, last, end, visible } (to = punctul de la
// ultima lumânare închisă, slope = variația pe o lumânare, last = ora ultimului punct desenat,
// end = prețul liniei la lumânarea în curs). `trendSpan` = maximul − minimul lumânărilor analizate.
let trendLines = [];
let trendSpan = 0;
let trendWidget = null;   // controlul din dreapta sus (există doar cât e atașat indicatorul)

// Construiește controlul din dreapta sus (slider, căsuța cu procentul, numărul de linii, ↻, ✕).
function buildTrendWidget() {
  const w = document.createElement("div");
  w.className = "indwidget";
  w.innerHTML = `
    <span class="indname" title="Linii de trend (analysis.py)">Trend</span>
    <input type="range" min="0" max="100" step="1" aria-label="Apropierea liniilor de trend de prețul curent"
      title="Arată liniile de trend apropiate de prețul curent (100% = toate)">
    <label class="indpct" title="Scrie procentul (0–100)"><input type="number" min="0" max="100" step="1"
      inputmode="numeric" aria-label="Procentul de apropiere">%</label>
    <span class="badge indcount" title="Linii vizibile / linii valide">0</span>
    <button type="button" class="iconbtn indrefresh" title="Cere din nou liniile (ultimele ${TREND_BARS} de lumânări închise din datastore)">↻</button>
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
  w.querySelector(".indrefresh").onclick = () => requestTrend();
  w.querySelector(".indremove").onclick = () => detachIndicator("trend");
  $("indWidgets").appendChild(w);
  return w;
}

// Șterge de pe chart toate liniile de trend.
function clearTrendLines() {
  for (const l of trendLines) chart.removeSeries(l.series);
  trendLines = [];
  applyTrendFilter();
}

// Ascunde / arată liniile după distanța față de prețul curent (bid, ca lumânările).
function applyTrendFilter() {
  if (!trendWidget) return;
  if (trendWidget.classList.contains("off")) return;
  const pct = Number(trendWidget.querySelector("input[type=range]").value);
  const price = state.prevBid ?? state.lastBar?.close;   // prețul curent
  const limit = (pct / 100) * trendSpan;                 // distanța maximă permisă, în preț
  let shown = 0;
  for (const l of trendLines) {
    const visible = pct >= 100 || price == null || Math.abs(l.end - price) <= limit;
    if (visible !== l.visible) { l.series.applyOptions({ visible }); l.visible = visible; }
    if (visible) shown++;
  }
  // „5” dacă se văd toate, altfel „3/5” (vizibile / valide).
  trendWidget.querySelector(".indcount").textContent =
    shown === trendLines.length ? shown : `${shown}/${trendLines.length}`;
}

// Desenează o dreaptă primită de la server (from -> via -> to) ca o serie de linie, cu culoarea dată.
function drawTrendLine(line, color) {
  const s = chart.addLineSeries({
    color, lineWidth: 1,
    // Liniile nu au etichetă pe axa de preț și nu schimbă scara chartului.
    lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
    autoscaleInfoProvider: () => null,
  });
  s.setData([line.from, line.via, line.to]);
  trendLines.push({ series: s, to: line.to, slope: line.slope, last: line.to.time, end: line.to.value, visible: true });
}

// Poziția (indexul) lumânării cu ora `t` pe chart, prin căutare binară; -1 dacă nu există.
function barIndex(data, t) {
  let lo = 0, hi = data.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (data[mid].time === t) return mid;
    if (data[mid].time < t) lo = mid + 1; else hi = mid - 1;
  }
  return -1;
}

// Prelungește liniile de la ultima lumânare închisă până la lumânarea în curs de pe chart.
// analysis.py lucrează cu poziții (lumânarea 0, 1, 2...), iar chartul are aceleași lumânări ca
// datastore-ul, deci valoarea la lumânarea curentă = valoarea la `to` + panta × lumânările dintre ele.
function extendTrendLines() {
  if (!trendLines.length) return;
  const data = series.data();
  if (!data.length) return;
  const cur = data.length - 1;
  const time = data[cur].time;
  let changed = false;
  for (const l of trendLines) {
    if (l.last >= time) continue;   // linia ajunge deja până la lumânarea curentă
    const i = barIndex(data, l.to.time);
    if (i < 0) continue;            // capătul liniei nu mai e pe chart
    l.end = l.to.value + l.slope * (cur - i);
    l.series.update({ time, value: l.end });
    l.last = time;
    changed = true;
  }
  if (changed) applyTrendFilter();
}

// Cere serverului liniile pentru simbolul / timeframe-ul de acum. Rezultatul vine în onResult.
function requestTrend() {
  if (!trendWidget || !state.loaded) return;
  if (sendWS({ type: "indicator", id: "trend", params: { bars: TREND_BARS } })) {
    trendWidget.classList.add("busy");
    trendWidget.querySelector(".indcount").textContent = "…";
  }
}

// Rezultatul de la server: ștergem liniile vechi și le desenăm pe cele noi (rezistențe cu roșu,
// suporturi cu verde), apoi le prelungim și aplicăm filtrul sliderului.
function onTrendResult(msg) {
  const w = trendWidget;
  // Răspuns pentru alt simbol / timeframe (chartul s-a schimbat între timp): îl ignorăm.
  if (!w || msg.symbol !== state.symbol || msg.tf !== state.tf) return;
  w.classList.remove("busy");
  if (msg.error) { toast("Linii de trend", msg.error); applyTrendFilter(); return; }
  const res = msg.result;
  clearTrendLines();
  for (const l of res.max) drawTrendLine(l, css("--down"));
  for (const l of res.min) drawTrendLine(l, css("--up"));
  trendSpan = res.range[1] - res.range[0];
  extendTrendLines();
  applyTrendFilter();
  // Rezumatul calculului, la trecerea mouse-ului peste control.
  w.title = `${res.max.length} prin maxime, ${res.min.length} prin minime ` +
    `(${res.pivots.max} maxime, ${res.pivots.min} minime în ultimele ${res.bars} lumânări închise ` +
    `din datastore, până la ${fmtBarTime(res.last)})` +
    (res.bars < res.requested ? ` — datastore-ul are doar ${res.bars} lumânări` : "");
}

// Înregistrarea în meniul de indicatori (vezi indicators.js pentru ce înseamnă fiecare câmp).
registerIndicator({
  id: "trend",
  name: "Linii de trend",
  iconHTML: '<svg class="ico" viewBox="0 0 16 16"><path d="M2 12.5l12-6"/><path d="M2 8.5l12-6" opacity=".45"/><circle cx="5" cy="11" r="1.2"/><circle cx="11" cy="8" r="1.2"/></svg>',
  icon: "TL",
  desc: `Drepte valide prin maxime și minime — rulează analysis.py pe ultimele ${TREND_BARS} de lumânări închise din datastore`,
  note: `Se calculează pe server, din datastore-ul local, pe ultimele ${TREND_BARS} de lumânări închise ` +
    "ale timeframe-ului curent, și se recalculează la fiecare lumânare închisă. Sliderul din dreapta sus " +
    "ascunde liniile depărtate de prețul curent.",
  attach() {
    if (trendWidget) return;
    trendWidget = buildTrendWidget();
    applyTrendFilter();
    // Dacă istoricul e deja pe chart cerem acum; altfel, când sosește (reload, din indicators.js).
    requestTrend();
  },
  detach() {
    sendWS({ type: "indicator_off", id: "trend" });
    clearTrendLines();
    trendWidget?.remove();
    trendWidget = null;
  },
  // Istoric nou (alt simbol / timeframe / reconectare): liniile vechi nu mai sunt valabile.
  reload() {
    clearTrendLines();
    requestTrend();
  },
  onResult: onTrendResult,
  onNewBar: extendTrendLines,
});

// Prețul se mișcă: reverificăm de două ori pe secundă care linii sunt destul de aproape.
setInterval(() => { if (trendLines.length) applyTrendFilter(); }, 500);
