// Indicatorul „Medie mobilă”: desenează media mobilă calculată de medie_mobila() din analysis.py.
// =====================================================================================
//  CE FACE ACEST FIȘIER (pe scurt)
// -------------------------------------------------------------------------------------
//  - Se înregistrează în meniul de indicatori (indicators.js) ca indicator `multi`: se poate
//    adăuga pe chart de mai multe ori (ex. MA 20 și MA 50). Fiecare copie („sma#1”, „sma#2”...)
//    are linia, culoarea, controlul și parametrii ei.
//  - O copie cere serverului (pe WebSocket) media cu parametrii period (numărul de lumânări) și
//    price (close / open / high / low). Serverul rulează medie_mobila(df, period, price) din
//    analysis.py pe ultimele lumânări ÎNCHISE din datastore și întoarce valorile ei.
//    La fiecare lumânare închisă, serverul recalculează și trimite singur rezultatul nou.
//  - Media e desenată ca o linie pe chart, cu valoarea ultimei lumânări închise pe axa de preț.
//  - În dreapta sus pe chart, fiecare copie are controlul ei: perioada, prețul, ↻ (cere din nou
//    rezultatul) și ✕ (o scoate de pe chart). Perioada și prețul se păstrează în browser.
//  - Rulează pe orice timeframe, inclusiv pe „Ticuri” (o lumânare pe tic).
//
//  Folosește variabilele din app.js (chart, state, $, css, store, sendWS), toast() din
//  alerts.js și registerIndicator() / detachIndicator() din indicators.js.
// =====================================================================================

const SMA_MAX_PERIOD = 1000;   // la fel ca SMA_MAX_PERIOD din indicators.py
const SMA_PRICES = { close: "Close", open: "Open", high: "High", low: "Low" };
// O copie nouă primește perioada și culoarea după numărul ei (sma#1 = 20, sma#2 = 50...).
const SMA_DEFAULT_PERIODS = [20, 50, 100, 200];
const SMA_COLORS = ["--alert", "#f5a623", "#4cc9f0", "#ff6b9a", "#9be564", "#ffffff"];

