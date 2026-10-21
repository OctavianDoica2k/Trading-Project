// Meniul de indicatori („ƒx Indicatori”, stânga sus pe chart).
// =====================================================================================
//  CE FACE ACEST FIȘIER (pe scurt)
// -------------------------------------------------------------------------------------
//  - Ține lista indicatorilor disponibili. Fiecare indicator se înregistrează singur, din
//    fișierul lui, cu registerIndicator({ id, name, desc, icon, attach, detach, reload,
//    onResult, onNewBar }). Exemple: trend.js („Linii de trend”), sma.js („Medie mobilă”),
//    mtf.js („Trend multi-timeframe”, care merge pe orice timeframe: `anyTf: true`).
//  - Indicatorii se calculează pe server, din datastore-ul local (doar lumânări închise, de la
//    M1 în sus): un indicator își cere rezultatul cu sendWS({type: "indicator", id, params}),
//    iar rezultatele (și cele recalculate la fiecare lumânare închisă) ajung în onResult.
//    reload() = istoric nou (alt simbol / timeframe / reconectare); onNewBar() = a început o
//    lumânare nouă pe chart.
//  - Fereastra „Indicatori”: căutare, câte un cartonaș pentru fiecare indicator, butonul
//    Atașează / Detașează. Dublu-click pe cartonaș = același lucru.
//  - Indicatorii atașați se păstrează în browser (localStorage), deci rămân pe chart și
//    după reîncărcarea paginii sau schimbarea simbolului / timeframe-ului. După o repornire a
//    serverului, prima deschidere a chartului începe fără indicatori.
//  - Un indicator cu `multi: true` (media mobilă) se poate adăuga de mai multe ori, fiecare
//    copie cu parametrii ei; copiile se scot de pe chart din ✕-ul controlului lor.
//  - Controalele unui indicator atașat (ex. sliderul Trend) stau în #indWidgets, în dreapta
//    sus pe chart; le creează fișierul indicatorului.
//
//  Folosește variabilele din app.js (state, $, store).
// =====================================================================================

const INDICATORS = [];   // tipurile de indicatori, în ordinea din meniu
// Indicatorii de pe chart: id -> obiectul lui (attach, detach, reload, onResult, onNewBar).
// Un indicator obișnuit e pe chart o singură dată, cu id-ul lui („trend”). Unul cu `multi`
// (ex. media mobilă) poate fi adăugat de mai multe ori: fiecare copie are id-ul ei („sma#1”,
// „sma#2”) și e creată de create(id, isNew) din fișierul indicatorului.
const activeInd = new Map();
// Tipul unui id: „sma#2” -> „sma”, „trend” -> „trend”.
const typeOf = (id) => id.split("#")[0];
// Indicatorii salvați în browser (listă JSON de id-uri). Se pun înapoi pe chart la primul istoric,
// doar dacă serverul nu a fost repornit între timp (vezi onHistoryLoaded).
const savedInd = (() => {
  try { return JSON.parse(store.get("indicators", "[]")); } catch { return []; }
})();
let indRestored = false;   // indicatorii salvați au fost deja puși înapoi (o singură dată)
const indPanel = $("indPanel");
const indList = $("indList");
const indSearch = $("indSearch");
const indAttach = $("indAttach");
let indSelected = null;   // tipul de indicator selectat în fereastră (cartonașul evidențiat)

// Funcții mici: salvarea listei de pe chart în browser; câte copii are un tip pe chart; e un tip
// pe chart?; se poate adăuga de mai multe ori?
const saveAttached = () => store.set("indicators", JSON.stringify([...activeInd.keys()]));
const countAttached = (type) => [...activeInd.keys()].filter((id) => typeOf(id) === type).length;
const isAttached = (type) => countAttached(type) > 0;
const isMulti = (type) => !!INDICATORS.find((x) => x.id === type)?.multi;

// Pornește un indicator pe chart: pentru unul `multi` creează întâi copia cu id-ul dat.
function startIndicator(ind, id, isNew) {
  const inst = ind.multi ? ind.create(id, isNew) : ind;
  activeInd.set(id, inst);
  inst.attach();
}

// Pune pe chart indicatorul de tipul `type`; unul `multi` se adaugă încă o dată, ca o copie nouă.
function attachIndicator(type) {
  const ind = INDICATORS.find((x) => x.id === type);
  if (!ind) return;
  let id = type;
  if (ind.multi) {
    // Primul număr liber: dacă există sma#1 și sma#2, copia nouă e sma#3.
    let n = 1;
    while (activeInd.has(`${type}#${n}`)) n++;
    id = `${type}#${n}`;
  } else if (activeInd.has(id)) return;
  startIndicator(ind, id, true);
  saveAttached();
  renderIndicators();
}

// Scoate de pe chart un indicator (id-ul lui sau al copiei, ex. „sma#2”).
function detachIndicator(id) {
  const inst = activeInd.get(id);
  if (!inst) return;
  activeInd.delete(id);
  saveAttached();
  inst.detach();
  renderIndicators();
}

// Fiecare indicator se înregistrează o singură dată, la încărcarea fișierului lui.
function registerIndicator(ind) {
  INDICATORS.push(ind);
  if (!indSelected) indSelected = ind.id;
  renderIndicators();
}

