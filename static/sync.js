// Recuperarea ticurilor lipsă de pe VM (colectorul), pe chart lângă indicatorul datastore-ului.
// =====================================================================================
//  CE FACE ACEST FIȘIER (pe scurt)
// -------------------------------------------------------------------------------------
//  - Starea vine de la server (în hello și în mesajele "sync"), citită din QuestDB:
//      sync  : colectorul descarcă golul de ticuri (VM-ul a fost oprit / fără conexiune);
//              bara arată cât la sută s-a recuperat, de când lipsesc ticuri și cât mai durează;
//      error : QuestDB nu aplică ce scrie colectorul (ex. WAL suspendat după o oprire bruscă);
//      stale : colectorul nu mai raportează (oprit sau blocat);
//      live  : totul e la zi (eticheta dispare).
//  - Când recuperarea se termină, chartul își reîncarcă istoricul, ca golul umplut să apară.
//
//  Folosește variabilele din app.js ($, state, connect, setStatus, MONTHS, pad).
// =====================================================================================

const syncBadge = $("syncBadge");
const syncText = syncBadge.querySelector(".sbtext");
const syncBar = syncBadge.querySelector(".progress div");
let syncPrev = null;

const fmtWhen = (ms) => {
  const d = new Date(ms);
  return `${pad(d.getDate())} ${MONTHS[d.getMonth()]} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
};
const fmtEta = (s) => (s >= 3600 ? `~${Math.round(s / 360) / 10} h` : s >= 60 ? `~${Math.round(s / 60)} min` : `~${s} s`);

window.onSyncStatus = (s) => {
  if (!s) return;
  const prev = syncPrev;
  syncPrev = s.state;
  // Golul s-a umplut (sau QuestDB și-a revenit): reîncărcăm istoricul chartului.
  if (state.loaded && s.state === "live" && ["sync", "error", "stale"].includes(prev)) {
    connect();
    return;
  }
  let text = "", title = "", bar = null;
  if (s.state === "sync") {
    const pct = Math.floor((s.progress || 0) * 100);
    text = `⟳ Recuperez ticurile lipsă: ${pct}%`;
    if (s.gap_from) text += ` · de la ${fmtWhen(s.gap_from)}`;
    if (s.eta) text += ` · ${fmtEta(s.eta)}`;
    bar = s.progress || 0;
    title = "VM-ul a fost oprit sau fără conexiune: colectorul descarcă din istoricul brokerului " +
      "toate ticurile lipsă, până în prezent. Chartul se reîncarcă singur la 100%.";
    if (state.loaded) setStatus(true, `live · recuperare ${pct}%`);
  } else if (s.state === "error") {
    text = "QuestDB nu salvează ticurile noi";
    title = s.detail || "";
    if (state.loaded) setStatus(false, "QuestDB nu salvează ticurile");
  } else if (s.state === "stale") {
    text = `Colectorul de pe VM nu mai raportează${s.since ? ` (din ${fmtWhen(s.since)})` : ""}`;
    title = "Colectorul e oprit sau blocat: ticurile noi nu se mai salvează. La repornire, golul se recuperează automat.";
  }
  syncBadge.hidden = !text;
  syncBadge.className = `storebadge ${s.state === "sync" ? "syncing" : "error"}`;
  syncText.textContent = text;
  syncBadge.title = title;
  syncBar.parentElement.hidden = bar === null;
  if (bar !== null) syncBar.style.width = `${Math.max(0, Math.min(1, bar)) * 100}%`;
};
