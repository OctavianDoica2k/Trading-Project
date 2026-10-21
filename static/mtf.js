// Indicatorul „Trend multi-timeframe”: trendul pe o zi, o săptămână, o lună și un an (1D · 1W · 1M · 1Y).
// =====================================================================================
//  CE FACE ACEST FIȘIER (pe scurt)
// -------------------------------------------------------------------------------------
//  - Se înregistrează în meniul de indicatori (indicators.js). Când e atașat, cere serverului
//    indicatorul „mtf” pentru simbolul de pe chart (pe orice timeframe). Serverul (server.py,
//    MtfWatch):
//      1. caută datastore-ul D1 al simbolului: datastore/<sursă>/<SIMBOL>/D1/<SIMBOL>_D1.csv;
//      2. îl aduce la zi după regulile datastore-ului: dacă nu există îl creează (ultimele 10.000
//         de zile închise), dacă e învechit adaugă zilele lipsă (datastore.py);
//      3. rulează analysis.py neschimbat pe zilele închise ale anului curent și trimite cele
//         patru valori.
//    Pașii apar sub casete cât timp rulează. Serverul repetă totul la închiderea fiecărei zile
//    și trimite singur rezultatul nou; la schimbarea simbolului pagina îl cere din nou.
//  - Prețul live nu intră niciodată în calcul: casetele arată valorile din datastore (doar zile
//    închise), iar jos, în card, scrie până la ce zi sunt datele și când s-a verificat datastore-ul.
//  - Cardul are sus numele, marcajul de aliniere, pragul și butoanele ↻ / ✕ (ca ceilalți indicatori),
//    dedesubt cele patru casete. Culoarea casetei urmează procentul: verde spre +100%, roșu spre
//    −100%, stinsă spre 0; bara de sub procent pornește din mijloc (0%) spre stânga (scădere) sau
//    spre dreapta (creștere). Cu mouse-ul peste o casetă (sau cu o atingere, pe telefon) apar
//    detaliile calculului.
//  - Marcajul de aliniere: „▲ 4/4 aliniate” / „▼ 4/4 aliniate” când toate patru au aceeași culoare
//    (și, dacă e ales un prag, fiecare e cel puțin la pragul ales), altfel „Mixt ▲2 ▼2”. Cu un prag
//    ales, barele arată pragul, iar casetele sub prag se estompează. Este o informație, nu o
//    recomandare de tranzacționare.
//  - O valoare pe care analysis.py nu o poate calcula (ex. IndexError în primele zile ale lunii,
//    sau maximul egal cu minimul) apare „—”, cu motivul în detalii.
//
//  Folosește variabilele din app.js (state, $, store, sendWS), toast() din alerts.js și
//  registerIndicator() / detachIndicator() din indicators.js.
// =====================================================================================

// Cele patru perioade: [cheia din rezultatul serverului, eticheta casetei].
const MTF_KEYS = [["day", "1D"], ["week", "1W"], ["month", "1M"], ["year", "1Y"]];
const MTF_PRAG_KEY = "mtf.prag";          // cheia din localStorage pentru pragul ales
const MTF_PRAGS = [0, 10, 20, 30, 50];    // pragurile din listă (0 = fără prag)

let mtfWidget = null;   // există doar cât e atașat indicatorul
let mtfLast = null;     // ultimul rezultat primit (pentru simbolul de pe chart)
let mtfTipKey = null;   // caseta ale cărei detalii sunt afișate

