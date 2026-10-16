// Meniul de indicatori („ƒx Indicatori”, stânga sus pe chart).
// =====================================================================================
//  CE FACE ACEST FIȘIER (pe scurt)
// -------------------------------------------------------------------------------------
//  - Ține lista indicatorilor disponibili. Fiecare indicator se înregistrează singur, din
//    fișierul lui, cu registerIndicator({ id, name, desc, icon, attach, detach }).
//    Exemplu: trend.js înregistrează indicatorul „Linii de trend”.
//  - Fereastra „Indicatori”: căutare, câte un cartonaș pentru fiecare indicator, butonul
//    Atașează / Detașează. Dublu-click pe cartonaș = același lucru.
//  - Indicatorii atașați se păstrează în browser (localStorage), deci rămân pe chart și
//    după reîncărcarea paginii sau schimbarea simbolului / timeframe-ului.
//  - Controalele unui indicator atașat (ex. sliderul Trend) stau în #indWidgets, în dreapta
//    sus pe chart; le creează fișierul indicatorului.
//
//  Folosește variabilele din app.js (state, $, store).
// =====================================================================================

const INDICATORS = [];
// Id-urile indicatorilor atașați (salvate ca listă JSON în localStorage).
const attachedInd = new Set((() => {
  try { return JSON.parse(store.get("indicators", "[]")); } catch { return []; }
})());
const indPanel = $("indPanel");
const indList = $("indList");
const indSearch = $("indSearch");
const indAttach = $("indAttach");
let indSelected = null;

const saveAttached = () => store.set("indicators", JSON.stringify([...attachedInd]));
const isAttached = (id) => attachedInd.has(id);

function attachIndicator(id) {
  const ind = INDICATORS.find((x) => x.id === id);
  if (!ind || attachedInd.has(id)) return;
  attachedInd.add(id);
  saveAttached();
  ind.attach();
  renderIndicators();
}

function detachIndicator(id) {
  const ind = INDICATORS.find((x) => x.id === id);
  if (!ind || !attachedInd.has(id)) return;
  attachedInd.delete(id);
  saveAttached();
  ind.detach();
  renderIndicators();
}

// Fiecare indicator se înregistrează o singură dată, la încărcarea fișierului lui. Dacă era
// atașat data trecută, îl reatașăm imediat (își desenează datele când sosește istoricul).
function registerIndicator(ind) {
  INDICATORS.push(ind);
  if (!indSelected) indSelected = ind.id;
  if (attachedInd.has(ind.id)) ind.attach();
  renderIndicators();
}

function renderIndicators() {
  const q = indSearch.value.trim().toLowerCase();
  indList.replaceChildren();
  const shown = INDICATORS.filter((x) => !q || `${x.name} ${x.desc}`.toLowerCase().includes(q));
  if (!shown.some((x) => x.id === indSelected)) indSelected = shown[0]?.id ?? null;
  for (const ind of shown) {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "indcard" + (ind.id === indSelected ? " sel" : "");
    card.innerHTML = '<span class="indicon"></span><span class="indtext"><b></b><small></small></span>';
    card.querySelector(".indicon").textContent = ind.icon;
    card.querySelector("b").textContent = ind.name;
    card.querySelector("small").textContent = ind.desc;
    if (isAttached(ind.id)) {
      const tag = document.createElement("span");
      tag.className = "indtag";
      tag.textContent = "atașat";
      card.querySelector("b").append(" ", tag);
    }
    card.onclick = () => { indSelected = ind.id; renderIndicators(); };
    card.ondblclick = () => (isAttached(ind.id) ? detachIndicator(ind.id) : attachIndicator(ind.id));
    indList.appendChild(card);
  }
  if (!q) {
    // Locul rezervat pentru indicatorii care vor fi adăugați mai târziu.
    const more = document.createElement("div");
    more.className = "indcard soon";
    more.innerHTML = '<span class="indicon">+</span><span class="indtext"><b>Alți indicatori</b><small>în curând, cu același mecanism</small></span>';
    indList.appendChild(more);
  }
  const sel = INDICATORS.find((x) => x.id === indSelected);
  indAttach.disabled = !sel;
  indAttach.textContent = !sel ? "Niciun indicator găsit"
    : isAttached(sel.id) ? `Detașează de pe ${state.symbol}` : `Atașează pe ${state.symbol}`;
  indAttach.classList.toggle("danger", !!sel && isAttached(sel.id));
  $("indNote").textContent = sel?.note || "";
}

function openIndicators() {
  indSearch.value = "";
  renderIndicators();
  indPanel.hidden = false;
}
const closeIndicators = () => { indPanel.hidden = true; };

$("indBtn").onclick = () => (indPanel.hidden ? openIndicators() : closeIndicators());
$("indClose").onclick = closeIndicators;
indSearch.addEventListener("input", renderIndicators);
indAttach.onclick = () => {
  if (!indSelected) return;
  if (isAttached(indSelected)) detachIndicator(indSelected);
  else { attachIndicator(indSelected); closeIndicators(); }
};
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !indPanel.hidden) closeIndicators(); });
// Celelalte ferestre (alarme, export) închid meniul, ca să nu se suprapună.
$("alertBtn").addEventListener("click", closeIndicators);
$("exportBtn").addEventListener("click", closeIndicators);
