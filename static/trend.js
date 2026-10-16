// Butonul „📈 Trend”: desenează liniile de trend valide calculate de analysis.py.
// =====================================================================================
//  CE FACE ACEST FIȘIER (pe scurt)
// -------------------------------------------------------------------------------------
//  - La click trimite serverului lumânările vizibile pe chart (POST /api/trendlines).
//  - Serverul rulează pe ele partea a 2-a din analysis.py și întoarce dreptele valide:
//    cele prin maxime (rezistențe, roșu) și cele prin minime (suporturi, verde).
//  - Fiecare dreaptă e desenată ca o serie de linie, de la primul pivot, prin al doilea,
//    până la ultima lumânare analizată.
//  - Al doilea click șterge liniile. La schimbarea simbolului / timeframe-ului se șterg singure.
//
//  Folosește variabilele din app.js (chart, series, $, css) și toast() din alerts.js.
// =====================================================================================

const trendBtn = $("trendBtn");
const trendCount = $("trendCount");
// Seriile de linie desenate acum pe chart; `trendJob` crește la fiecare reset, ca un răspuns
// sosit după schimbarea simbolului să fie ignorat.
let trendSeries = [];
let trendJob = 0;

function clearTrendLines() {
  for (const s of trendSeries) chart.removeSeries(s);
  trendSeries = [];
  trendBtn.classList.remove("on");
  trendCount.hidden = true;
}

function drawTrendLine(line, color) {
  const s = chart.addLineSeries({
    color, lineWidth: 1,
    // Liniile nu au etichetă pe axa de preț și nu schimbă scara chartului.
    lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
    autoscaleInfoProvider: () => null,
  });
  s.setData([line.from, line.via, line.to]);
  trendSeries.push(s);
}

async function loadTrendLines() {
  // Lumânările vizibile acum pe ecran (indexurile logice ale chartului = poziția în listă).
  const data = series.data();
  const range = chart.timeScale().getVisibleLogicalRange();
  if (!data.length || !range) return;
  const from = Math.max(0, Math.floor(range.from));
  const to = Math.min(data.length - 1, Math.ceil(range.to));
  const bars = data.slice(from, to + 1).map(({ time, open, high, low, close }) => ({ time, open, high, low, close }));

  const job = ++trendJob;
  trendBtn.disabled = true;
  trendBtn.classList.add("busy");
  try {
    const r = await fetch("/api/trendlines", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ bars }),
    });
    const res = await r.json();
    if (!r.ok) throw new Error(res.detail || r.statusText);
    if (job !== trendJob) return;
    for (const l of res.max) drawTrendLine(l, css("--down"));
    for (const l of res.min) drawTrendLine(l, css("--up"));
    const n = res.max.length + res.min.length;
    trendBtn.classList.add("on");
    trendCount.textContent = n;
    trendCount.hidden = false;
    let info = `${res.max.length} prin maxime, ${res.min.length} prin minime ` +
      `(${res.pivots.max} maxime, ${res.pivots.min} minime în ${res.bars} lumânări).`;
    if (res.truncated) info += " Doar ultimele 300 de lumânări vizibile au fost analizate.";
    toast(n ? `${n} linii de trend valide` : "Nicio linie de trend validă", info);
  } catch (e) {
    if (job === trendJob) toast("Linii de trend", e.message);
  } finally {
    trendBtn.disabled = false;
    trendBtn.classList.remove("busy");
  }
}

trendBtn.onclick = () => (trendSeries.length ? clearTrendLines() : loadTrendLines());
// Apelată de app.js când sosește istoricul pentru un simbol / timeframe nou.
window.onHistoryLoaded = () => { trendJob++; clearTrendLines(); };