// O copie a mediei mobile, cu id-ul `id` („sma#2”). isNew = adăugată acum (parametri impliciți);
// altfel e pusă înapoi după reîncărcarea paginii, cu parametrii salvați.
function createSma(id, isNew) {
  const n = Number(id.split("#")[1]) || 1;   // numărul copiei („sma#2” -> 2)
  const key = `sma.params.${id}`;            // cheia din localStorage pentru parametrii ei
  const defaults = { period: SMA_DEFAULT_PERIODS[(n - 1) % SMA_DEFAULT_PERIODS.length], price: "close" };
  const colorOf = SMA_COLORS[(n - 1) % SMA_COLORS.length];
  // Culoarea poate fi o variabilă din style.css („--alert”) sau o culoare scrisă direct.
  const color = colorOf.startsWith("--") ? css(colorOf) : colorOf;
  let lineSeries = null;   // linia de pe chart (creată la primul rezultat)
  let w = null;   // controlul din dreapta sus (există doar cât e pe chart)

  // Parametrii salvați ai copiei, verificați: perioada între 1 și SMA_MAX_PERIOD, prețul unul
  // din SMA_PRICES; o valoare lipsă sau greșită primește valoarea implicită.
  function params() {
    let p = {};
    try { p = JSON.parse(store.get(key, "{}")) || {}; } catch { /* parametri impliciți */ }
    const period = Math.round(Number(p.period));
    return {
      period: Number.isFinite(period) ? Math.min(SMA_MAX_PERIOD, Math.max(1, period)) : defaults.period,
      price: p.price in SMA_PRICES ? p.price : defaults.price,
    };
  }
  const saveParams = (p) => store.set(key, JSON.stringify(p));
  if (isNew) saveParams(defaults);

  // Construiește controlul copiei din dreapta sus: perioada, prețul, ultima valoare, ↻ și ✕.
  function buildWidget() {
    const el = document.createElement("div");
    el.className = "indwidget";
    el.innerHTML = `
      <span class="indname" title="Medie mobilă (medie_mobila din analysis.py)">〰 MA</span>
      <label class="indpct" title="Perioada: câte lumânări intră în medie (1–${SMA_MAX_PERIOD})"><input type="number"
        class="smaperiod" min="1" max="${SMA_MAX_PERIOD}" step="1" inputmode="numeric" aria-label="Perioada mediei mobile"></label>
      <select class="indselect" aria-label="Prețul pe care se calculează media" title="Prețul pe care se calculează media">
        ${Object.entries(SMA_PRICES).map(([v, t]) => `<option value="${v}">${t}</option>`).join("")}
      </select>
      <span class="badge indcount" title="Ultima valoare a mediei">—</span>
      <button type="button" class="iconbtn indrefresh" title="Cere din nou media mobilă">↻</button>
      <button type="button" class="iconbtn indremove" title="Scoate media de pe chart">✕</button>`;
    // Numele și valoarea au culoarea liniei, ca să se vadă care control e al cărei linii.
    el.querySelector(".indname").style.color = color;
    el.querySelector(".indcount").style.background = color;
    el.querySelector(".indcount").style.color = "#10131a";
    const box = el.querySelector(".smaperiod");
    const sel = el.querySelector("select");
    const p = params();
    box.value = p.period;
    sel.value = p.price;
    // Perioada se aplică la Enter sau la ieșirea din căsuță; o valoare greșită revine la cea bună.
    box.addEventListener("change", () => {
      const v = Math.round(Number(box.value));
      if (box.value === "" || Number.isNaN(v)) { box.value = params().period; return; }
      box.value = Math.min(SMA_MAX_PERIOD, Math.max(1, v));
      saveParams({ ...params(), period: Number(box.value) });
      request();
    });
    box.addEventListener("keydown", (e) => { if (e.key === "Enter") box.blur(); });
    sel.addEventListener("change", () => { saveParams({ ...params(), price: sel.value }); request(); });
    el.querySelector(".indrefresh").onclick = () => request();
    el.querySelector(".indremove").onclick = () => detachIndicator(id);
    $("indWidgets").appendChild(el);
    return el;
  }

  // Scoate linia mediei de pe chart.
  function clear() {
    if (lineSeries) chart.removeSeries(lineSeries);
    lineSeries = null;
  }

  // Cere serverului media pentru simbolul / timeframe-ul de acum. Rezultatul vine în onResult.
  function request() {
    if (!w || !state.loaded) return;
    if (sendWS({ type: "indicator", id, params: params() })) {
      w.classList.add("busy");
      w.querySelector(".indcount").textContent = "…";
    }
  }

  // Rezultatul de la server: desenează (sau redesenează) linia mediei și ultima ei valoare.
  function onResult(msg) {
    if (!w || msg.symbol !== state.symbol || msg.tf !== state.tf) return;
    const res = msg.result;
    // Un răspuns pentru parametrii de dinainte (perioada / prețul s-au schimbat între timp).
    const p = params();
    if (res && (res.period !== p.period || res.price !== p.price)) return;
    w.classList.remove("busy");
    if (msg.error) { toast("Medie mobilă", msg.error); w.querySelector(".indcount").textContent = "!"; return; }
    const title = `MA(${res.period}, ${SMA_PRICES[res.price]})`;
    if (!lineSeries) {
      lineSeries = chart.addLineSeries({
        color, lineWidth: 2,
        priceLineVisible: false, crosshairMarkerVisible: false,
        // Media nu schimbă scara chartului (aceasta rămâne după lumânări).
        autoscaleInfoProvider: () => null,
      });
    }
    lineSeries.applyOptions({ title });
    lineSeries.setData(res.points);
    const last = res.points[res.points.length - 1];
    w.querySelector(".indcount").textContent = last ? last.value.toFixed(state.digits) : "—";
    w.title = `${title} pe ultimele ${res.bars} lumânări închise din datastore, până la ${fmtBarTime(res.last)}` +
      (res.points.length ? "" : ` — sunt prea puține lumânări pentru perioada ${res.period}`);
  }

  // Obiectul copiei, folosit de indicators.js (aceleași funcții ca un indicator obișnuit).
  return {
    attach() {
      if (w) return;
      w = buildWidget();
      request();
    },
    detach() {
      sendWS({ type: "indicator_off", id });
      clear();
      w?.remove();
      w = null;
      // Copia a fost scoasă: îi ștergem și parametrii salvați.
      try { localStorage.removeItem(key); } catch { /* ignorat */ }
    },
    // Istoric nou (alt simbol / timeframe / reconectare): media veche nu mai e valabilă.
    reload() {
      clear();
      request();
    },
    onResult,
  };
}

// Înregistrarea în meniul de indicatori; `create` face câte o copie nouă (vezi createSma).
registerIndicator({
  id: "sma",
  multi: true,
  name: "Medie mobilă",
  icon: "〰",
  desc: "Media mobilă pe o perioadă, calculată pe close, open, high sau low — rulează medie_mobila() din analysis.py",
  note: "Se poate adăuga de mai multe ori (ex. perioade diferite). Se calculează pe server, din datastore-ul " +
    "local, pe lumânările închise ale timeframe-ului curent, și se recalculează la fiecare lumânare închisă. " +
    "Perioada și prețul se aleg în controlul fiecărei medii, din dreapta sus.",
  create: createSma,
});