// Formatări: procent cu semn (+12.34% / −5.00%), data „AAAA-LL-ZZ...” ca „ZZ.LL.AAAA”,
// și textul sigur pentru HTML (caracterele speciale înlocuite).
const mtfPct = (v) => (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(2) + "%";
const mtfDay = (s) => { const [y, m, d] = s.slice(0, 10).split("-"); return `${d}.${m}.${y}`; };
const mtfEsc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

// Clasa culorii: pos = creștere (verde), neg = scădere (roșu), nimic la 0%.
const mtfSign = (v) => (v > 0 ? "pos" : v < 0 ? "neg" : "");

// Construiește cardul indicatorului (antet, cele patru casete, rândul de stare, caseta de detalii),
// leagă butoanele și îl pune în colțul din dreapta sus al chartului.
function buildMtfWidget() {
  const w = document.createElement("div");
  w.className = "mtfw";
  w.innerHTML = `
    <div class="mtfhead">
      <span class="indname" title="Trend multi-timeframe: analysis.py pe datastore-ul D1">Trend MTF</span>
      <span class="mtfpill" hidden></span>
      <select class="mtfprag" title="Pragul alinierii: toate patru de aceeași culoare și fiecare cel puțin la prag" aria-label="Pragul alinierii">
        ${MTF_PRAGS.map((p) => `<option value="${p}">${p ? `prag ≥ ${p}%` : "fără prag"}</option>`).join("")}
      </select>
      <button type="button" class="iconbtn mtfrefresh" title="Verifică acum datastore-ul D1 și recalculează">↻</button>
      <button type="button" class="iconbtn mtfremove" title="Detașează indicatorul">✕</button>
    </div>
    <div class="mtfboxes">${MTF_KEYS.map(([k, label]) =>
      `<button type="button" class="mtfbox" data-k="${k}"><span class="k">${label}</span><span class="v">—</span>` +
      `<span class="mtfbar"><i></i></span></button>`).join("")}</div>
    <div class="mtfstat"><span class="mtftext"></span></div>
    <div class="mtftip" hidden></div>`;
  // Pragul salvat în browser (doar dacă e unul din listă, altfel 0).
  const prag = w.querySelector(".mtfprag");
  prag.value = String(MTF_PRAGS.includes(Number(store.get(MTF_PRAG_KEY, 0))) ? Number(store.get(MTF_PRAG_KEY, 0)) : 0);
  prag.addEventListener("change", () => { store.set(MTF_PRAG_KEY, prag.value); renderMtfPill(); });
  w.querySelector(".mtfrefresh").onclick = () => requestMtf();
  w.querySelector(".mtfremove").onclick = () => detachIndicator("mtf");
  // Detaliile unei casete: la trecerea mouse-ului (desktop) sau la atingere (telefon).
  for (const box of w.querySelectorAll(".mtfbox")) {
    let touch = false;   // ultima apăsare a fost cu degetul / creionul, nu cu mouse-ul
    box.addEventListener("pointerdown", (e) => { touch = e.pointerType !== "mouse"; });
    box.addEventListener("pointerenter", (e) => { if (e.pointerType === "mouse") showMtfTip(box.dataset.k); });
    box.addEventListener("pointerleave", (e) => { if (e.pointerType === "mouse") showMtfTip(null); });
    // Pe telefon nu există „mouse peste”: o atingere arată / ascunde detaliile.
    box.addEventListener("click", () => { if (touch) showMtfTip(mtfTipKey === box.dataset.k ? null : box.dataset.k); });
  }
  $("indWidgets").appendChild(w);
  return w;
}

// Actualizează cele patru casete din ultimul rezultat: procentul, culoarea și bara.
// loading = true: încă se calculează, deci casetele arată un text de umplutură, fără culoare.
function renderMtfBoxes(loading) {
  if (!mtfWidget) return;
  const res = mtfLast?.result;
  for (const box of mtfWidget.querySelectorAll(".mtfbox")) {
    const v = res?.values?.[box.dataset.k];
    const ok = !loading && v != null;
    const k = ok ? Math.min(1, Math.abs(v) / 100) : 0;   // 0 la 0%, 1 la ±100%
    box.className = "mtfbox " + (ok ? mtfSign(v) : "empty");
    box.classList.toggle("on", box.dataset.k === mtfTipKey);
    // --mix = cât de colorată e caseta, --w = lungimea barei (jumătate din lățime la ±100%).
    box.style.setProperty("--mix", `${Math.round(5 + k * 23)}%`);
    box.style.setProperty("--w", `${k * 50}%`);
    box.querySelector(".v").textContent = loading ? "+00.00%" : ok ? mtfPct(v) : "—";
    const label = MTF_KEYS.find(([key]) => key === box.dataset.k)[1];
    box.setAttribute("aria-label", `${label}: ${loading ? "se calculează" : ok ? mtfPct(v) : "nu s-a putut calcula"}`);
  }
  renderMtfPill();
}

// Marcajul de aliniere: câte din cele patru au aceeași culoare (cu pragul ales). Pragul apare și pe
// bare (cele două linii verticale), iar casetele sub prag se estompează.
function renderMtfPill() {
  if (!mtfWidget) return;
  const pill = mtfWidget.querySelector(".mtfpill");
  const prag = Number(mtfWidget.querySelector(".mtfprag").value);
  mtfWidget.classList.toggle("prag", prag > 0);
  // --t = poziția liniilor de prag pe bară (bara are jumătate de lățime pentru 100%).
  mtfWidget.style.setProperty("--t", `${prag / 2}%`);
  // Cele patru valori, în ordinea MTF_KEYS (sau null dacă nu avem încă rezultat).
  const vals = mtfLast?.result ? MTF_KEYS.map(([k]) => mtfLast.result.values[k]) : null;
  for (const box of mtfWidget.querySelectorAll(".mtfbox")) {
    const v = vals?.[MTF_KEYS.findIndex(([k]) => k === box.dataset.k)];
    box.classList.toggle("weak", prag > 0 && v != null && Math.abs(v) < prag);
  }
  if (!vals || mtfWidget.classList.contains("loading")) { pill.hidden = true; return; }
  // Câte perioade sunt în creștere / în scădere și cel puțin la prag.
  const pos = vals.filter((v) => v != null && v > 0 && v >= prag).length;
  const neg = vals.filter((v) => v != null && v < 0 && -v >= prag).length;
  pill.hidden = false;
  if (pos === 4 || neg === 4) {
    pill.className = "mtfpill " + (pos === 4 ? "pos" : "neg");
    pill.textContent = pos === 4 ? "▲ 4/4 aliniate" : "▼ 4/4 aliniate";
  } else {
    pill.className = "mtfpill mix";
    pill.innerHTML = `Mixt <span class="pos">▲${pos}</span><span class="neg">▼${neg}</span>`;
  }
  pill.title = (pos === 4 ? "Toate patru perioadele sunt în creștere" : neg === 4 ? "Toate patru perioadele sunt în scădere"
    : `Perioadele nu au toate aceeași culoare: ${pos} în creștere, ${neg} în scădere`) +
    (prag ? ` (fiecare cel puțin ${prag}%)` : "") + ". Este o informație, nu o recomandare de tranzacționare.";
}

// Rândul de jos al cardului; cls: "" = date la zi (punct verde), "load" = se pregătesc, "err" = eroare.
function setMtfText(html, cls = "") {
  if (!mtfWidget) return;
  mtfWidget.querySelector(".mtfstat").className = "mtfstat " + cls;
  mtfWidget.querySelector(".mtftext").innerHTML = html;
}

// Detaliile calculului pentru o casetă, luate din fișierul pe care a rulat analysis.py.
function showMtfTip(key) {
  if (!mtfWidget) return;
  mtfTipKey = key;
  for (const box of mtfWidget.querySelectorAll(".mtfbox")) box.classList.toggle("on", box.dataset.k === key);
  const tip = mtfWidget.querySelector(".mtftip");
  const res = mtfLast?.result;
  if (!key || !res) { tip.hidden = true; return; }
  const label = MTF_KEYS.find(([k]) => k === key)[1];
  // p = detaliile perioadei (start, end, open, close, high, low), v = procentul, dg = zecimalele.
  const p = res.details?.[key], v = res.values[key], dg = res.digits;
  const fx = (x) => Number(x).toFixed(dg);
  const row = (a, b) => `<div class="r"><span>${a}</span><span>${b}</span></div>`;
  let html = `<div class="h"><b>${label}</b><span>` +
    (p ? (p.n > 1 ? `${mtfDay(p.start)} → ${mtfDay(p.end)} · ${p.n} zile` : mtfDay(p.start)) : "—") + "</span>" +
    (v != null ? `<b class="${mtfSign(v)}">${mtfPct(v)}</b>` : "") + "</div>";
  if (p) {
    html += row("open (prima zi)", fx(p.open)) + row("close (ultima zi)", fx(p.close)) +
      row("high (maxim)", fx(p.high)) + row("low (minim)", fx(p.low));
  }
  // Formula cu numerele reale, ca să se poată verifica de mână; altfel motivul erorii.
  if (v != null && p) {
    html += `<div class="f">(${fx(p.close)} − ${fx(p.open)}) ÷ (${fx(p.high)} − ${fx(p.low)}) × 100 = ` +
      `<b class="${mtfSign(v)}">${mtfPct(v)}</b></div>`;
  } else {
    html += `<div class="f err">— ${mtfEsc(res.errors?.[key] || "nu s-a putut calcula")}</div>`;
  }
  const notes = [];
  if (key === "year") notes.push("1Y = de la 1 ianuarie (anul în curs), nu ultimele 12 luni.");
  if (key === "week" && p && (p.high !== p.own_high || p.low !== p.own_low)) {
    notes.push(`Maximul și minimul sunt ale lunii (analysis.py folosește lastmonth). ` +
      `Ale săptămânii: ${fx(p.own_high)} / ${fx(p.own_low)}.`);
  }
  if (key === "day" && p) notes.push("1D = ultima zi închisă din datastore (ziua în curs nu se salvează).");
  for (const n of notes) html += `<div class="n">${mtfEsc(n)}</div>`;
  html += `<div class="n">sursa: ${mtfEsc(res.file)}</div>`;
  tip.innerHTML = html;
  tip.hidden = false;
}

// Cere serverului pregătirea bazei de date și calculul, pentru simbolul de pe chart.
function requestMtf() {
  if (!mtfWidget || !state.loaded) return;
  if (sendWS({ type: "indicator", id: "mtf", params: {} })) {
    mtfWidget.classList.add("busy");
    // Rezultatul vechi e pentru alt simbol: îl ștergem, nu-l lăsăm pe ecran.
    if (mtfLast?.symbol !== state.symbol) startMtfLoading();
  }
}

// Golește cardul și arată starea „se pregătește”, până sosește primul rezultat.
function startMtfLoading() {
  mtfLast = null;
  mtfWidget.classList.add("loading");
  showMtfTip(null);
  renderMtfBoxes(true);
  setMtfText("Pregătesc datastore-ul D1…", "load");
}

// Mesajele serverului pentru indicator: progres (pașii 1–3), eroare sau rezultatul final.
function onMtfResult(msg) {
  const w = mtfWidget;
  if (!w || msg.symbol !== state.symbol) return;   // mesaj pentru alt simbol: îl ignorăm
  if (msg.progress) {
    w.classList.add("busy");
    if (w.classList.contains("loading")) {
      setMtfText(`Pasul ${msg.progress.step}/3 · ${mtfEsc(msg.progress.text)}`, "load");
    }
    return;
  }
  w.classList.remove("busy", "loading");
  if (msg.error) {
    if (!mtfLast) renderMtfBoxes(false);
    setMtfText(`Eroare: ${mtfEsc(msg.error)}`, "err");
    return;
  }
  mtfLast = msg;
  const r = msg.result, db = r.db;
  renderMtfBoxes(false);
  const upd = new Date(r.updated);
  const hm = `${String(upd.getHours()).padStart(2, "0")}:${String(upd.getMinutes()).padStart(2, "0")}`;
  setMtfText(`D1 închis până la <b>${mtfDay(db.last)}</b> · verificat ${hm}`);
  // Explicația completă despre baza de date, afișată la trecerea mouse-ului peste rândul de jos.
  const lines = [
    db.created ? `Datastore-ul D1 nu exista: creat acum (${db.total} zile închise de la ${r.source}).`
      : `Datastore-ul D1 există (${db.total} zile${db.complete ? ", tot istoricul sursei" : ""}).`,
    !db.created ? (db.added ? `Era învechit: ${db.added} zile adăugate până la zi.` : "Era la zi: nicio zi lipsă.") : "",
    `Fișier: ${r.file}.`,
    `analysis.py primește zilele din ${db.first.slice(0, 4)}: ${db.days} zile, ${mtfDay(db.first)} → ${mtfDay(db.last)} ` +
      "(cu data zilei de tranzacționare; fișierul păstrează ora UTC).",
    "Doar zile închise, nu prețul live. Se verifică din nou la închiderea fiecărei zile.",
  ];
  w.querySelector(".mtftext").title = [`${msg.symbol} · datastore D1 · ${db.days} zile din ${db.first.slice(0, 4)}.`, ...lines]
    .filter(Boolean).join("\n");
  if (mtfTipKey) showMtfTip(mtfTipKey);
  if (db.created) toast("Trend multi-timeframe", `Datastore-ul D1 pentru ${msg.symbol} a fost creat: ${r.file}`);
}

// Înregistrarea în meniul de indicatori (vezi indicators.js pentru ce înseamnă fiecare câmp).
registerIndicator({
  id: "mtf",
  name: "Trend multi-timeframe",
  iconHTML: '<span class="mtfico"><i class="neg"></i><i class="pos"></i><i class="pos"></i><i class="pos weak"></i></span>',
  icon: "▦",
  anyTf: true,
  desc: "1D · 1W · 1M · 1Y — rulează analysis.py pe datastore-ul D1",
  note: "Sursa datelor: datastore-ul D1 al simbolului (zile închise), nu prețul live. Dacă nu există, se creează " +
    "după regulile datastore-ului; dacă e învechit, se aduce la zi, apoi analysis.py rulează pe zilele anului curent. " +
    "Merge pe orice timeframe al chartului.",
  attach() {
    if (mtfWidget) return;
    mtfWidget = buildMtfWidget();
    startMtfLoading();
    requestMtf();
  },
  detach() {
    sendWS({ type: "indicator_off", id: "mtf" });
    mtfWidget?.remove();
    mtfWidget = null;
    mtfLast = null;
    mtfTipKey = null;
  },
  // Istoric nou (alt simbol / timeframe / reconectare): pagina are o conexiune nouă, deci cerem din nou.
  reload() { requestMtf(); },
  onResult: onMtfResult,
});