// Redesenează fereastra „Indicatori”: cartonașele (filtrate după căutare), butonul de jos și nota.
function renderIndicators() {
  const q = indSearch.value.trim().toLowerCase();
  indList.replaceChildren();
  // Indicatorii al căror nume sau descriere conține textul căutat.
  const shown = INDICATORS.filter((x) => !q || `${x.name} ${x.desc}`.toLowerCase().includes(q));
  // Selecția a ieșit din rezultate: selectăm primul rezultat.
  if (!shown.some((x) => x.id === indSelected)) indSelected = shown[0]?.id ?? null;
  for (const ind of shown) {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "indcard" + (ind.id === indSelected ? " sel" : "");
    card.innerHTML = '<span class="indicon"></span><span class="indtext"><b></b><small></small></span>';
    if (ind.iconHTML) card.querySelector(".indicon").innerHTML = ind.iconHTML;
    else card.querySelector(".indicon").textContent = ind.icon;
    card.querySelector("b").textContent = ind.name;
    card.querySelector("small").textContent = ind.desc;
    if (isAttached(ind.id)) {
      const tag = document.createElement("span");
      tag.className = "indtag";
      tag.textContent = ind.multi ? `${countAttached(ind.id)} pe chart` : "atașat";
      card.querySelector("b").append(" ", tag);
    }
    card.onclick = () => { indSelected = ind.id; renderIndicators(); };
    card.ondblclick = () => (isAttached(ind.id) && !ind.multi ? detachIndicator(ind.id) : attachIndicator(ind.id));
    indList.appendChild(card);
  }
  if (!q) {
    // Locul rezervat pentru indicatorii care vor fi adăugați mai târziu.
    const more = document.createElement("div");
    more.className = "indcard soon";
    more.innerHTML = '<span class="indicon">+</span><span class="indtext"><b>Alți indicatori</b><small>în curând, cu același mecanism</small></span>';
    indList.appendChild(more);
  }
  // Butonul de jos: Atașează / Adaugă încă una / Detașează, după indicatorul selectat.
  const sel = INDICATORS.find((x) => x.id === indSelected);
  indAttach.disabled = !sel;
  const remove = !!sel && !sel.multi && isAttached(sel.id);
  indAttach.textContent = !sel ? "Niciun indicator găsit"
    : remove ? `Detașează de pe ${state.symbol}`
    : sel.multi && isAttached(sel.id) ? `Adaugă încă una pe ${state.symbol}` : `Atașează pe ${state.symbol}`;
  indAttach.classList.toggle("danger", remove);
  $("indNote").textContent = state.tf < 60 && !sel?.anyTf
    ? "Pe 1s–30s indicatorii nu rulează: aceste lumânări nu se salvează în datastore. Alege M1 sau un timeframe mai mare."
    : sel?.note || "";
}

// Evenimente de la app.js, transmise indicatorilor atașați.
// La primul istoric primit, punem înapoi indicatorii salvați, dar numai dacă serverul e același
// (msg.boot se schimbă la fiecare pornire a serverului): prima deschidere a chartului după
// pornirea serverului începe fără indicatori.
function restoreIndicators(boot) {
  indRestored = true;
  const sameServer = !!boot && store.get("indicators.boot", null) === boot;
  store.set("indicators.boot", boot ?? "");
  const started = new Set();
  if (sameServer) {
    for (const id of savedInd) {
      const ind = typeof id === "string" && INDICATORS.find((x) => x.id === typeOf(id));
      if (!ind || activeInd.has(id) || (!ind.multi && id !== ind.id)) continue;
      startIndicator(ind, id, false);
      started.add(id);
    }
  }
  saveAttached();
  return started;   // aceștia și-au cerut deja rezultatul în attach()
}

// Istoric nou primit (app.js): la prima dată punem înapoi indicatorii salvați; restul își
// cer din nou rezultatul pentru noul istoric.
window.onHistoryLoaded = (msg) => {
  const started = indRestored ? new Set() : restoreIndicators(msg?.boot);
  renderIndicators();
  for (const [id, inst] of activeInd) if (!started.has(id)) inst.reload?.();
};
// O lumânare nouă pe chart: o anunțăm tuturor indicatorilor atașați.
window.onNewBar = () => { for (const inst of activeInd.values()) inst.onNewBar?.(); };
// Un rezultat de la server: îl primește doar indicatorul cu id-ul din mesaj.
window.onIndicator = (msg) => activeInd.get(msg.id)?.onResult?.(msg);

// Deschide fereastra cu căutarea goală.
function openIndicators() {
  indSearch.value = "";
  renderIndicators();
  indPanel.hidden = false;
}
const closeIndicators = () => { indPanel.hidden = true; };

$("indBtn").onclick = () => (indPanel.hidden ? openIndicators() : closeIndicators());
$("indClose").onclick = closeIndicators;
indSearch.addEventListener("input", renderIndicators);
// Butonul de jos: detașează un indicator simplu deja atașat; altfel îl atașează și închide fereastra.
indAttach.onclick = () => {
  if (!indSelected) return;
  if (isAttached(indSelected) && !isMulti(indSelected)) detachIndicator(indSelected);
  else { attachIndicator(indSelected); closeIndicators(); }
};
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !indPanel.hidden) closeIndicators(); });
// Celelalte ferestre (alarme, export) închid meniul, ca să nu se suprapună.
$("alertBtn").addEventListener("click", closeIndicators);
$("exportBtn").addEventListener("click", closeIndicators);
