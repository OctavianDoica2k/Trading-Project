// Indicatorul datastore-ului local, pe chart (stânga sus, lângă „ƒx Indicatori”).
// =====================================================================================
//  CE FACE ACEST FIȘIER (pe scurt)
// -------------------------------------------------------------------------------------
//  - Arată cât din baza de date locală a simbolului / timeframe-ului deschis e descărcată:
//    100% = ultimele 10.000 de lumânări închise sunt pe disc (sau tot istoricul sursei, dacă are
//    mai puține). Indicatorii rulează doar pe aceste date.
//  - În timpul descărcării: bara de progres, live (mesajele "store" de la server). La prima
//    deschidere a unui timeframe, chartul apare după ce descărcarea se termină.
//  - După aceea: câte lumânări are fișierul; numărul crește la fiecare lumânare închisă
//    (lumânările noi se adaugă după cele 10.000, cele vechi nu se șterg).
//  - 1s–30s și „Ticuri”: lumânările se calculează în QuestDB din ticuri (pe „Ticuri”, una pe tic).
//
//  Folosește variabilele din app.js (state, $, setStatus).
// =====================================================================================

// Eticheta de pe chart: textul ei și bara de progres de dedesubt.
const storeBadge = $("storeBadge");
const storeText = storeBadge.querySelector(".sbtext");
const storeBar = storeBadge.querySelector(".progress div");
// Formatări: număr cu separator de mii („10.000”), eticheta timeframe-ului curent („M5”),
// ora unei lumânări (sau „—” dacă lipsește).
const fmtInt = (n) => Number(n || 0).toLocaleString("ro-RO");
const tfLabel = () => TFS.find(([tf]) => tf === state.tf)?.[1] ?? "";
const fmtUtc = (t) => (t ? fmtBarTime(t) : "—");

// Ce face serverul acum, pe faze (vezi datastore.py).
const PHASES = {
  read: "Citesc din QuestDB", download: "Descarc", update: "Actualizez", history: "Completez istoricul", rebuild: "Refac",
};

// Apelată de app.js cu starea datastore-ului (din hello sau din mesajele "store").
// s.state: off / pending / syncing / error / ready. Construiește textul, explicația (title) și bara.
window.onStoreStatus = (s) => {
  if (!s) return;
  storeBadge.hidden = false;
  storeBadge.className = `storebadge ${s.state}`;
  const tf = tfLabel();
  let text = "", title = "", bar = null;
  if (s.state === "off") {
    text = `${tf}: ticuri brute · fără indicatori`;
    title = "Pe „Ticuri” graficul arată fiecare tic din QuestDB, fără lumânări, " +
      "deci indicatorii nu rulează. Alege 1s sau un timeframe mai mare.";
  } else if (s.state === "pending") {
    text = `${tf}: verific datastore-ul…`;
  } else if (s.state === "syncing") {
    // La „update” arătăm cât din golul de umplut s-a descărcat; la celelalte faze, cât din
    // cele 10.000 de lumânări avem.
    const pct = Math.floor((s.phase === "update" ? s.progress : s.pct) * 100);
    text = `${PHASES[s.phase] || "Sincronizez"} ${tf}: ${pct}%`;
    if (s.phase !== "update") text += ` · ${fmtInt(s.bars)} / ${fmtInt(s.target)}`;
    bar = s.phase === "update" ? s.progress : s.pct;
    title = "Datastore-ul local se descarcă; indicatorii pornesc când ajunge la 100%.";
    if (!state.loaded) setStatus(false, `datastore ${pct}%…`);
  } else if (s.state === "error") {
    text = `${tf}: eroare datastore — reîncerc`;
    title = s.error || "";
  } else {
    // ready: fișierul e la zi.
    const pct = Math.floor((s.pct ?? 1) * 100);
    text = `${tf} · ${pct}% · ${fmtInt(s.bars)} lumânări`;
    if (pct < 100) bar = s.pct;
    title = [
      `Lumânări din ${s.file || "QuestDB"}`,
      `Prima lumânare: ${fmtUtc(s.first)}`,
      `Ultima lumânare închisă: ${fmtUtc(s.last)}`,
      s.complete && s.bars < s.target
        ? `Sursa are doar ${fmtInt(s.bars)} lumânări pe acest timeframe (tot istoricul e salvat).`
        : `Ținta: ultimele ${fmtInt(s.target)} de lumânări; cele noi se adaugă după ele.`,
      "Se actualizează la fiecare lumânare închisă. Indicatorii rulează pe aceste date.",
    ].join("\n");
  }
  // Afișăm textul și explicația; bara apare doar când avem un procent de arătat.
  storeText.textContent = text;
  storeBadge.title = title;
  storeBar.parentElement.hidden = bar === null;
  if (bar !== null) storeBar.style.width = `${Math.max(0, Math.min(1, bar)) * 100}%`;
};
