// Strategy Tester (pagina /tester; în fereastra de peste chart cu ?embed=1, vezi testerwin.js).
// =====================================================================================
//  CE FACE ACEST FIȘIER (pe scurt)
// -------------------------------------------------------------------------------------
//  - Setări și Parametri: aceleași câmpuri ca în MT5 (Settings / Inputs); se păstrează în browser.
//  - Start: POST /api/tester/run; serverul rulează backtester.py într-un proces separat, pagina
//    întreabă progresul la 0,5 s, iar la final primește raportul. Al doilea click = Stop.
//  - Grafic: balanța, equity-ul și încărcarea depozitului, pe axa tranzacționării (fără golurile
//    de weekend), cu linie de urmărire la trecerea mouse-ului.
//  - Backtest: indicatorii principali + statisticile, cu aceleași nume ca în raportul MT5.
//  - Tranzacții: deal-urile, cu filtre și export CSV. Jurnal: mesajele testului.
//  - Verifică: rulează funcțiile ORIGINALE din strategy.py pe lumânări alese și le compară cu
//    copia rapidă folosită în test.
//  - Optimizare (ca în MT5): în Setări tipul (completă / genetică) și criteriul (Balance max); în
//    Parametri, pentru fiecare parametru bifat: Start, Pas, Stop. Start pornește optimizarea
//    (optimizer.py); tab-ul Optimizare arată fiecare test (tabel sortabil + grafic), iar dublu clic
//    pe un rând rulează testul complet cu acei parametri.
// =====================================================================================
(() => {
  const $ = (id) => document.getElementById(id);
  const $$ = (sel) => document.querySelectorAll(sel);
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const pad = (n) => String(n).padStart(2, "0");
  const ymd = (d) => `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
  const utcMs = (s) => Date.parse(`${s}T00:00:00Z`);
  const DAY = 86400000;
  const fmtTime = (ms) => { const d = new Date(ms); return `${ymd(d)} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`; };
  const MONTHS = ["ian", "feb", "mar", "apr", "mai", "iun", "iul", "aug", "sep", "oct", "nov", "dec"];
  const clock = (s) => `${Math.floor(s / 60)}:${pad(Math.floor(s % 60))}`;

  // Numere în stil românesc: 1 033,07 (spațiu la mii, virgulă la zecimale); null = „—”.
  function fmt(v, dec = 2, sign = false) {
    if (v === null || v === undefined || Number.isNaN(v)) return "—";
    const n = Number(v);
    const [i, d] = Math.abs(n).toFixed(dec).split(".");
    const s = `${i.replace(/\B(?=(\d{3})+(?!\d))/g, "\u00a0")}${d ? `,${d}` : ""}`;
    return `${n < 0 ? "−" : sign && n > 0 ? "+" : ""}${s}`;
  }
  const cls = (v) => (v > 0 ? "pos" : v < 0 ? "neg" : "");

  const EMBED = new URLSearchParams(location.search).has("embed");
  if (EMBED) document.body.classList.add("embed");
  // Starea pentru fereastra din pagina principală (bara de titlu + punctul de pe butonul Tester).
  const tellParent = (text, running) => { if (EMBED) parent.postMessage({ type: "tester-state", text, running }, location.origin); };
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && EMBED) parent.postMessage({ type: "tester-close" }, location.origin); });

  const KEY = "nexusTester";
  const DEFAULT_INPUTS = { ma_period: 50, ma_price: "close", tl_bars: 1000, rr: 2.0, close_on_ma: true, risk_pct: 1.0, magic: 20261026 };
  const LEVERAGES = [1, 2, 5, 10, 20, 25, 30, 33, 50, 66, 100, 125, 200, 300, 400, 500, 1000];
  const MODES = {
    real: { p: 3, s: 1, d: "Toate ticurile reale (bid / ask). SL și TP se execută la prețul ticului care le atinge, cu alunecare la goluri de preț." },
    ohlc1: { p: 2, s: 2, d: "4 prețuri pe fiecare M1: O→L→H→C (urcătoare) sau O→H→L→C (coborâtoare); ask = bid + spread. SL / TP la prețul din ordin." },
    open: { p: 1, s: 3, d: "Strategia rulează doar la Open; SL, TP și stop out se verifică pe OHLC-ul lumânării, la prețul din ordin. Pentru căutări grosiere." },
  };

  // Parametrii care se pot optimiza și intervalele implicite (Start, Pas, Stop).
  const PRICE_LIST = ["close", "open", "high", "low", "median", "typical", "weighted"];
  const DEFAULT_OPT = {
    ma_period: { on: true, start: 10, step: 10, stop: 200 }, ma_price: { on: false, start: "close", stop: "weighted" },
    tl_bars: { on: false, start: 200, step: 100, stop: 1000 }, rr: { on: true, start: 1, step: 0.5, stop: 4 },
    close_on_ma: { on: false }, risk_pct: { on: false, start: 0.5, step: 0.5, stop: 3 },
  };
  const OPT_NAMES = { ma_period: "MA", ma_price: "Preț MA", tl_bars: "TLBars", rr: "RR", close_on_ma: "Close on MA", risk_pct: "Risc %" };
  const MODEL_NAMES = { open: "Open prices", ohlc1: "1 minute OHLC", real: "Real ticks" };
  const MODEL_SHORT = { open: "OP", ohlc1: "M1", real: "RT" };
  const PERIOD_NAMES = { full: "toată perioada", back: "perioada optimizată", fwd: "perioada forward" };
  const PRICE_NAMES = { close: "Close", open: "Open", high: "High", low: "Low", median: "Median", typical: "Typical", weighted: "Weighted" };
  // Tipurile optimizării (ca în MT5: Disabled / Slow complete / Fast genetic based algorithm).
  const OPT_MODES = {
    off: { t: "Dezactivată", d: "Un singur test, cu valorile din coloana Valoare a parametrilor." },
    complete: { t: "Completă", d: "Toate combinațiile, una câte una. Exactă: găsește garantat maximul, dar crește cu fiecare parametru." },
    genetic: { t: "Genetică rapidă", d: "Fast genetic based algorithm: testează de obicei 5–15% din combinații și ajunge la (sau lângă) maxim." },
  };

  let info = null;      // /api/tester/info
  let current = null;   // testul în curs: {id, kind}
  let result = null;    // ultimul raport
  let optResult = null; // ultima optimizare
  let optSort = { key: "fitness", asc: false };
  let optFilter = "all";
  let selectedPass = null;   // testul din optimizare rulat complet (marcat în tabel și în raport)
  let tf = "H1", mode = "ohlc1", preset = "m3", dealFilter = "all";

  // ---------- Mesaje plutitoare (în loc de alert) ----------
  function toast(text, kind = "") {
    const el = document.createElement("div");
    el.className = `toast ${kind}`;
    el.textContent = text;
    $("toasts").append(el);
    setTimeout(() => el.remove(), kind === "err" ? 7000 : 4000);
  }

  // ---------- Tab-uri ----------
  function showTab(name) {
    for (const t of $$(".tab")) t.classList.toggle("active", t.dataset.tab === name);
    for (const p of $$(".pane")) p.classList.toggle("active", p.id === `tab-${name}`);
    if (name === "graph") requestAnimationFrame(drawGraph);
    if (name === "opt") requestAnimationFrame(drawOpt);
  }
  for (const t of $$(".tab")) t.onclick = () => showTab(t.dataset.tab);

  // ---------- Setări salvate în browser ----------
  const load = () => { try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch { return {}; } };
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify({ settings: readSettings(), inputs: readInputs(), opt: readOptState() })); } catch { /* fără stocare */ } };

  function readSettings() {
    return {
      symbol: $("sSymbol").value, tf, period: preset, start: $("sStart").value, end: $("sEnd").value,
      forward: $("sForward").value, forward_date: $("sForwardDate").value, modelling: mode,
      deposit: Number($("sDeposit").value), currency: $("sCurrency").value, leverage: Number($("sLeverage").value),
    };
  }
  function readInputs() {
    return {
      ma_period: Number($("iPeriod").value), ma_price: $("iPrice").value, tl_bars: Number($("iBars").value),
      rr: Number($("iRR").value), close_on_ma: $("iCloseMA").value === "true", risk_pct: Number($("iRisk").value),
      magic: Number($("iMagic").value),
    };
  }
  function writeInputs(v) {
    $("iPeriod").value = v.ma_period; $("iPrice").value = v.ma_price; $("iBars").value = v.tl_bars;
    $("iRR").value = v.rr; $("iCloseMA").value = String(v.close_on_ma); $("iRisk").value = v.risk_pct; $("iMagic").value = v.magic;
    markInputs();
  }
  // Parametrii schimbați față de EA: marcați în tabel și numărați pe tab.
  function markInputs() {
    const v = readInputs();
    let n = 0;
    for (const tr of $$("#tab-inputs tr[data-k]")) {
      const changed = String(v[tr.dataset.k]) !== String(DEFAULT_INPUTS[tr.dataset.k]);
      tr.classList.toggle("changed", changed);
      n += changed;
    }
    $("inputsBadge").hidden = !n;
    $("inputsBadge").textContent = n;
  }

  // ---------- Optimizarea: coloanele Start / Pas / Stop din Parametri ----------
  // Celulele se adaugă la rândurile parametrilor; rândurile fără optimizare primesc o celulă goală.
  function buildOptCells() {
    for (const tr of $$("#tab-inputs tr[data-k]")) {
      const k = tr.dataset.k;
      if (!DEFAULT_OPT[k]) { tr.insertAdjacentHTML("beforeend", `<td colspan="5" class="o"></td>`); continue; }
      const num = (f) => `<td class="o"><input type="number" step="any" data-o="${f}" aria-label="${f} ${k}"></td>`;
      const sel = (f) => `<td class="o"><select data-o="${f}" aria-label="${f} ${k}">${PRICE_LIST.map((v) => `<option value="${v}">${PRICE_NAMES[v]}</option>`).join("")}</select></td>`;
      let cells = `<td class="o chk"><label class="switch"><input type="checkbox" data-o="on" aria-label="Optimizează ${k}"><i></i></label></td>`;
      if (k === "ma_price") cells += sel("start") + `<td class="o dash">—</td>` + sel("stop");
      else if (k === "close_on_ma") cells += `<td class="o fixed" colspan="3">false și true</td>`;
      else cells += num("start") + num("step") + num("stop");
      tr.insertAdjacentHTML("beforeend", cells + `<td class="o r" data-o="count"></td>`);
    }
    for (const el of $$("#tab-inputs [data-o]")) el.addEventListener("input", () => { updateOpt(); save(); });
    $("sOpt").addEventListener("change", () => { buildOptModes(); updateOpt(); save(); warmOpt(); setPlay(); });
    for (const id of ["sCrit", "sSeed"]) $(id).addEventListener("change", save);
    for (const b of $$("[data-goto]")) b.onclick = () => showTab(b.dataset.goto);
  }
  // Cardurile din Setări → Optimizare (ca la Modelling); alegerea se ține în selectul ascuns sOpt.
  function buildOptModes() {
    const cur = $("sOpt").value;
    $("sOptModes").innerHTML = Object.entries(OPT_MODES).map(([k, m]) =>
      `<button type="button" class="mode ${k === cur ? "on" : ""}" data-v="${k}" role="radio" aria-checked="${k === cur}">` +
      `<b>${m.t}</b><p>${m.d}</p><div class="meters"><span data-tag="${k}"></span></div></button>`).join("");
    for (const b of $("sOptModes").children) {
      b.onclick = () => { $("sOpt").value = b.dataset.v; $("sOpt").dispatchEvent(new Event("change")); };
    }
  }
  // Procesele optimizării pornesc dinainte pe server (durează câteva secunde), cât alegi parametrii.
  const warmOpt = () => { if ($("sOpt").value !== "off") fetch("/api/tester/warm", { method: "POST" }).catch(() => {}); };
  const optRow = (k) => document.querySelector(`#tab-inputs tr[data-k="${k}"]`);
  function readOptState() {
    const params = {};
    for (const k of Object.keys(DEFAULT_OPT)) {
      const tr = optRow(k), p = {};
      for (const el of tr.querySelectorAll("[data-o]")) {
        if (el.dataset.o === "count") continue;
        p[el.dataset.o] = el.type === "checkbox" ? el.checked : el.type === "number" ? Number(el.value) : el.value;
      }
      params[k] = p;
    }
    return { mode: $("sOpt").value, criterion: $("sCrit").value, seed: $("sSeed").value, params };
  }
  function writeOptState(o) {
    $("sOpt").value = o.mode || "off";
    $("sSeed").value = o.seed || "";
    for (const k of Object.keys(DEFAULT_OPT)) {
      const p = { ...DEFAULT_OPT[k], ...(o.params?.[k] || {}) };
      for (const el of optRow(k).querySelectorAll("[data-o]")) {
        if (el.dataset.o === "count") continue;
        if (el.type === "checkbox") el.checked = !!p[el.dataset.o];
        else el.value = p[el.dataset.o];
      }
    }
    buildOptModes();
    updateOpt();
  }
  // Valorile unui parametru optimizat (aceleași reguli ca optimizer.grid).
  function optValues(k, p) {
    if (k === "close_on_ma") return [false, true];
    if (k === "ma_price") {
      let a = PRICE_LIST.indexOf(p.start), b = PRICE_LIST.indexOf(p.stop);
      if (a > b) [a, b] = [b, a];
      return PRICE_LIST.slice(a, b + 1);
    }
    if (!(p.step > 0) || !(p.stop >= p.start)) return [];
    const n = Math.floor((p.stop - p.start) / p.step + 1e-9) + 1;
    const vals = Array.from({ length: Math.min(n, 100000) }, (_, i) => Number((p.start + i * p.step).toFixed(10)));
    return ["ma_period", "tl_bars"].includes(k) ? [...new Set(vals.map(Math.round))] : vals;
  }
  // Un număr fără zerouri inutile la final: 0,5 (nu 0,50), 2,25, 1 000.
  const num = (v) => fmt(v, Number.isInteger(v) ? 0 : Math.min(4, String(v).split(".")[1]?.length || 0));
  const valName = (k, v) => (k === "ma_price" ? PRICE_NAMES[v] || v : typeof v === "number" ? num(v) : String(v));
  // Intervalul unui parametru, pe scurt: „10 → 200, pas 5” / „Close → Weighted” / „false, true”.
  function rangeText(k, p, vals) {
    if (!vals.length) return "interval greșit";
    if (k === "close_on_ma") return "false, true";
    if (k === "ma_price") return vals.length === 1 ? valName(k, vals[0]) : `${valName(k, vals[0])} → ${valName(k, vals.at(-1))}`;
    return vals.length === 1 ? valName(k, vals[0]) : `${valName(k, vals[0])} → ${valName(k, vals.at(-1))}, pas ${valName(k, p.step)}`;
  }
  // Mărimea algoritmului genetic, cu aceleași formule ca optimizer.genetic: populația, generațiile minime,
  // generațiile de convergență și ~câte teste face (generația 0 = 2 populații, apoi copiii fără elită).
  function gaPlan(n) {
    const pop = Math.min(256, Math.max(64, Math.round(Math.sqrt(n) / 8) * 8));
    const gu = Math.min(31, Math.max(15, Math.round(Math.log2(Math.max(2, n)))));
    const gc = Math.ceil(gu / 3);
    const tests = Math.min(n, 2 * pop + gu * (pop - Math.ceil(pop * 0.1)));
    return { pop, gu, gc, tests, genetic: n > 2 * pop };
  }
  // Pornită / oprită, valorile pe rând, numărul de combinații și cardul din Setări.
  function updateOpt() {
    const st = readOptState();
    const on = st.mode !== "off";
    document.body.classList.toggle("opt-on", on);
    const rows = [];
    let n = 1;
    for (const [k, p] of Object.entries(st.params)) {
      const tr = optRow(k), vals = optValues(k, p);
      tr.classList.toggle("opt", !!p.on);
      tr.classList.toggle("bad", !!p.on && !vals.length);
      const cnt = tr.querySelector('[data-o="count"]');
      cnt.textContent = p.on ? (vals.length ? fmt(vals.length, 0) : "!") : "";
      cnt.title = p.on && vals.length ? vals.slice(0, 40).map((v) => valName(k, v)).join(", ") + (vals.length > 40 ? " …" : "") : "";
      if (p.on) { n *= vals.length; rows.push({ k, vals, text: rangeText(k, p, vals) }); }
    }
    const plan = gaPlan(n);
    const genetic = st.mode === "genetic" && plan.genetic;
    const tests = st.mode === "complete" || (st.mode === "genetic" && !plan.genetic) ? n : plan.tests;

    // Bara de jos din Parametri: combinațiile, pe parametri.
    $("iCombos").innerHTML = rows.length
      ? `<div class="ctotal"><b>${fmt(n, 0)}</b><span>combinații</span></div><div class="cparts">${rows.map((r) =>
        `<span class="cpart"><em>${OPT_NAMES[r.k]}</em>${fmt(r.vals.length, 0)}</span>`).join('<span class="x">×</span>')}</div>` +
        (st.mode === "genetic" ? `<div class="cplan">${genetic ? `genetic: ~${fmt(tests, 0)} teste (${fmt(tests / n * 100, tests / n < 0.1 ? 1 : 0)}%)` : "grilă mică: se rulează completă"}</div>` :
          `<div class="cplan">${fmt(n, 0)} teste</div>`)
      : `<div class="cempty">Bifează în coloana <b>Optimizare</b> parametrii care iau mai multe valori.</div>`;

    // Setări → Optimizare: etichetele cardurilor, cifrele și observațiile.
    const tag = (k, t) => { const el = document.querySelector(`#sOptModes [data-tag="${k}"]`); if (el) el.textContent = t; };
    tag("off", "1 test");
    tag("complete", rows.length ? `${fmt(n, 0)} teste` : "—");
    tag("genetic", rows.length ? (plan.genetic ? `~${fmt(plan.tests, 0)} teste` : `${fmt(n, 0)} teste (grilă mică)`) : "—");
    $("sOptCard").classList.toggle("off", !on);
    const stat = (label, value, small = "") => `<div class="ostat"><span>${label}</span><b>${value}</b><small>${small}</small></div>`;
    $("sOptStats").innerHTML = !on ? "" : [
      stat("Combinații", rows.length ? fmt(n, 0) : "—", rows.map((r) => `${OPT_NAMES[r.k]} ${r.vals.length}`).join(" × ")),
      stat("Teste", rows.length ? `${genetic ? "~" : ""}${fmt(tests, 0)}` : "—", genetic ? `${fmt(tests / n * 100, tests / n < 0.1 ? 1 : 0)}% din combinații` : "toate combinațiile"),
      genetic ? stat("Populație", fmt(plan.pop, 0), `elită ${Math.ceil(plan.pop * 0.1)} · turneu din 3`) : stat("Criteriu", "Balance max", "balanța finală"),
      genetic ? stat("Generații", `≥ ${plan.gu}`, `oprire după ${plan.gc} fără un maxim nou`) : stat("Modelling", MODEL_NAMES[mode], "în paralel pe toate nucleele"),
    ].join("");
    const notes = [];
    if (on) {
      notes.push(rows.length ? `<div class="onote"><b>Parametri:</b> ${rows.map((r) => `${OPT_NAMES[r.k]} <span>${esc(r.text)}</span>`).join(" · ")}</div>`
        : `<div class="onote warn">Niciun parametru bifat. Deschide <a href="#" data-goto-link="inputs">Parametri</a> și bifează ce vrei să optimizezi.</div>`);
      if (rows.some((r) => !r.vals.length)) notes.push(`<div class="onote warn">Un interval e greșit (Pas > 0 și Start ≤ Stop) — marcat cu roșu în Parametri.</div>`);
      notes.push(`<div class="onote">Modelling: <b>${MODEL_NAMES[mode]}</b> (din Setări → Modelling). ${{
        open: "Cel mai rapid: 4 prețuri pe lumânare.",
        ohlc1: "4 prețuri pe fiecare minut: prinde SL-urile atinse în interiorul lumânării.",
        real: "Toate ticurile reale: cel mai exact. Prima optimizare pe o perioadă nouă construiește indexul pe minute (câteva secunde), apoi un test durează câteva milisecunde.",
      }[mode]}</div>`);
      if (mode !== "open" && $("sCurrency").value !== "USD") {
        notes.push(`<div class="onote warn">Optimizarea pe ${MODEL_NAMES[mode]} merge deocamdată doar cu cont în USD (Setări → Cont).</div>`);
      }
      const fwd = forwardMs();
      if (fwd) notes.push(`<div class="onote">Forward: optimizarea folosește ${esc($("sStart").value)} → ${ymd(new Date(fwd))}; toate seturile profitabile se rulează apoi pe ${ymd(new Date(fwd))} → ${esc($("sEnd").value)}.</div>`);
    }
    $("sOptNotes").innerHTML = notes.join("");
    for (const a of $$("[data-goto-link]")) a.onclick = (e) => { e.preventDefault(); showTab(a.dataset.gotoLink); };
    return n;
  }
  function optRequest() {
    const st = readOptState(), params = {};
    for (const [k, p] of Object.entries(st.params)) {
      if (!p.on) continue;
      if (k === "ma_price") params[k] = { values: optValues(k, p) };
      else if (k === "close_on_ma") params[k] = { values: [false, true] };
      else {
        if (!optValues(k, p).length) throw new Error(`Parametri: verifică Start / Pas / Stop la ${OPT_NAMES[k]} (Pas > 0, Start ≤ Stop).`);
        params[k] = { start: p.start, step: p.step, stop: p.stop };
      }
    }
    if (!Object.keys(params).length) throw new Error("Bifează în Parametri cel puțin un parametru de optimizat.");
    return { mode: st.mode, criterion: st.criterion, seed: st.seed ? Number(st.seed) : null, params };
  }

  // ---------- Controalele de tip „buton dintr-un grup” ----------
  function buildSeg() {
    $("sTf").innerHTML = info.tfs.map((t) => `<button type="button" data-v="${t}" class="${t === tf ? "on" : ""}">${t}</button>`).join("");
    for (const b of $("sTf").children) b.onclick = () => { tf = b.dataset.v; buildSeg(); save(); };
  }
  function buildModes() {
    const dots = (n) => [1, 2, 3].map((i) => `<i class="${i <= n ? "on" : ""}"></i>`).join("");
    $("sMode").innerHTML = Object.entries(info.modes).map(([k, name]) => {
      const m = MODES[k] || { p: 0, s: 0, d: "" };
      return `<button type="button" class="mode ${k === mode ? "on" : ""}" data-v="${k}" role="radio" aria-checked="${k === mode}">` +
        `<b>${esc(name)}</b><p>${m.d}</p><div class="meters"><span>Precizie${dots(m.p)}</span><span>Viteză${dots(m.s)}</span></div></button>`;
    }).join("");
    for (const b of $("sMode").children) b.onclick = () => { mode = b.dataset.v; buildModes(); updateOpt(); save(); };
  }

  // ---------- Perioada, forward și cronologia ----------
  function applyPreset() {
    for (const b of $("sPresets").children) b.classList.toggle("on", b.dataset.p === preset);
    if (preset !== "custom") {
      const cov = info?.coverage?.[$("sSymbol").value];
      const now = new Date();
      const end = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1));   // azi inclus
      const start = new Date(end);
      if (preset === "all" && cov) start.setTime(utcMs(cov.first));
      else start.setUTCMonth(start.getUTCMonth() - { m1: 1, m3: 3, m6: 6, y1: 12 }[preset]);
      $("sStart").value = ymd(start);
      $("sEnd").value = ymd(end);
    }
    updatePeriod();
  }
  for (const b of $("sPresets").children) b.onclick = () => { preset = b.dataset.p; applyPreset(); };
  for (const id of ["sStart", "sEnd"]) $(id).addEventListener("change", () => { preset = "custom"; applyPreset(); });

  // Forward 1/N: ultima parte (1/N) din perioadă; data se rotunjește la zi (ca în MT5).
  function forwardMs() {
    const f = $("sForward").value;
    if (f === "0") return null;
    if (f === "custom") return $("sForwardDate").value ? utcMs($("sForwardDate").value) : null;
    const s = utcMs($("sStart").value), e = utcMs($("sEnd").value);
    return Math.floor((e - (e - s) / Number(f)) / 86400000) * 86400000;
  }
  function updatePeriod() {
    const f = $("sForward").value;
    $("sForwardDate").disabled = f !== "custom";
    const fwd = forwardMs();
    if (f !== "custom") $("sForwardDate").value = fwd ? ymd(new Date(fwd)) : "";
    drawRange();
    const cov = info?.coverage?.[$("sSymbol").value];
    const s = utcMs($("sStart").value), e = utcMs($("sEnd").value);
    const days = Math.round((e - s) / DAY);
    const parts = [];
    if (days > 0) parts.push(`${days} ${days === 1 ? "zi" : "zile"}`);
    else parts.push("Data de start trebuie să fie înaintea datei de final.");
    if (cov && s < utcMs(cov.first)) parts.push(`datele încep la ${cov.first}, deci testul pornește de acolo`);
    if (fwd) parts.push(`forward de la ${ymd(new Date(fwd))} (marcat pe grafic; testul acoperă toată perioada; optimizarea folosește doar perioada de dinainte)`);
    $("sPeriodHint").textContent = parts.join(" · ");
    if (optRow("ma_period")?.querySelector("[data-o]")) updateOpt();
    save();
  }
  for (const id of ["sForward", "sForwardDate"]) $(id).addEventListener("change", updatePeriod);

  // ---------- Cronologia ca slider ----------
  // Bara merge de la primul tic din QuestDB până la ultimul (sau azi). Mânerele mută startul și
  // finalul, mijlocul mută toată perioada (aceeași durată), un click pe bară aduce acolo mânerul
  // cel mai apropiat. Totul se rotunjește la zi; câmpurile De la / Până la se schimbă pe loc.
  // Tastatura pe mâner: ← → o zi (cu Shift o săptămână), PageUp / PageDown 30 de zile, Home / End.
  // Escape în timpul tragerii revine la perioada de dinainte.
  let drag = null;      // tragerea în curs: {what, x0, s0, e0, lo, hi, moved}
  let tickKey = "";
  const fmtDay = (t) => { const d = new Date(t); return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`; };
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

  // Domeniul barei; rămâne fix cât timp se trage, ca scara să nu fugă de sub mouse.
  function domain() {
    if (drag) return drag;
    const cov = info?.coverage?.[$("sSymbol").value];
    if (!cov) return null;
    const now = new Date();
    const today = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1);
    return { lo: utcMs(cov.first), hi: Math.max(utcMs(cov.last) + DAY, today, utcMs($("sEnd").value) || 0) };
  }
  // Perioada curentă, cu startul adus la primul tic (de acolo pornește oricum testul).
  function period(d) {
    const s = utcMs($("sStart").value), e = utcMs($("sEnd").value);
    if (!Number.isFinite(s) || !Number.isFinite(e) || e <= s) return null;
    return { s: clamp(s, d.lo, d.hi - DAY), e: clamp(e, d.lo + DAY, d.hi) };
  }
  function setRange(s, e) {
    const a = ymd(new Date(s)), b = ymd(new Date(e));
    if (a === $("sStart").value && b === $("sEnd").value) return;
    $("sStart").value = a;
    $("sEnd").value = b;
    preset = "custom";
    applyPreset();
  }

  function drawRange() {
    const el = $("tlRange");
    const cov = info?.coverage?.[$("sSymbol").value];
    const d = domain(), p = d && period(d);
    el.classList.toggle("nodata", !p);
    if (cov) {
      $("tlFirst").textContent = cov.first;
      $("tlLast").textContent = cov.last;
      $("tlInfo").textContent = "ticuri complete în QuestDB";
    }
    if (!p) return;
    const f = (t) => clamp((t - d.lo) / (d.hi - d.lo), 0, 1);
    const a = f(p.s), b = f(p.e);
    const fwd = forwardMs();
    Object.assign($("tlSel").style, { left: `${a * 100}%`, right: `${(1 - b) * 100}%` });
    Object.assign($("tlBody").style, { left: `${a * 100}%`, right: `${(1 - b) * 100}%` });
    $("tlFwd").hidden = !fwd;
    if (fwd) Object.assign($("tlFwd").style, { left: `${f(fwd) * 100}%`, right: `${(1 - b) * 100}%` });
    const days = (t) => Math.round((t - d.lo) / DAY);
    for (const [k, t, frac] of [["tlA", p.s, a], ["tlB", p.e, b]]) {
      const knob = $(k);
      knob.style.left = `${frac * 100}%`;
      knob.setAttribute("aria-valuemin", 0);
      knob.setAttribute("aria-valuemax", days(d.hi));
      knob.setAttribute("aria-valuenow", days(t));
      knob.setAttribute("aria-valuetext", fmtDay(t));
    }
    placeTips(a, b, p);
    drawTicks(d);
  }

  // Etichetele cu datele de deasupra mânerelor; dacă s-ar suprapune, devin una singură.
  function placeTips(a, b, p) {
    const w = $("tlRange").clientWidth;
    if (!w) return;
    const A = $("tlTipA"), B = $("tlTipB"), M = $("tlTipM");
    A.textContent = fmtDay(p.s);
    B.textContent = fmtDay(p.e);
    M.textContent = `${fmtDay(p.s)} → ${fmtDay(p.e)}`;
    const place = (tip, x) => {
      const tw = tip.offsetWidth, left = clamp(x - tw / 2, -6, w + 6 - tw);
      tip.style.left = `${left}px`;
      tip.style.setProperty("--ax", `${clamp(x - left, 8, tw - 8)}px`);
      return [left, left + tw];
    };
    const ra = place(A, a * w), rb = place(B, b * w);
    const merged = ra[1] + 6 > rb[0];
    if (merged) place(M, (a + b) / 2 * w);
    A.classList.toggle("off", merged);
    B.classList.toggle("off", merged);
    M.classList.toggle("off", !merged);
    const hot = drag?.what;
    A.classList.toggle("hot", hot === "a" || hot === "body");
    B.classList.toggle("hot", hot === "b" || hot === "body");
    M.classList.toggle("hot", !!hot);
  }

  // Liniile lunilor pe bară și etichetele de sub ea (pasul se alege după lățime: luni, trimestre, ani).
  function drawTicks(d) {
    const w = $("tlRange").clientWidth;
    const key = `${d.lo}|${d.hi}|${w}`;
    if (!w || key === tickKey) return;
    tickKey = key;
    const months = (d.hi - d.lo) / (30.44 * DAY);
    const step = [1, 2, 3, 6, 12, 24, 60].find((m) => w / (months / m) >= 58) || 120;
    const lineStep = w / months >= 7 ? 1 : step;
    let lines = "", labels = "";
    const lo = new Date(d.lo);
    for (let t = Date.UTC(lo.getUTCFullYear(), lo.getUTCMonth() + 1, 1); t < d.hi;) {
      const dt = new Date(t), m = dt.getUTCMonth(), idx = dt.getUTCFullYear() * 12 + m;
      const x = (t - d.lo) / (d.hi - d.lo);
      if (idx % lineStep === 0) lines += `<i class="${m ? "" : "y"}" style="left:${x * 100}%"></i>`;
      if (idx % step === 0 && x * w > 16 && x * w < w - 16)
        labels += `<span class="${m ? "" : "y"}" style="left:${x * 100}%">${m ? MONTHS[m] : dt.getUTCFullYear()}</span>`;
      t = Date.UTC(dt.getUTCFullYear(), m + 1, 1);
    }
    $("tlMarks").innerHTML = lines;
    $("tlTicks").innerHTML = labels;
  }

  {
    const el = $("tlRange");
    const snap = (t) => Math.round(t / DAY) * DAY;
    const endDrag = (cancel) => {
      if (!drag) return;
      const { s0, e0, moved } = drag;
      drag = null;
      el.classList.remove("dragging", "drag-body", "drag-knob");
      for (const k of [$("tlA"), $("tlB")]) k.classList.remove("active");
      if (cancel && moved) setRange(s0, e0);
      drawRange();   // domeniul se poate schimba după ce s-a mutat finalul
    };
    el.addEventListener("pointerdown", (ev) => {
      if (ev.button !== 0 || drag) return;
      const d = domain(), p = d && period(d);
      if (!p) return;
      const r = el.getBoundingClientRect(), x = ev.clientX - r.left;
      const xa = (p.s - d.lo) / (d.hi - d.lo) * r.width, xb = (p.e - d.lo) / (d.hi - d.lo) * r.width;
      let what = ev.target.closest(".knob")?.dataset.k || (ev.target === $("tlBody") ? "body" : null);
      // Mânere aproape lipite: decide direcția primei mișcări (stânga = start, dreapta = final).
      if (what && what !== "body" && Math.abs(xb - xa) < 24) what = "ab";
      ev.preventDefault();
      drag = { what, x0: x, s0: p.s, e0: p.e, lo: d.lo, hi: d.hi, moved: false };
      if (!what) {
        // Click pe bară în afara perioadei: mânerul cel mai apropiat sare acolo și se poate trage în continuare.
        const t = snap(d.lo + x / r.width * (d.hi - d.lo));
        drag.what = Math.abs(x - xa) < Math.abs(x - xb) ? "a" : "b";
        if (drag.what === "a") drag.s1 = clamp(t, d.lo, p.e - DAY);
        else drag.e1 = clamp(t, p.s + DAY, d.hi);
        setRange(drag.s1 ?? p.s, drag.e1 ?? p.e);
        drag.moved = true;
      }
      el.setPointerCapture(ev.pointerId);
      el.classList.add("dragging", drag.what === "body" ? "drag-body" : "drag-knob");
      const knob = { a: $("tlA"), b: $("tlB") }[drag.what];
      knob?.classList.add("active");
      knob?.focus({ preventScroll: true });
      drawRange();
    });
    el.addEventListener("pointermove", (ev) => {
      if (!drag || !el.hasPointerCapture(ev.pointerId)) return;
      const r = el.getBoundingClientRect(), dx = ev.clientX - r.left - drag.x0;
      if (drag.what === "ab") {
        if (Math.abs(dx) < 2) return;
        drag.what = dx < 0 ? "a" : "b";
        $(drag.what === "a" ? "tlA" : "tlB").classList.add("active");
      }
      const dt = snap(dx / r.width * (drag.hi - drag.lo));
      const s0 = drag.s1 ?? drag.s0, e0 = drag.e1 ?? drag.e0;
      let s = s0, e = e0;
      if (drag.what === "a") s = clamp(s0 + dt, drag.lo, e0 - DAY);
      else if (drag.what === "b") e = clamp(e0 + dt, s0 + DAY, drag.hi);
      else { const k = clamp(dt, drag.lo - s0, drag.hi - e0); s += k; e += k; }
      if (!drag.moved && s === drag.s0 && e === drag.e0) return;
      drag.moved = true;
      setRange(s, e);
    });
    el.addEventListener("pointerup", () => endDrag(false));
    el.addEventListener("pointercancel", () => endDrag(true));
    el.addEventListener("lostpointercapture", () => endDrag(false));
    // Escape: anulează tragerea (și nu închide fereastra testerului).
    document.addEventListener("keydown", (ev) => {
      if (ev.key !== "Escape" || !drag) return;
      ev.stopImmediatePropagation();
      endDrag(true);
    }, true);

    for (const knob of [$("tlA"), $("tlB")]) knob.addEventListener("keydown", (ev) => {
      const d = domain(), p = d && period(d);
      if (!p) return;
      const n = { ArrowLeft: -1, ArrowDown: -1, ArrowRight: 1, ArrowUp: 1, PageDown: -30, PageUp: 30 }[ev.key];
      const isA = knob.dataset.k === "a";
      let t = isA ? p.s : p.e;
      if (n) t += n * (ev.shiftKey && Math.abs(n) === 1 ? 7 : 1) * DAY;
      else if (ev.key === "Home") t = d.lo;
      else if (ev.key === "End") t = d.hi;
      else return;
      ev.preventDefault();
      if (isA) setRange(clamp(t, d.lo, p.e - DAY), p.e);
      else setRange(p.s, clamp(t, p.s + DAY, d.hi));
    });
    // Lățimea barei se schimbă cu fereastra (și e 0 cât timp tab-ul Setări e ascuns).
    new ResizeObserver(() => drawRange()).observe(el);
  }

  // ---------- Pornire ----------
  async function init() {
    const saved = load();
    buildOptCells();
    writeInputs({ ...DEFAULT_INPUTS, ...(saved.inputs || {}) });
    writeOptState(saved.opt || {});
    for (const l of LEVERAGES) $("sLeverage").add(new Option(`1:${l}`, String(l)));
    try {
      const r = await fetch("/api/tester/info");
      info = await r.json();
      if (!r.ok) throw new Error(info.detail || r.statusText);
    } catch (e) {
      toast(`Nu pot citi datele disponibile: ${e.message}`, "err");
      info = { symbols: ["EURUSD"], tfs: ["H1"], modes: { ohlc1: "1 minute OHLC" }, currencies: ["USD"], coverage: {} };
    }
    const s = { symbol: "EURUSD", tf: "H1", period: "m3", forward: "0", modelling: "ohlc1", deposit: 1000, currency: "USD", leverage: 500, ...(saved.settings || {}) };
    for (const sym of info.symbols) $("sSymbol").add(new Option(sym, sym));
    for (const c of info.currencies) $("sCurrency").add(new Option(c, c));
    if (info.symbols.includes(s.symbol)) $("sSymbol").value = s.symbol;
    tf = info.tfs.includes(s.tf) ? s.tf : info.tfs[0];
    mode = info.modes[s.modelling] ? s.modelling : Object.keys(info.modes)[0];
    preset = s.period;
    $("sForward").value = s.forward;
    $("sDeposit").value = s.deposit; $("sCurrency").value = s.currency; $("sLeverage").value = String(s.leverage);
    if (s.start) $("sStart").value = s.start;
    if (s.end) $("sEnd").value = s.end;
    if (s.forward_date) $("sForwardDate").value = s.forward_date;
    buildSeg();
    buildModes();
    applyPreset();
    $("sSymbol").addEventListener("change", applyPreset);
    for (const id of ["sDeposit", "sCurrency", "sLeverage"]) $(id).addEventListener("change", () => { save(); updateOpt(); });
    for (const el of $$("#tab-inputs input, #tab-inputs select")) el.addEventListener("input", () => { markInputs(); save(); });
    $("iReset").onclick = () => { writeInputs(DEFAULT_INPUTS); save(); };
    updateOpt();
    setPlay();
    await restoreLast();
    if (info.running) follow({ id: info.running, kind: info.running_kind || "run", status: "running" });
    else warmOpt();
  }

  // Ultimul test și ultima optimizare terminate rămân pe server (până la repornire): după o reîncărcare
  // a paginii le aducem înapoi, fără să schimbăm tab-ul.
  async function restoreLast() {
    const get = async (id) => { try { const r = await fetch(`/api/tester/${id}?result=true`); return r.ok ? (await r.json()).result : null; } catch { return null; } };
    if (info.last_opt) { const res = await get(info.last_opt); if (res) renderOpt(res); }
    if (info.last_run) { const res = await get(info.last_run); if (res) { render(res); markSelected(); } }
  }

  async function refreshTickMonths() {
    try { const r = await fetch("/api/tester/info"); if (r.ok) { info.tick_months = (await r.json()).tick_months; renderRunHint(); } } catch { /* rămâne lista veche */ }
  }

  function request(kind, period = null) {
    const s = readSettings();
    if (!s.start || !s.end || utcMs(s.end) <= utcMs(s.start)) throw new Error("Data de start trebuie să fie înaintea datei de final.");
    if (!(s.deposit > 0)) throw new Error("Depozitul trebuie să fie pozitiv.");
    const inputs = readInputs();
    if (!(inputs.ma_period >= 1 && inputs.tl_bars >= 10 && inputs.rr > 0 && inputs.risk_pct > 0)) {
      showTab("inputs");
      throw new Error("Verifică parametrii strategiei (perioada ≥ 1, lumânări ≥ 10, RR și riscul pozitive).");
    }
    const opt = kind === "optimize" ? optRequest() : undefined;
    if (kind === "optimize" && s.modelling !== "open" && s.currency !== "USD") {
      throw new Error(`Optimizarea pe ${MODEL_NAMES[s.modelling]} merge deocamdată doar cu cont în USD.`);
    }
    const settings = { ...s, start_ms: utcMs(s.start), end_ms: utcMs(s.end), forward_ms: forwardMs() };
    if (period) Object.assign(settings, period);
    return { kind, samples: Number($("vSamples").value) || 10, inputs, opt, settings };
  }

  let starting = false;   // cererea de pornire e pe drum: un al doilea click nu pornește încă un test
  async function start(kind, period = null) {
    if (current) {   // al doilea click = Stop
      await fetch(`/api/tester/${current.id}/stop`, { method: "POST" }).catch(() => {});
      return;
    }
    if (starting) return;
    if (kind === "run" && $("sOpt").value !== "off") kind = "optimize";
    if (kind === "single") kind = "run";
    let body;
    try { body = request(kind, period); } catch (e) { toast(e.message, "err"); return; }
    save();
    starting = true;
    const r = await fetch("/api/tester/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).catch((e) => ({ ok: false, statusText: e.message, json: async () => ({}) }));
    const job = await r.json().catch(() => ({}));
    starting = false;
    if (!r.ok) { toast(job.detail || r.statusText, "err"); return; }
    if (kind === "verify") $("vOut").innerHTML = `<p class="hint">Rulez funcțiile originale...</p>`;
    follow(job);
  }
  $("tStart").onclick = () => start("run");
  $("vGo").onclick = () => start("verify");

  // Eticheta butonului: „Optimizează” cu optimizarea pornită în Setări, altfel „Start” (un singur test).
  const PLAY_ICON = '<svg viewBox="0 0 16 16"><path d="M5 3l8 5-8 5z"/></svg>';
  const playHtml = () => `${PLAY_ICON}<span>${$("sOpt").value === "off" ? "Start" : "Optimizează"}</span>`;
  const setPlay = () => { if (!current) $("tStart").innerHTML = playHtml(); };
  const STOP = '<svg viewBox="0 0 16 16"><path d="M4 4h8v8H4z"/></svg><span>Stop</span>';
  const PHASES = { start: "Pornesc", data: "Descarc datele", signals: "Calculez semnalele", run: "Simulez", verify: "Verific",
    optimize: "Optimizez", forward: "Forward" };

  async function follow(job) {
    current = job;
    if (job.kind === "optimize") {   // rezultatele vechi se ascund; tab-ul arată progresul
      document.body.classList.add("opt-running");
      $("oLive").hidden = false; $("oResult").hidden = true; $("oEmpty").hidden = true;
      showTab("opt");
    }
    const btn = $("tStart");
    btn.innerHTML = STOP; btn.classList.add("stop");
    $("vGo").disabled = true;
    $("runInfo").hidden = false;
    try {
      for (;;) {
        const pct = Math.round((job.progress || 0) * 100);
        const lv = job.live;
        const phase = (PHASES[job.phase] || "Pornesc") + (lv && job.phase === "optimize" ? ` · generația ${lv.gen}` : "");
        if (job.kind === "optimize") renderOptLive(job);
        $("runBar").style.width = `${pct}%`;
        $("runPhase").textContent = phase;
        $("runPct").textContent = `${pct}% · ${clock(job.elapsed || 0)}`;
        tellParent(`${phase} ${pct}% · ${clock(job.elapsed || 0)}`, true);
        if (job.log?.length) renderJournal(job.log);
        if (job.status !== "running") break;
        await new Promise((res) => setTimeout(res, 500));
        const r = await fetch(`/api/tester/${job.id}`);
        if (!r.ok) throw new Error("Serverul nu mai știe de acest test (a fost repornit?)");
        job = await r.json();
      }
      if (job.status === "done") {
        const r = await fetch(`/api/tester/${job.id}?result=true`);
        const full = await r.json();
        if (job.kind === "verify") renderVerify(full.result);
        else if (job.kind === "optimize") {
          renderOpt(full.result);
          showTab("opt");
          const best = optBest();
          toast(`Optimizare terminată în ${clock(job.elapsed)}: ${fmt(full.result.passes.length, 0)} teste, cel mai bun ${fmt(best?.fitness)} ${full.result.currency}`, "ok");
        } else {
          full.result.elapsed = job.elapsed;
          render(full.result);
          recordRun(full.result);
          if (full.result.settings.modelling === "real") refreshTickMonths();
          markSelected();
          // Sărim la grafic doar din Setări / Parametri / Optimizare (nu și dacă ai deschis singur alt tab între timp).
          if (["settings", "inputs", "opt"].includes(document.querySelector(".tab.active")?.dataset.tab)) showTab("graph");
          const st = full.result.stats;
          toast(`Test terminat în ${clock(job.elapsed)}: ${st.total_trades} tranzacții, profit net ${fmt(st.net_profit, 2, true)} ${full.result.currency}`, "ok");
        }
      } else if (job.status === "cancelled") {
        toast("Test oprit.");
        if (job.kind === "verify") $("vOut").innerHTML = "";
      } else {
        if (job.kind === "verify") $("vOut").innerHTML = `<div class="vres bad">${esc(job.error)}</div>`;
        else { toast(`Testul a eșuat: ${job.error}`, "err"); showTab("journal"); }
      }
    } catch (e) {
      toast(e.message, "err");
    } finally {
      current = null;
      if (job.kind === "optimize") {
        document.body.classList.remove("opt-running");
        $("oLive").hidden = true;
        $("oResult").hidden = !optResult; $("oEmpty").hidden = !!optResult;
      }
      btn.innerHTML = playHtml(); btn.classList.remove("stop");
      $("vGo").disabled = false;
      $("runInfo").hidden = true;
      tellParent(result ? `Ultimul test: ${fmt(result.stats.net_profit, 2, true)} ${result.currency}` : "", false);
    }
  }

  // ---------- Raportul ----------
  function render(res) {
    result = res;
    document.body.classList.add("has-result");
    const st = res.stats, cur = res.currency, s = res.settings;
    const dep = st.initial_deposit;
    const kpi = (label, value, small = "", c = "") => `<div class="kpi"><span>${label}</span><b class="${c}">${value}</b><small>${small}</small></div>`;
    const kpis = kpi("Profit net", `${fmt(st.net_profit, 2, true)}`, `${fmt(st.net_profit / dep * 100, 2, true)}% · ${cur}`, cls(st.net_profit)) +
      kpi("Profit factor", fmt(st.profit_factor), `brut ${fmt(st.gross_profit, 0)} / ${fmt(st.gross_loss, 0)}`) +
      kpi("Drawdown maxim", `${fmt(st.eq_dd_rel_pct)}%`, `equity · ${fmt(st.eq_dd_max)} ${cur}`, st.eq_dd_rel_pct > 20 ? "neg" : "") +
      kpi("Tranzacții", fmt(st.total_trades, 0), `${fmt(st.profit_trades_pct, 1)}% câștigătoare`) +
      kpi("Sharpe", fmt(st.sharpe), `Z-Score ${fmt(st.z_score)}`) +
      kpi("Recovery factor", fmt(st.recovery_factor), `payoff ${fmt(st.expected_payoff)} / tranzacție`);
    for (const el of $$("[data-kpis]")) el.innerHTML = kpis;

    const modeName = info.modes[s.modelling] || s.modelling;
    $("rSummary").innerHTML = [`<b>${esc(s.symbol)}</b> ${esc(s.tf)}`, esc(modeName),
      `${ymd(new Date(s.start_ms))} → ${ymd(new Date(s.end_ms))}`, `depozit <b>${fmt(dep, 0)} ${cur}</b> · 1:${s.leverage}`,
      `calculat în ${clock(res.elapsed || 0)}`].map((x) => `<span>${x}</span>`).join("");
    $("rSummary").hidden = false;

    const kv = (k, v, c = "") => `<div class="kv"><span class="k">${k}</span><span class="v ${c}">${v}</span></div>`;
    const both = (money, pct, pctFirst = false) => pctFirst ? `${fmt(pct)}%<small>${fmt(money)}</small>` : `${fmt(money)}<small>${fmt(pct)}%</small>`;
    const card = (title, rows) => `<section class="card"><h3>${title}</h3>${rows.join("")}</section>`;
    $("rBody").innerHTML = [
      card("Profitabilitate", [kv("Total Net Profit", fmt(st.net_profit), cls(st.net_profit)), kv("Gross Profit", fmt(st.gross_profit)),
        kv("Gross Loss", fmt(st.gross_loss)), kv("Profit Factor", fmt(st.profit_factor)), kv("Expected Payoff", fmt(st.expected_payoff)),
        kv("Recovery Factor", fmt(st.recovery_factor))]),
      card("Drawdown", [kv("Balance Drawdown Absolute", fmt(st.bal_dd_abs)), kv("Balance Drawdown Maximal", both(st.bal_dd_max, st.bal_dd_max_pct)),
        kv("Balance Drawdown Relative", both(st.bal_dd_rel, st.bal_dd_rel_pct, true)), kv("Equity Drawdown Absolute", fmt(st.eq_dd_abs)),
        kv("Equity Drawdown Maximal", both(st.eq_dd_max, st.eq_dd_max_pct)), kv("Equity Drawdown Relative", both(st.eq_dd_rel, st.eq_dd_rel_pct, true)),
        kv("Margin Level", st.margin_level === null ? "—" : `${fmt(st.margin_level)}%`)]),
      card("Raporturi statistice", [kv("Sharpe Ratio", fmt(st.sharpe)),
        kv("Z-Score", st.z_score === null ? "—" : `${fmt(st.z_score)}<small>${fmt(st.z_conf)}%</small>`),
        kv("AHPR", st.ahpr === null ? "—" : `${fmt(st.ahpr, 4)}<small>${fmt((st.ahpr - 1) * 100)}%</small>`),
        kv("GHPR", st.ghpr === null ? "—" : `${fmt(st.ghpr, 4)}<small>${fmt((st.ghpr - 1) * 100)}%</small>`),
        kv("LR Correlation", fmt(st.lr_correlation)), kv("LR Standard Error", fmt(st.lr_std_error))]),
      card("Tranzacții", [kv("Total Trades", fmt(st.total_trades, 0)), kv("Total Deals", fmt(st.total_deals, 0)),
        kv("Short Trades (won %)", `${st.short_trades}<small>${fmt(st.short_won_pct)}%</small>`),
        kv("Long Trades (won %)", `${st.long_trades}<small>${fmt(st.long_won_pct)}%</small>`),
        kv("Profit Trades (% of total)", `${st.profit_trades}<small>${fmt(st.profit_trades_pct)}%</small>`),
        kv("Loss Trades (% of total)", `${st.loss_trades}<small>${fmt(st.loss_trades_pct)}%</small>`)]),
      card("Câștiguri și pierderi", [kv("Largest profit trade", fmt(st.largest_profit), "pos"), kv("Largest loss trade", fmt(st.largest_loss), "neg"),
        kv("Average profit trade", fmt(st.average_profit)), kv("Average loss trade", fmt(st.average_loss)),
        kv("Correlation (Profits,MFE)", fmt(st.corr_profit_mfe)), kv("Correlation (Profits,MAE)", fmt(st.corr_profit_mae)),
        kv("Correlation (MFE,MAE)", fmt(st.corr_mfe_mae))]),
      card("Serii consecutive", [kv("Maximum consecutive wins ($)", `${st.max_consec_wins}<small>${fmt(st.max_consec_wins_money)}</small>`),
        kv("Maximum consecutive losses ($)", `${st.max_consec_losses}<small>${fmt(st.max_consec_losses_money)}</small>`),
        kv("Maximal consecutive profit (count)", `${fmt(st.maximal_consec_profit)}<small>${st.maximal_consec_profit_count}</small>`),
        kv("Maximal consecutive loss (count)", `${fmt(st.maximal_consec_loss)}<small>${st.maximal_consec_loss_count}</small>`),
        kv("Average consecutive wins", st.avg_consec_wins), kv("Average consecutive losses", st.avg_consec_losses)]),
      card("Poziții și date", [kv("Minimal position holding time", st.hold_min || "—"), kv("Maximal position holding time", st.hold_max || "—"),
        kv("Average position holding time", st.hold_avg || "—"), kv("History Quality", `${fmt(st.history_quality, 0)}%`),
        kv("Bars", fmt(st.bars, 0)), kv("Ticks", fmt(st.ticks, 0)), kv("Initial Deposit", fmt(dep))]),
    ].join("");

    $("dealsBadge").hidden = false;
    $("dealsBadge").textContent = fmt(res.deals.length, 0);
    $("dCsv").disabled = !res.deals.length;
    renderDeals();
    renderJournal(res.journal);
    drawGraph();
  }

  // ---------- Optimizarea: progresul live ----------
  function renderOptLive(job) {
    const lv = job.live, st = readOptState();
    const pct = Math.round((job.progress || 0) * 100);
    $("oLiveTitle").textContent = { data: "Pregătesc datele", optimize: lv ? `Generația ${lv.gen}` : "Pregătesc datele și procesele de lucru",
      forward: "Rulez seturile profitabile pe forward" }[job.phase] || "Pornesc";
    $("oLiveSub").textContent = `${st.mode === "genetic" ? "genetică" : "completă"} · ${MODEL_NAMES[mode]} · Balance max · ${clock(job.elapsed || 0)}`;
    $("oLiveBar").style.width = `${pct}%`;
    const stat = (label, value) => `<div><span>${label}</span><b>${value}</b></div>`;
    const plan = lv ? gaPlan(lv.combinations) : null;
    $("oLiveStats").innerHTML = lv ? [stat("Teste", `${fmt(lv.tested, 0)} <small>din ${fmt(lv.combinations, 0)}</small>`),
      stat("Cel mai bun", `${fmt(lv.best)} <small>testul ${lv.best_pass}</small>`),
      stat("Generația", `${lv.gen} <small>minim ${plan.gu}, apoi oprire după ${plan.gc} fără un maxim nou</small>`)].join("")
      : stat("Progres", `${pct}%`);
    $("oLiveBest").innerHTML = lv?.best_values ? `<span class="muted">Cel mai bun set până acum:</span> ${paramChips(lv.best_values)}` : "";
  }
  const paramChips = (values) => Object.entries(values).map(([k, v]) => `<span class="pchip"><em>${OPT_NAMES[k] || k}</em>${esc(valName(k, v))}</span>`).join("");

  // ---------- Optimizarea: rezultatele ----------
  const optModel = () => optResult?.modelling || "open";
  const optBest = () => optResult?.passes.reduce((a, p) => (!a || p.fitness > a.fitness ? p : a), null);
  const profitable = (r, dep) => !!r && r.trades > 0 && r.balance > dep;
  let optPeriod = "full";
  let optAxis = "order";
  // Coloanele tabelului: [cheie, titlu, valoare(p), format, grup].
  function optColumns() {
    const r = optResult;
    const signed = (v) => (v === null || v === undefined ? "—" : `<span class="${cls(v)}">${fmt(v, 2, true)}</span>`);
    const cols = [["pass", "Test", (p) => p.pass, (v) => v, "id"]];
    if (r.mode === "genetic") cols.push(["gen", "Gen.", (p) => p.gen, (v) => v, "id"]);
    cols.push(["fitness", "Rezultat", (p) => p.fitness, (v) => `<b>${fmt(v)}</b>`, "back"], ["profit", "Profit", (p) => p.profit, signed, "back"],
      ["trades", "Tranzacții", (p) => p.trades, (v) => fmt(v, 0), "back"], ["pf", "Profit factor", (p) => p.profit_factor, (v) => fmt(v), "back"],
      ["ep", "Expected payoff", (p) => p.expected_payoff, (v) => fmt(v), "back"], ["rf", "Recovery factor", (p) => p.recovery, (v) => fmt(v), "back"],
      ["dd", "Equity DD %", (p) => p.equity_dd_pct, (v) => fmt(v), "back"]);
    for (const k of r.keys) cols.push([`v:${k}`, OPT_NAMES[k] || k, (p) => p.values[k], (v) => esc(valName(k, v)), "par"]);
    if (r.forward_ms) {
      cols.push(["fwd", "Rezultat", (p) => p.forward?.balance ?? null, (v) => (v === null ? "—" : fmt(v)), "fwd"],
        ["fwdp", "Profit", (p) => p.forward?.profit ?? null, signed, "fwd"],
        ["fwdt", "Tranzacții", (p) => p.forward?.trades ?? null, (v) => (v === null ? "—" : fmt(v, 0)), "fwd"],
        ["fwddd", "Equity DD %", (p) => p.forward?.equity_dd_pct ?? null, (v) => (v === null ? "—" : fmt(v)), "fwd"]);
    }
    return cols;
  }
  // Filtrul de deasupra tabelului: toate testele, doar cele profitabile, sau profitabile și în forward.
  function filteredPasses() {
    const dep = optResult.deposit;
    if (optFilter === "profit") return optResult.passes.filter((p) => profitable(p, dep));
    if (optFilter === "both") return optResult.passes.filter((p) => profitable(p, dep) && profitable(p.forward, dep));
    return optResult.passes;
  }
  function sortedPasses() {
    const cols = optColumns();
    const col = cols.find((c) => c[0] === optSort.key) || cols[0];
    const get = col[2], dir = optSort.asc ? 1 : -1;
    return [...filteredPasses()].sort((a, b) => {
      const x = get(a), y = get(b);
      if (x === y) return a.pass - b.pass;
      if (x === null || x === undefined) return 1;
      if (y === null || y === undefined) return -1;
      return (x < y ? -1 : 1) * dir;
    });
  }
  function renderOpt(res) {
    optResult = res;
    optSort = { key: "fitness", asc: false };
    optFilter = "all";
    selectedPass = null;
    loadRuns();
    const best = optBest(), s = res.settings, dep = res.deposit, cur = res.currency;
    const pct = res.passes.length / res.combinations * 100;
    const nProfit = res.passes.filter((p) => profitable(p, dep)).length;
    const nBoth = res.forward_ms ? res.passes.filter((p) => profitable(p, dep) && profitable(p.forward, dep)).length : 0;

    $("oMeta").innerHTML = [`<b>${esc(s.symbol)}</b> ${esc(s.tf)}`, `<b>${MODEL_NAMES[res.modelling || "open"]}</b>`,
      `optimizare ${ymd(new Date(s.start_ms))} → ${ymd(new Date(res.back_end_ms))}`,
      res.forward_ms ? `forward ${ymd(new Date(res.forward_ms))} → ${ymd(new Date(s.end_ms))}` : "",
      `depozit <b>${fmt(dep, 0)} ${cur}</b> · 1:${s.leverage}`,
      `${res.mode === "genetic" ? "genetică" : "completă"} · Balance max`, res.seed && res.mode === "genetic" ? `sămânța ${res.seed}` : ""]
      .filter(Boolean).map((x) => `<span>${x}</span>`).join("");

    const kpi = (label, value, small = "", c = "") => `<div class="kpi"><span>${label}</span><b class="${c}">${value}</b><small>${small}</small></div>`;
    $("oKpis").innerHTML = [
      kpi("Cel mai bun rezultat", best ? fmt(best.fitness) : "—", best ? `${fmt(best.profit / dep * 100, 2, true)}% · testul ${best.pass}` : "", best ? cls(best.profit) : ""),
      kpi("Teste", fmt(res.passes.length, 0), `din ${fmt(res.combinations, 0)} combinații · ${fmt(pct, pct < 10 ? 1 : 0)}%`),
      res.mode === "genetic" ? kpi("Generații", fmt(res.generations, 0), `populația ${res.population} · minim ${res.min_generations}`)
        : kpi("Algoritm", "Completă", "toate combinațiile"),
      kpi("Profitabile", fmt(nProfit, 0), `${fmt(nProfit / Math.max(1, res.passes.length) * 100, 1)}% din teste`),
      res.forward_ms ? kpi("Profitabile și în forward", fmt(nBoth, 0), `din ${fmt(res.forward_count ?? nProfit, 0)} rulate pe forward`, nBoth ? "pos" : "neg")
        : kpi("Equity DD % (cel mai bun)", best ? `${fmt(best.equity_dd_pct)}%` : "—", "drawdown relativ"),
      kpi("Durată", `${fmt(res.seconds, 1)} s`, `${fmt(res.passes.length / Math.max(0.1, res.seconds), 0)} teste / s`),
    ].join("");

    $("oBest").innerHTML = best ? `<div class="obestl"><span class="olabel">Cel mai bun set</span>
      <div class="pchips">${paramChips(best.values)}</div></div>
      <div class="obestn"><div><span>Rezultat</span><b>${fmt(best.fitness)}</b></div><div><span>Profit factor</span><b>${fmt(best.profit_factor)}</b></div>
        <div><span>Equity DD</span><b>${fmt(best.equity_dd_pct)}%</b></div>${best.forward ? `<div><span>Forward</span><b class="${cls(best.forward.profit)}">${fmt(best.forward.profit, 2, true)}</b></div>` : ""}</div>
      <div class="obestrun"><span>Testul complet:</span>${Object.keys(MODEL_NAMES).filter((m) => m === optModel() || m === (optModel() === "real" ? "open" : "real"))
        .map((m) => `<button type="button" class="btn ${m === "real" ? "primary" : ""}" data-m="${m}">${MODEL_NAMES[m]}</button>`).join("")}</div>` : "";
    for (const b of $("oBest").querySelectorAll("[data-m]")) b.onclick = () => runPass(best, b.dataset.m);

    $("oFilter").innerHTML = [["all", "Toate", res.passes.length], ["profit", "Profitabile", nProfit],
      ...(res.forward_ms ? [["both", "Profitabile și în forward", nBoth]] : [])]
      .map(([f, t, n]) => `<button type="button" data-f="${f}" class="${f === optFilter ? "on" : ""}">${t}<span class="cnt">${fmt(n, 0)}</span></button>`).join("");
    for (const b of $("oFilter").children) b.onclick = () => {
      optFilter = b.dataset.f;
      for (const x of $("oFilter").children) x.classList.toggle("on", x === b);
      renderOptTable();
    };
    for (const b of $("oPeriod").children) b.disabled = b.dataset.v !== "full" && !res.forward_ms;
    if (!res.forward_ms) setOptPeriod("full");

    $("oAxis").innerHTML = `<option value="order">ordinea testelor</option>` +
      res.keys.map((k) => `<option value="${k}">${OPT_NAMES[k] || k}</option>`).join("");
    if (optAxis !== "order" && !res.keys.includes(optAxis)) optAxis = "order";
    $("oAxis").value = optAxis;

    $("oResult").hidden = false;
    $("oEmpty").hidden = true;
    $("optBadge").hidden = false;
    $("optBadge").textContent = fmt(res.passes.length, 0);
    renderJournal(res.journal);
    renderOptTable();
    renderRunHint();
    drawOpt();
  }
  function setOptPeriod(v) {
    optPeriod = v;
    for (const b of $("oPeriod").children) b.classList.toggle("on", b.dataset.v === v);
    renderRunHint();
  }
  for (const b of $("oPeriod").children) b.onclick = () => setOptPeriod(b.dataset.v);
  $("oAxis").onchange = () => { optAxis = $("oAxis").value; drawOpt(); };

  function renderOptTable() {
    const cols = optColumns(), rows = sortedPasses(), best = optBest();
    const groups = { id: "", back: "Perioada optimizată", par: "Parametri", fwd: "Forward" };
    const spans = [];
    for (const c of cols) { if (spans.length && spans.at(-1)[0] === c[4]) spans.at(-1)[1]++; else spans.push([c[4], 1]); }
    const first = new Set(cols.map((c, i) => (i && cols[i - 1][4] !== c[4] ? i : -1)).filter((i) => i > 0));
    $("oTable").tHead.innerHTML = `<tr class="grp">${spans.map(([g, n]) => `<th colspan="${n}" class="g-${g}">${groups[g]}</th>`).join("")}</tr>` +
      `<tr>${cols.map(([k, t, , , g], i) => `<th data-k="${k}" class="g-${g} ${i > 0 ? "r" : ""} ${first.has(i) ? "gs" : ""} ${k === optSort.key ? `sorted${optSort.asc ? " asc" : ""}` : ""}">${t}</th>`).join("")}</tr>`;
    $("oTable").tBodies[0].innerHTML = rows.slice(0, 2000).map((p) => `<tr data-p="${p.pass}" class="${p === best ? "top" : ""} ${p.pass === selectedPass ? "sel" : ""}" title="Clic: testul complet cu acești parametri">` +
      cols.map(([, , get, f, g], i) => `<td class="g-${g} ${i > 0 ? "r" : ""} ${first.has(i) ? "gs" : ""}">${i === 0 && p === best ? `<span class="star" title="cel mai bun">★</span>` : ""}${f(get(p))}${i === 0 ? runBadges(p) : ""}</td>`).join("") + "</tr>").join("");
    $("oCount").textContent = `${fmt(rows.length, 0)} teste${rows.length > 2000 ? " · primele 2 000 afișate după sortare (CSV-ul le are pe toate)" : ""} · clic pe titlul unei coloane pentru sortare`;
    for (const th of $("oTable").tHead.rows[1].cells) th.onclick = () => {
      optSort = { key: th.dataset.k, asc: optSort.key === th.dataset.k ? !optSort.asc : false };
      renderOptTable();
    };
    requestAnimationFrame(() => placeRowAct(selectedRow()));
  }
  // Clic pe un rând: parametrii lui în tab-ul Parametri, apoi testul complet (grafic, statistici, tranzacții)
  // pe perioada aleasă deasupra tabelului. Rezultatele optimizării rămân aici; rândul rulat rămâne marcat.
  // Perioada testului complet ales deasupra tabelului, în ms.
  function passPeriod(per) {
    const s = optResult.settings, fwd = optResult.forward_ms;
    if (!fwd) per = "full";
    const range = per === "back" ? { start_ms: s.start_ms, end_ms: fwd, forward_ms: null }
      : per === "fwd" ? { start_ms: fwd, end_ms: s.end_ms, forward_ms: null }
      : { start_ms: s.start_ms, end_ms: s.end_ms, forward_ms: fwd };
    return { per, ...range };
  }
  // Testul complet al unui set din optimizare, pe perioada aleasă și pe modelling-ul cerut (implicit cel al
  // optimizării; Real ticks = toate ticurile reale, cu execuția la prețul ticului). Modelling-ul din Setări rămâne
  // neschimbat.
  function runPass(p, model = optModel()) {
    if (current) { toast("Așteaptă să se termine testul în curs."); return; }
    const { per, ...range } = passPeriod(optPeriod);
    // Contul, simbolul, timeframe-ul și parametrii ficși ai optimizării (nu cei din Setări, dacă s-au schimbat între timp).
    const o = optResult.settings;
    Object.assign(range, { symbol: o.symbol, tf: o.tf, deposit: o.deposit, currency: o.currency, leverage: o.leverage });
    writeInputs({ ...readInputs(), ...(optResult.inputs || {}), ...p.values });
    save();
    selectedPass = p.pass;
    for (const r of $("oTable").tBodies[0].rows) r.classList.toggle("sel", Number(r.dataset.p) === p.pass);
    const miss = model === "real" ? missingTickMonths(range.start_ms, range.end_ms) : [];
    toast(`Testul ${p.pass} pe ${MODEL_NAMES[model]} · ${PERIOD_NAMES[per]}${miss.length ? ` · descarc întâi ${miss.length} luni de ticuri` : ""}...`);
    start("single", { ...range, modelling: model, from_pass: p.pass, period_key: per, period_label: PERIOD_NAMES[per] });
  }
  $("oTable").tBodies[0].addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-p]");
    if (tr) runPass(optResult.passes[Number(tr.dataset.p) - 1]);
  });
  // Butoanele de pe rândul de sub mouse: rămân pe rândul selectat când mouse-ul iese din tabel.
  const rowAct = $("oRowAct"), wrapEl = $("oTableWrap");
  function placeRowAct(tr) {
    if (!tr) { rowAct.hidden = true; return; }
    rowAct.hidden = false;
    rowAct.dataset.p = tr.dataset.p;
    const table = $("oTable");
    rowAct.style.top = `${table.offsetTop + tr.offsetTop + (tr.offsetHeight - rowAct.offsetHeight) / 2}px`;
    rowAct.style.left = `${wrapEl.scrollLeft + wrapEl.clientWidth - rowAct.offsetWidth - 8}px`;
    for (const b of rowAct.children) b.disabled = !!current;
  }
  const selectedRow = () => (selectedPass ? $("oTable").querySelector(`tbody tr[data-p="${selectedPass}"]`) : null);
  $("oTable").tBodies[0].addEventListener("mouseover", (e) => { const tr = e.target.closest("tr[data-p]"); if (tr) placeRowAct(tr); });
  wrapEl.addEventListener("mouseleave", () => placeRowAct(selectedRow()));
  wrapEl.addEventListener("scroll", () => { if (!rowAct.hidden) placeRowAct($("oTable").querySelector(`tbody tr[data-p="${rowAct.dataset.p}"]`)); });
  rowAct.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-m]");
    if (!b || b.disabled) return;
    e.stopPropagation();
    runPass(optResult.passes[Number(rowAct.dataset.p) - 1], b.dataset.m);
  });

  // Lunile de ticuri care lipsesc de pe disc pentru un test Real ticks (le descarcă întâi din QuestDB, ~20 s pe lună).
  function missingTickMonths(lo, hi) {
    const sym = optResult?.settings.symbol || $("sSymbol").value;
    const have = new Set(info?.tick_months?.[sym] || []);
    const cov = info?.coverage?.[sym];
    const now = new Date(), curMonth = `${now.getUTCFullYear()}-${pad(now.getUTCMonth() + 1)}`;
    const out = [];
    let d = new Date(Math.max(lo, cov ? utcMs(cov.first) : lo));
    d = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1));
    for (; d.getTime() < hi; d = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1))) {
      const m = `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}`;
      if (m !== curMonth && !have.has(m)) out.push(m);
    }
    return out;
  }
  // Rândul de sub filtre: ce face un clic și cât costă un test Real ticks pe perioada aleasă.
  function renderRunHint() {
    if (!optResult) return;
    const { per, start_ms, end_ms } = passPeriod(optPeriod);
    const miss = missingTickMonths(start_ms, end_ms);
    const ticks = miss.length
      ? `<span class="warn">Real ticks: ${miss.length} ${miss.length === 1 ? "lună" : "luni"} de ticuri nu ${miss.length === 1 ? "e" : "sunt"} încă pe disc; primul test le descarcă din QuestDB (~${Math.max(1, Math.round(miss.length * 20 / 60))} min).</span>`
      : `<span class="ok">Real ticks: toate ticurile perioadei sunt pe disc.</span>`;
    const others = Object.keys(MODEL_NAMES).filter((m) => m !== optModel()).map((m) => `<b>${MODEL_NAMES[m]}</b>`).join(" sau ");
    $("oRunHint").innerHTML = `Clic pe un rând = testul complet pe <b>${MODEL_NAMES[optModel()]}</b> (${PERIOD_NAMES[per]}). ` +
      `Treci cu mouse-ul peste un rând pentru ${others}. ${ticks}`;
  }

  // Testele complete pornite din optimizare, pe set și pe modelling / perioadă: se văd în tabel (OP / M1 / RT)
  // și în bara din Grafic / Backtest (comparația între modelling-uri). Se păstrează în browser, pe optimizare.
  let optRuns = {};
  const runsKey = () => `${KEY}Runs:${optResult.settings.symbol}|${optResult.settings.tf}|${optResult.settings.start_ms}|${optResult.back_end_ms}|${optResult.seed}|${optResult.passes.length}`;
  function loadRuns() { try { optRuns = JSON.parse(localStorage.getItem(runsKey())) || {}; } catch { optRuns = {}; } }
  function saveRuns() { try { localStorage.setItem(runsKey(), JSON.stringify(optRuns)); } catch { /* fără stocare */ } }
  function recordRun(res) {
    const s = res.settings;
    if (!s.from_pass || !optResult || !s.period_key) return;
    const st = res.stats;
    (optRuns[s.from_pass] ||= {})[`${s.modelling}|${s.period_key}`] = { m: s.modelling, per: s.period_key, profit: st.net_profit,
      dd: st.eq_dd_rel_pct, trades: st.total_trades, pf: st.profit_factor };
    saveRuns();
    renderOptTable();
  }
  function runBadges(p) {
    return Object.values(optRuns[p.pass] || {}).sort((a, b) => a.m.localeCompare(b.m)).map((r) =>
      `<span class="rbadge ${r.profit > 0 ? "up" : r.profit < 0 ? "down" : "flat"}" title="${MODEL_NAMES[r.m]} · ${PERIOD_NAMES[r.per]}: ` +
      `${fmt(r.profit, 2, true)} · profit factor ${fmt(r.pf)} · equity DD ${fmt(r.dd)}% · ${fmt(r.trades, 0)} tranzacții">${MODEL_SHORT[r.m]}</span>`).join("");
  }
  // Profitul aceluiași set pe un modelling și o perioadă: din testele complete sau, pe modelling-ul optimizării
  // (perioada optimizată / forward), direct din optimizare (aceleași reguli, aceleași tranzacții).
  function runProfit(p, m, per) {
    const r = optRuns[p.pass]?.[`${m}|${per}`];
    if (r) return r.profit;
    if (m !== optModel()) return null;
    if (per === "back") return p.profit;
    if (per === "fwd") return p.forward?.profit ?? null;
    if (per === "full" && !optResult.forward_ms) return p.profit;
    return null;
  }
  // Grafic + Backtest: de unde vine testul (rândul din optimizare și perioada), cu întoarcere la tabel.
  function markSelected() {
    const s = result?.settings;
    for (const el of $$("[data-from]")) {
      el.hidden = !s?.from_pass;
      if (!s?.from_pass) continue;
      const p = optResult?.passes[s.from_pass - 1];
      const m = s.modelling, per = s.period_key;
      let cmp = "";
      if (p && per) {
        const open = runProfit(p, "open", per);    // comparația se face față de Open prices
        cmp = `<span class="fcmp">${Object.keys(MODEL_NAMES).map((k) => {
          const v = runProfit(p, k, per);
          if (v === null || v === undefined) return "";
          const diff = k !== "open" && open ? ` <span class="d">${fmt((v - open) / Math.abs(open) * 100, 0, true)}% față de Open</span>` : "";
          return `<span class="${k === m ? "cur" : ""}">${MODEL_NAMES[k]} <b class="${cls(v)}">${fmt(v, 2, true)}</b>${diff}</span>`;
        }).join("")}</span>`;
      }
      el.innerHTML = `<span class="ftag">Testul ${s.from_pass} din optimizare</span>` +
        `<span class="mtag ${m === "real" ? "real" : ""}">${MODEL_NAMES[m] || m}</span><span class="muted">${esc(s.period_label || "")}</span>` +
        (p ? `<span class="pchips">${paramChips(p.values)}</span>` : "") + cmp +
        `<button type="button" class="linkbtn" data-back>← Înapoi la optimizare</button>`;
      el.querySelector("[data-back]").onclick = () => showTab("opt");
    }
  }
  $("oCsv").onclick = () => {
    const cols = optColumns();
    const names = { back: "", par: "", fwd: "Forward " };
    const lines = [cols.map((c) => `${names[c[4]] || ""}${c[1]}`).join(",")];
    for (const p of sortedPasses()) lines.push(cols.map(([, , get]) => { const v = get(p); return v === null || v === undefined ? "" : String(v); }).join(","));
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/csv" }));
    const s = optResult.settings;
    a.download = `optimizare_${s.symbol}_${s.tf}_${ymd(new Date(s.start_ms))}_${ymd(new Date(optResult.back_end_ms))}.csv`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  };

  // Graficul optimizării. Axa X = ordinea testelor (cum a avansat căutarea; linia = cel mai bun până atunci)
  // sau un parametru (rezultatele pe fiecare valoare; linia = cel mai bun rezultat al valorii, ca să se vadă
  // zonele robuste față de vârfurile izolate). Punctele: albastre = profitabile, gri = în pierdere.
  const oCanvas = $("oCanvas");
  const oOverlay = document.createElement("canvas");
  oOverlay.style.cssText = "position:absolute;inset:0;width:100%;height:100%;pointer-events:none";
  oCanvas.after(oOverlay);
  let ogeo = null;
  const jitter = (n) => ((Math.sin(n * 12.9898) * 43758.5453) % 1 + 1) % 1 - 0.5;   // fix pentru fiecare test
  function drawOpt() {
    if (!optResult || !oCanvas.offsetParent) return;
    const ps = optResult.passes, n = ps.length;
    if (!n) return;
    const { ctx, W, H } = sizeCanvas(oCanvas);
    sizeCanvas(oOverlay);
    const L = 64, R = 16, T = 12, B = 28;
    const dep = optResult.deposit, best = optBest();
    let lo = dep, hi = dep;
    for (const p of ps) { if (p.trades) { lo = Math.min(lo, p.fitness); hi = Math.max(hi, p.fitness); } }
    if (hi - lo < 1e-9) { hi += 1; lo -= 1; }
    const padv = (hi - lo) * 0.07; hi += padv; lo -= padv;
    const k = optAxis !== "order" ? optAxis : null;
    const cats = k ? optResult.values[k] : null;
    const idxOf = k ? new Map(cats.map((v, i) => [String(v), i])) : null;
    const nx = k ? cats.length : n;
    const xr = (i) => L + (nx === 1 ? 0.5 : (k ? (i + 0.5) / nx : i / (n - 1))) * (W - L - R);
    const y = (v) => T + (hi - Math.max(lo, v)) / (hi - lo) * (H - B - T);
    const px = (p, i) => (k ? xr(idxOf.get(String(p.values[k])) + jitter(p.pass) * 0.55) : xr(i));
    ogeo = { k, cats, idxOf, nx, xr, y, px, L, R, T, B };
    ctx.clearRect(0, 0, W, H);
    ctx.font = "11px system-ui, -apple-system, Segoe UI, sans-serif";
    // Grila + etichetele Y.
    const raw = (hi - lo) / 4, mag = 10 ** Math.floor(Math.log10(raw));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw);
    ctx.fillStyle = color("--faint"); ctx.strokeStyle = "rgba(255,255,255,0.05)"; ctx.textAlign = "right";
    for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) {
      const yy = Math.round(y(v)) + 0.5;
      ctx.beginPath(); ctx.moveTo(L, yy); ctx.lineTo(W - R, yy); ctx.stroke();
      ctx.fillText(fmt(v, 0), L - 8, yy + 4);
    }
    // Etichetele X.
    ctx.textAlign = "center";
    if (k) {
      const every = Math.max(1, Math.ceil(nx / Math.max(2, Math.floor((W - L - R) / 46))));
      for (let i = 0; i < nx; i += every) ctx.fillText(valName(k, cats[i]), xr(i), H - 9);
    } else {
      for (let t = 0; t <= 4; t++) { const i = Math.round(t / 4 * (n - 1)); ctx.fillText(`#${ps[i].pass}`, Math.min(W - R - 16, Math.max(L + 16, xr(i))), H - 9); }
    }
    // Depozitul.
    ctx.strokeStyle = "rgba(255,255,255,0.22)"; ctx.setLineDash([3, 4]);
    ctx.beginPath(); ctx.moveTo(L, Math.round(y(dep)) + 0.5); ctx.lineTo(W - R, Math.round(y(dep)) + 0.5); ctx.stroke(); ctx.setLineDash([]);
    // Punctele (în pierdere întâi, ca cele profitabile să fie deasupra).
    for (const good of [false, true]) {
      ctx.fillStyle = good ? "rgba(76,141,255,0.55)" : "rgba(125,134,150,0.35)";
      for (let i = 0; i < n; i++) {
        const p = ps[i];
        if (!p.trades || (p.fitness > dep) !== good) continue;
        ctx.fillRect(px(p, i) - 1.2, y(p.fitness) - 1.2, 2.4, 2.4);
      }
    }
    // Linia: cel mai bun până atunci (ordinea testelor) sau cel mai bun pe fiecare valoare (parametru).
    ctx.strokeStyle = color("--eq"); ctx.lineWidth = 1.8; ctx.lineJoin = "round"; ctx.beginPath();
    if (k) {
      const top = new Array(nx).fill(-Infinity);
      for (const p of ps) { const i = idxOf.get(String(p.values[k])); if (p.trades) top[i] = Math.max(top[i], p.fitness); }
      let started = false;
      top.forEach((v, i) => { if (!Number.isFinite(v)) return; if (started) ctx.lineTo(xr(i), y(v)); else { ctx.moveTo(xr(i), y(v)); started = true; } });
      ogeo.top = top;
    } else {
      let m = -Infinity;
      for (let i = 0; i < n; i++) {
        const prev = m;
        m = Math.max(m, ps[i].fitness);
        if (!i) ctx.moveTo(xr(i), y(m)); else { ctx.lineTo(xr(i), y(prev)); ctx.lineTo(xr(i), y(m)); }
      }
    }
    ctx.stroke(); ctx.lineWidth = 1;
    // Cel mai bun test (inel verde) și testul rulat complet (inel albastru).
    const ring = (p, c) => {
      if (!p) return;
      const i = p.pass - 1;
      ctx.strokeStyle = c; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(px(p, i), y(p.fitness), 5, 0, Math.PI * 2); ctx.stroke(); ctx.lineWidth = 1;
    };
    ring(best, color("--eq"));
    if (selectedPass && selectedPass !== best?.pass) ring(ps[selectedPass - 1], color("--accent"));
    $("oLineName").textContent = k ? `cel mai bun pe fiecare valoare ${OPT_NAMES[k] || k}` : "cel mai bun până atunci";
  }
  // Detaliile de sub mouse: testul cel mai apropiat (ordinea testelor) sau valoarea parametrului.
  oCanvas.addEventListener("mousemove", (e) => {
    if (!ogeo || !optResult) return;
    const r = oCanvas.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top, tip = $("oTip");
    const { ctx } = sizeCanvas(oOverlay);
    if (mx < ogeo.L || mx > r.width - ogeo.R) { tip.hidden = true; return; }
    const ps = optResult.passes, dep = optResult.deposit;
    let html;
    if (ogeo.k) {
      const i = Math.max(0, Math.min(ogeo.nx - 1, Math.floor((mx - ogeo.L) / (r.width - ogeo.L - ogeo.R) * ogeo.nx)));
      const v = ogeo.cats[i], here = ps.filter((p) => String(p.values[ogeo.k]) === String(v));
      const top = here.reduce((a, p) => (!a || p.fitness > a.fitness ? p : a), null);
      const good = here.filter((p) => profitable(p, dep)).length;
      ctx.fillStyle = "rgba(255,255,255,0.04)";
      const w = (r.width - ogeo.L - ogeo.R) / ogeo.nx;
      ctx.fillRect(ogeo.L + i * w, ogeo.T, w, r.height - ogeo.T - ogeo.B);
      html = `<div class="t">${OPT_NAMES[ogeo.k] || ogeo.k} = ${esc(valName(ogeo.k, v))}</div>` +
        `<div><span>Teste</span><span>${fmt(here.length, 0)}</span></div><div><span>Profitabile</span><span>${fmt(good, 0)}</span></div>` +
        (top ? `<div><span>Cel mai bun</span><b>${fmt(top.fitness)}</b></div><div><span class="muted">testul</span><span class="muted">${top.pass}</span></div>` : "");
    } else {
      // Cel mai apropiat punct (în pixeli), nu doar pe X.
      let bi = 0, bd = Infinity;
      for (let i = 0; i < ps.length; i++) {
        if (!ps[i].trades) continue;
        const d = (ogeo.px(ps[i], i) - mx) ** 2 + ((ogeo.y(ps[i].fitness) - my) / 3) ** 2;
        if (d < bd) { bd = d; bi = i; }
      }
      const p = ps[bi];
      ctx.strokeStyle = "rgba(255,255,255,0.8)"; ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.arc(ogeo.px(p, bi), ogeo.y(p.fitness), 4.5, 0, Math.PI * 2); ctx.stroke();
      html = `<div class="t">Testul ${p.pass}${optResult.mode === "genetic" ? ` · generația ${p.gen}` : ""}</div>` +
        `<div><span>Rezultat</span><b>${fmt(p.fitness)}</b></div><div><span>Tranzacții</span><span>${fmt(p.trades, 0)}</span></div>` +
        optResult.keys.map((k) => `<div><span class="muted">${OPT_NAMES[k] || k}</span><span>${esc(valName(k, p.values[k]))}</span></div>`).join("");
    }
    tip.innerHTML = html;
    tip.hidden = false;
    const tw = tip.offsetWidth;
    tip.style.left = `${mx + 16 + tw > r.width ? mx - tw - 16 : mx + 16}px`;
    tip.style.top = `${Math.max(4, Math.min(r.height - tip.offsetHeight - 4, my - 20))}px`;
  });
  oCanvas.addEventListener("mouseleave", () => { $("oTip").hidden = true; sizeCanvas(oOverlay); });

  // ---------- Tranzacții ----------
  // Profitul fiecărui deal de ieșire, ca filtrele „câștigătoare / pierzătoare” să arate și deal-ul de intrare pereche.
  function dealRows() {
    const deals = result.deals;
    const rows = [];
    for (let i = 0; i < deals.length; i++) {
      const d = deals[i];
      const out = d.entry === "out" ? d : deals[i + 1];
      const dir = d.entry === "in" ? d.type : (d.type === "buy" ? "sell" : "buy");
      if (dealFilter === "win" && !(out && out.profit > 0)) continue;
      if (dealFilter === "loss" && !(out && out.profit < 0)) continue;
      if ((dealFilter === "buy" || dealFilter === "sell") && dir !== dealFilter) continue;
      rows.push(d);
    }
    return rows;
  }
  function renderDeals() {
    if (!result) return;
    const s = result.settings, dg = result.digits;
    const rows = dealRows();
    const html = rows.slice(0, 5000).map((d) => `<tr><td>${fmtTime(d.time)}</td><td>${d.deal}</td><td>${esc(s.symbol)}</td>` +
      `<td><span class="pill ${d.type}">${d.type}</span></td><td class="dir">${d.entry}</td><td class="r">${fmt(d.volume)}</td>` +
      `<td class="r">${fmt(d.price, dg)}</td><td class="r ${d.entry === "out" ? cls(d.profit) : ""}">${d.entry === "out" ? fmt(d.profit, 2, true) : ""}</td>` +
      `<td class="r">${fmt(d.balance)}</td><td class="cm">${esc(d.comment)}</td></tr>`);
    const first = dealFilter === "all" ? `<tr class="bal"><td>${fmtTime(s.start_ms)}</td><td>1</td><td></td><td>balance</td><td></td><td></td><td></td>` +
      `<td class="r">${fmt(result.stats.initial_deposit)}</td><td class="r">${fmt(result.stats.initial_deposit)}</td><td></td></tr>` : "";
    $("dTable").tBodies[0].innerHTML = first + html.join("");
    $("dCount").textContent = `${fmt(rows.length, 0)} deal-uri${rows.length > 5000 ? " (primele 5 000 afișate; CSV-ul le are pe toate)" : ""}`;
  }
  for (const b of $("dFilter").children) {
    b.onclick = () => {
      dealFilter = b.dataset.f;
      for (const x of $("dFilter").children) x.classList.toggle("on", x === b);
      renderDeals();
    };
  }
  $("dCsv").onclick = () => {
    const s = result.settings;
    const lines = [["time_utc", "deal", "symbol", "type", "direction", "volume", "price", "profit", "balance", "comment"].join(",")];
    for (const d of dealRows()) {
      lines.push([fmtTime(d.time), d.deal, s.symbol, d.type, d.entry, d.volume, d.price.toFixed(result.digits),
        d.entry === "out" ? d.profit.toFixed(2) : "", d.balance.toFixed(2), `"${String(d.comment).replace(/"/g, '""')}"`].join(","));
    }
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/csv" }));
    a.download = `backtest_${s.symbol}_${s.tf}_${ymd(new Date(s.start_ms))}_${ymd(new Date(s.end_ms))}.csv`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  };

  // ---------- Jurnal ----------
  function renderJournal(lines) {
    $("jLog").innerHTML = lines.map((l) => {
      const m = /^(\d\d:\d\d:\d\d)\s+(.*)$/s.exec(l);
      const text = m ? m[2] : l;
      return `<div>${m ? `<span class="t">${m[1]}</span>` : ""}<span class="${/eroare|eșuat/i.test(text) ? "e" : ""}">${esc(text)}</span></div>`;
    }).join("");
  }
  $("jCopy").onclick = async () => {
    try { await navigator.clipboard.writeText($("jLog").innerText); toast("Jurnalul a fost copiat.", "ok"); } catch { toast("Nu pot copia în clipboard.", "err"); }
  };

  // ---------- Verificarea ----------
  function renderVerify(v) {
    const yn = (x) => (x === null || x === undefined ? "—" : x ? "da" : "nu");
    const rows = v.results.map((r) => `<tr><td>${fmtTime(r.time * 1000)}</td><td>${r.original}</td><td>${r.fast}</td>` +
      `<td>${yn(r.exit_original.buy)} / ${yn(r.exit_original.sell)}</td><td>${yn(r.exit_fast.buy)} / ${yn(r.exit_fast.sell)}</td>` +
      `<td class="${r.ok ? "pos" : "neg"}">${r.ok ? "identic" : "diferit"}</td></tr>`).join("");
    $("vOut").innerHTML = `<div class="vres ${v.mismatches ? "bad" : "ok"}">${v.mismatches ? `${v.mismatches} diferențe` : "Toate identice cu strategy.py"} ` +
      `· ${v.checked} lumânări verificate în ${fmt(v.seconds, 1)} s · ${v.signals_in_period} semnale în perioadă</div>` +
      `<table class="mini"><thead><tr><th>Lumânarea (deschidere, UTC)</th><th>verify_entry original</th><th>copia rapidă</th>` +
      `<th>verify_exit original (buy / sell)</th><th>copia rapidă</th><th></th></tr></thead><tbody>${rows}</tbody></table>`;
  }

  // ---------- Graficul ----------
  // Axa X = punctele graficului în ordine (timpul de tranzacționare, fără weekenduri), ca în MT5.
  const canvas = $("gCanvas");
  const overlay = document.createElement("canvas");
  overlay.style.cssText = "position:absolute;inset:0;width:100%;height:100%;pointer-events:none";
  canvas.after(overlay);
  const show = { bal: true, eq: true, load: true };
  for (const b of $$(".lg")) b.onclick = () => { show[b.dataset.s] = !show[b.dataset.s]; b.classList.toggle("on", show[b.dataset.s]); drawGraph(); };
  let geo = null;

  function color(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
  function sizeCanvas(c) {
    const dpr = window.devicePixelRatio || 1;
    const W = c.clientWidth, H = c.clientHeight;
    c.width = W * dpr; c.height = H * dpr;
    const ctx = c.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx, W, H };
  }
  function dateLabel(t, long) {
    const d = new Date(t * 1000);
    return long ? `${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}` : `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`;
  }

  function drawGraph() {
    if (!result || !canvas.offsetParent) return;
    const g = result.graph;
    if (!g.length) return;
    const { ctx, W, H } = sizeCanvas(canvas);
    sizeCanvas(overlay);
    const L = 64, R = 14, T = 12, B = 26;
    const loadH = show.load ? Math.round((H - T - B) * 0.15) : 0;
    const mainB = H - B - (loadH ? loadH + 14 : 0);
    const n = g.length;
    const dep = result.stats.initial_deposit;
    let lo = dep, hi = dep;
    for (const p of g) {
      if (show.bal) { lo = Math.min(lo, p[1]); hi = Math.max(hi, p[1]); }
      if (show.eq) { lo = Math.min(lo, p[2]); hi = Math.max(hi, p[3]); }
    }
    if (hi - lo < 1e-9) { hi += 1; lo -= 1; }
    const padv = (hi - lo) * 0.06; hi += padv; lo -= padv;
    const x = (i) => L + (n === 1 ? 0 : i / (n - 1)) * (W - L - R);
    const y = (v) => T + (hi - v) / (hi - lo) * (mainB - T);
    geo = { x, y, n, L, R, W, T, mainB, H, B, g };

    ctx.clearRect(0, 0, W, H);
    ctx.font = "11px system-ui, -apple-system, Segoe UI, sans-serif";
    ctx.lineWidth = 1;
    // Forward: zona din dreapta, ușor colorată.
    let fi = -1;
    if (result.forward_ms) {
      fi = g.findIndex((p) => p[0] * 1000 >= result.forward_ms);
      if (fi > 0) {
        ctx.fillStyle = "rgba(176,124,255,0.05)";
        ctx.fillRect(x(fi), T, W - R - x(fi), H - B - T);
      }
    }
    // Grila orizontală + etichetele Y (pași „rotunzi”).
    const raw = (hi - lo) / 5, mag = 10 ** Math.floor(Math.log10(raw));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw);
    const dec = step < 1 ? Math.min(4, Math.ceil(-Math.log10(step))) : 0;
    ctx.fillStyle = color("--faint"); ctx.strokeStyle = "rgba(255,255,255,0.05)"; ctx.textAlign = "right";
    for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) {
      const yy = Math.round(y(v)) + 0.5;
      ctx.beginPath(); ctx.moveTo(L, yy); ctx.lineTo(W - R, yy); ctx.stroke();
      ctx.fillText(fmt(v, dec), L - 8, yy + 4);
    }
    // Etichetele X: date la intervale egale.
    const span = g[n - 1][0] - g[0][0];
    const long = span > 200 * 86400;
    const ticks = Math.max(2, Math.floor((W - L - R) / 95));
    ctx.textAlign = "center";
    for (let k = 0; k <= ticks; k++) {
      const i = Math.round(k / ticks * (n - 1));
      const xx = Math.min(W - R - 24, Math.max(L + 24, x(i)));
      ctx.fillText(dateLabel(g[i][0], long), xx, H - 8);
    }
    // Depozitul inițial: linie punctată.
    ctx.strokeStyle = "rgba(255,255,255,0.18)"; ctx.setLineDash([3, 4]);
    ctx.beginPath(); ctx.moveTo(L, Math.round(y(dep)) + 0.5); ctx.lineTo(W - R, Math.round(y(dep)) + 0.5); ctx.stroke();
    ctx.setLineDash([]);
    // Forward: linia verticală.
    if (fi > 0) {
      ctx.strokeStyle = "rgba(176,124,255,0.6)"; ctx.setLineDash([4, 4]);
      ctx.beginPath(); ctx.moveTo(Math.round(x(fi)) + 0.5, T); ctx.lineTo(Math.round(x(fi)) + 0.5, H - B); ctx.stroke(); ctx.setLineDash([]);
      ctx.fillStyle = color("--load"); ctx.textAlign = "left"; ctx.fillText("forward", x(fi) + 6, T + 10);
    }
    // Equity: zona dintre minimul și valoarea de la sfârșit (căderile din interiorul lumânărilor) + linia.
    if (show.eq) {
      const grad = ctx.createLinearGradient(0, T, 0, mainB);
      grad.addColorStop(0, "rgba(38,166,154,0.22)"); grad.addColorStop(1, "rgba(38,166,154,0.02)");
      ctx.fillStyle = grad;
      ctx.beginPath(); ctx.moveTo(x(0), mainB);
      g.forEach((p, i) => ctx.lineTo(x(i), y(p[3])));
      ctx.lineTo(x(n - 1), mainB); ctx.closePath(); ctx.fill();
      ctx.strokeStyle = color("--eq"); ctx.lineWidth = 1.2; ctx.lineJoin = "round";
      ctx.beginPath();
      g.forEach((p, i) => { const xx = x(i); if (i) ctx.lineTo(xx, y(p[2])); else ctx.moveTo(xx, y(p[2])); ctx.lineTo(xx, y(p[3])); });
      ctx.stroke();
    }
    // Balanța: linie în trepte.
    if (show.bal) {
      ctx.strokeStyle = color("--bal"); ctx.lineWidth = 2; ctx.lineJoin = "round";
      ctx.beginPath();
      g.forEach((p, i) => { const xx = x(i); if (i) ctx.lineTo(xx, y(g[i - 1][1])); else ctx.moveTo(xx, y(p[1])); ctx.lineTo(xx, y(p[1])); });
      ctx.stroke();
    }
    // Încărcarea depozitului (marja / equity), jos.
    if (loadH) {
      const top = H - B - loadH;
      ctx.strokeStyle = "rgba(255,255,255,0.06)";
      ctx.beginPath(); ctx.moveTo(L, top - 7.5); ctx.lineTo(W - R, top - 7.5); ctx.stroke();
      const loadMax = Math.max(1, ...g.map((p) => p[4]));
      ctx.fillStyle = "rgba(176,124,255,0.75)";
      const bw = Math.max(1, (W - L - R) / n);
      for (let i = 0; i < n; i++) {
        if (g[i][4] <= 0) continue;
        const hgt = Math.max(1, g[i][4] / loadMax * loadH);
        ctx.fillRect(x(i) - bw / 2, H - B - hgt, bw, hgt);
      }
      ctx.fillStyle = color("--faint"); ctx.textAlign = "right";
      ctx.fillText(`${fmt(loadMax, 0)}%`, L - 8, top + 8);
      ctx.fillText("0%", L - 8, H - B);
    }
    const last = g[n - 1];
    $("gInfo").textContent = `balanță finală ${fmt(last[1])} ${result.currency}`;
  }
  window.addEventListener("resize", () => requestAnimationFrame(() => { drawGraph(); drawOpt(); }));

  // Linia de urmărire și detaliile punctului de sub mouse.
  canvas.addEventListener("mousemove", (e) => {
    if (!geo) return;
    const r = canvas.getBoundingClientRect();
    const mx = e.clientX - r.left;
    const tip = $("gTip");
    const { ctx, W } = sizeCanvas(overlay);
    if (mx < geo.L || mx > W - geo.R) { tip.hidden = true; return; }
    const i = Math.max(0, Math.min(geo.n - 1, Math.round((mx - geo.L) / (W - geo.L - geo.R) * (geo.n - 1))));
    const p = geo.g[i], xx = Math.round(geo.x(i)) + 0.5;
    ctx.strokeStyle = "rgba(255,255,255,0.25)"; ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(xx, geo.T); ctx.lineTo(xx, geo.H - geo.B); ctx.stroke(); ctx.setLineDash([]);
    const dot = (v, c) => { ctx.fillStyle = c; ctx.beginPath(); ctx.arc(xx, geo.y(v), 3.5, 0, Math.PI * 2); ctx.fill(); };
    if (show.eq) dot(p[3], color("--eq"));
    if (show.bal) dot(p[1], color("--bal"));
    const cur = result.currency;
    tip.innerHTML = `<div class="t">${fmtTime(p[0] * 1000)}</div><div><span>Balanță</span><b>${fmt(p[1])} ${cur}</b></div>` +
      `<div><span>Equity</span><b>${fmt(p[3])}</b></div><div><span class="muted">minim</span><span class="muted">${fmt(p[2])}</span></div>` +
      `<div><span>Încărcare</span><span>${fmt(p[4])}%</span></div>`;
    tip.hidden = false;
    const tw = tip.offsetWidth;
    tip.style.left = `${mx + 16 + tw > r.width ? mx - tw - 16 : mx + 16}px`;
    tip.style.top = `${Math.max(4, Math.min(r.height - tip.offsetHeight - 4, e.clientY - r.top - 20))}px`;
  });
  canvas.addEventListener("mouseleave", () => { $("gTip").hidden = true; sizeCanvas(overlay); });

  init();
})();
