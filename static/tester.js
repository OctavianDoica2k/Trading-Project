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

  let info = null;      // /api/tester/info
  let current = null;   // testul în curs: {id, kind}
  let result = null;    // ultimul raport
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
  }
  for (const t of $$(".tab")) t.onclick = () => showTab(t.dataset.tab);

  // ---------- Setări salvate în browser ----------
  const load = () => { try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch { return {}; } };
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify({ settings: readSettings(), inputs: readInputs() })); } catch { /* fără stocare */ } };

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
    for (const b of $("sMode").children) b.onclick = () => { mode = b.dataset.v; buildModes(); save(); };
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
    if (fwd) parts.push(`forward de la ${ymd(new Date(fwd))} (marcat pe grafic; testul acoperă toată perioada)`);
    $("sPeriodHint").textContent = parts.join(" · ");
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
    writeInputs({ ...DEFAULT_INPUTS, ...(saved.inputs || {}) });
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
    for (const id of ["sDeposit", "sCurrency", "sLeverage"]) $(id).addEventListener("change", save);
    for (const el of $$("#tab-inputs input, #tab-inputs select")) el.addEventListener("input", () => { markInputs(); save(); });
    $("iReset").onclick = () => { writeInputs(DEFAULT_INPUTS); save(); };
    if (info.running) follow({ id: info.running, kind: "run", status: "running" });
  }

  function request(kind) {
    const s = readSettings();
    if (!s.start || !s.end || utcMs(s.end) <= utcMs(s.start)) throw new Error("Data de start trebuie să fie înaintea datei de final.");
    if (!(s.deposit > 0)) throw new Error("Depozitul trebuie să fie pozitiv.");
    const inputs = readInputs();
    if (!(inputs.ma_period >= 1 && inputs.tl_bars >= 10 && inputs.rr > 0 && inputs.risk_pct > 0)) {
      showTab("inputs");
      throw new Error("Verifică parametrii strategiei (perioada ≥ 1, lumânări ≥ 10, RR și riscul pozitive).");
    }
    return {
      kind, samples: Number($("vSamples").value) || 10, inputs,
      settings: { ...s, start_ms: utcMs(s.start), end_ms: utcMs(s.end), forward_ms: forwardMs() },
    };
  }

  async function start(kind) {
    if (current) {   // al doilea click = Stop
      await fetch(`/api/tester/${current.id}/stop`, { method: "POST" }).catch(() => {});
      return;
    }
    let body;
    try { body = request(kind); } catch (e) { toast(e.message, "err"); return; }
    save();
    const r = await fetch("/api/tester/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).catch((e) => ({ ok: false, statusText: e.message, json: async () => ({}) }));
    const job = await r.json().catch(() => ({}));
    if (!r.ok) { toast(job.detail || r.statusText, "err"); return; }
    if (kind === "verify") $("vOut").innerHTML = `<p class="hint">Rulez funcțiile originale...</p>`;
    follow(job);
  }
  $("tStart").onclick = () => start("run");
  $("vGo").onclick = () => start("verify");

  const PLAY = '<svg viewBox="0 0 16 16"><path d="M5 3l8 5-8 5z"/></svg><span>Start</span>';
  const STOP = '<svg viewBox="0 0 16 16"><path d="M4 4h8v8H4z"/></svg><span>Stop</span>';
  const PHASES = { start: "Pornesc", data: "Descarc datele", signals: "Calculez semnalele", run: "Simulez", verify: "Verific" };

  async function follow(job) {
    current = job;
    const btn = $("tStart");
    btn.innerHTML = STOP; btn.classList.add("stop");
    $("vGo").disabled = true;
    $("runInfo").hidden = false;
    try {
      for (;;) {
        const pct = Math.round((job.progress || 0) * 100);
        const phase = PHASES[job.phase] || "Pornesc";
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
        else {
          full.result.elapsed = job.elapsed;
          render(full.result);
          // Sărim la grafic doar din Setări / Parametri (nu și dacă ai deschis singur alt tab între timp).
          if (["settings", "inputs"].includes(document.querySelector(".tab.active")?.dataset.tab)) showTab("graph");
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
      btn.innerHTML = PLAY; btn.classList.remove("stop");
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
    $("dealsBadge").textContent = res.deals.length;
    $("dCsv").disabled = !res.deals.length;
    renderDeals();
    renderJournal(res.journal);
    drawGraph();
  }

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
  window.addEventListener("resize", () => requestAnimationFrame(drawGraph));

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
