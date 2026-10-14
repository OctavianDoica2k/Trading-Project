// Alarme de preț: panou, linii pe chart, notificări în pagină. Folosește `state`, `series`, `chart` din app.js.
// =====================================================================================
//  CE FACE ACEST FIȘIER (pe scurt)
// -------------------------------------------------------------------------------------
//  - Fereastra „Alarme de preț”: deschidere/închidere, mutare cu mouse-ul, formularul.
//  - Linia mov de alarmă de pe chart: o poți trage, iar câmpul „Preț țintă” se completează
//    automat (și invers: dacă scrii prețul, linia se mută).
//  - Lista alarmelor (cerută de la server) și liniile punctate 🔔 pentru alarmele active.
//  - Ce se întâmplă în pagină când se declanșează o alarmă: mesaj pe ecran, sunet,
//    notificare de browser.
//
//  Comunicarea cu serverul se face prin funcția api() (cereri HTTP cu date JSON).
// =====================================================================================

// Elementele HTML folosite des (le căutăm o singură dată).
const panel = $("alertPanel");
const form = $("alertForm");
const aPrice = $("aPrice"), aSide = $("aSide"), aEmail = $("aEmail"), aNote = $("aNote");
// alertList = alarmele primite de la server; alertLines = liniile 🔔 desenate pe chart.
let alertList = [];
let alertLines = [];
// smtpOk = e-mailul e configurat pe server? (dacă nu, afișăm un avertisment)
let smtpOk = true;

// Completăm automat adresa de e-mail folosită ultima dată pe acest dispozitiv.
aEmail.value = store.get("alertEmail", "");

// Prețul curent pe latura aleasă (bid sau ask), luat din ultimul tic primit.
// Întoarce null dacă n-a sosit încă niciun tic.
function currentPrice(side = aSide.value) {
  const last = state.ticks[state.ticks.length - 1];
  return last ? (side === "ask" ? last[2] : last[1]) : null;
}

// ---------- Fereastra de alarme ----------
// Fereastră plutitoare, fără fundal blocant: chartul și scala de prețuri rămân utilizabile.
const candlesEl = $("candles");
// Suntem pe ecran îngust (telefon)? Aceeași limită ca în style.css (640px).
const mobile = () => matchMedia("(max-width: 640px)").matches;

// Scrie lățimea scalei de prețuri din dreapta în variabila CSS --axis-w.
// style.css o folosește ca să așeze fereastra fără să acopere scala.
function syncAxisWidth() {
  // Fereastra se poziționează lângă scala de prețuri, fără s-o acopere.
  const w = chart.priceScale("right").width();
  if (w) document.documentElement.style.setProperty("--axis-w", `${w}px`);
}
// Recalculăm la fiecare schimbare de mărime a chartului.
new ResizeObserver(syncAxisWidth).observe(candlesEl);

// Deschide fereastra. `price` (opțional) = prețul la care să apară linia de alarmă
// (ex. de la dublu-click pe chart). Dacă lipsește, linia pornește de la prețul scris deja
// în câmp sau, dacă acesta e gol, de la prețul curent.
function openPanel(price) {
  const wasHidden = panel.hidden;
  panel.hidden = false;
  // Clasa "editing" pe chart activează stilurile pentru tragerea liniei (vezi style.css).
  candlesEl.classList.add("editing");
  syncAxisWidth();
  // La o deschidere nouă, fereastra revine în centru (chiar dacă înainte fusese mutată).
  if (wasHidden) resetPanelPosition();
  $("aSymbol").value = state.symbol;
  if (price != null) setDraftPrice(price);
  else if (aPrice.value && wasHidden) setDraftPrice(Number(aPrice.value));
  else if (wasHidden) setNow();
  refresh();
}
// Închide fereastra și scoate linia de alarmă (încă nesalvată) de pe chart.
function closePanel() {
  panel.hidden = true;
  candlesEl.classList.remove("editing", "nearline", "dragging");
  removeDraft();
}
// Butonul 🔔 deschide/închide fereastra; ✕ și tasta Escape o închid.
$("alertBtn").onclick = () => (panel.hidden ? openPanel() : closePanel());
$("alertClose").onclick = closePanel;
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !panel.hidden) closePanel(); });

// Fereastra se poate muta trăgând de titlu (pe desktop).
// Ștergând pozițiile setate manual, fereastra revine la poziția din CSS (centrată).
function resetPanelPosition() {
  panel.style.left = panel.style.top = panel.style.transform = "";
}
// Funcție care se execută imediat, ca variabilele `head` și `off` să rămână „private” aici.
(() => {
  const head = panel.querySelector(".panelhead");
  // off = distanța dintre cursor și colțul ferestrei în momentul apucării (null = nu tragem).
  let off = null;
  head.addEventListener("pointerdown", (e) => {
    // Pe telefon fereastra stă fixă jos; iar un click pe ✕ nu trebuie să înceapă mutarea.
    if (mobile() || e.target.closest("button")) return;
    const r = panel.getBoundingClientRect();
    off = { x: e.clientX - r.left, y: e.clientY - r.top };
    // „Capturăm” cursorul: primim mișcările chiar dacă iese din zona titlului.
    head.setPointerCapture(e.pointerId);
  });
  head.addEventListener("pointermove", (e) => {
    if (!off) return;
    const r = panel.getBoundingClientRect();
    // Cea mai din dreapta poziție permisă: fereastra nu are voie să acopere scala de prețuri.
    const maxX = innerWidth - r.width - (parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--axis-w")) || 0);
    // Renunțăm la centrarea din CSS și poziționăm fereastra exact unde e cursorul,
    // limitată la marginile ecranului.
    panel.style.transform = "none";
    panel.style.left = `${Math.min(Math.max(e.clientX - off.x, 0), Math.max(maxX, 0))}px`;
    panel.style.top = `${Math.min(Math.max(e.clientY - off.y, 0), innerHeight - 60)}px`;
  });
  const stop = () => (off = null);
  head.addEventListener("pointerup", stop);
  head.addEventListener("pointercancel", stop);
})();

// ---------- Linia de alarmă de pe chart ----------
// draftLine = linia mov „⇕ alarmă” (o „ciornă” a alarmei, până apeși „Adaugă alarmă”).
let draftLine = null;
// Rotunjește un preț la pasul simbolului (ex. 1.1204637 -> 1.12046 la EURUSD).
const roundPrice = (p) => Number((Math.round(p / state.point) * state.point).toFixed(state.digits));

// Mută linia mov la prețul `p` (o creează dacă nu există) și actualizează câmpul de preț.
// fromInput = true când prețul vine chiar din câmp: atunci NU rescriem câmpul, ca să nu
// stricăm ce tastează utilizatorul.
function setDraftPrice(p, fromInput = false) {
  if (p == null || !Number.isFinite(p) || p <= 0) return;
  p = roundPrice(p);
  if (!fromInput) aPrice.value = p.toFixed(state.digits);
  if (draftLine) {
    draftLine.applyOptions({ price: p });
  } else {
    draftLine = series.createPriceLine({
      price: p, color: css("--alert"), lineWidth: 2,
      lineStyle: LightweightCharts.LineStyle.Solid, axisLabelVisible: true, title: "⇕ alarmă",
    });
  }
  updateHint();
}
// Scoate linia mov de pe chart.
function removeDraft() {
  if (draftLine) { series.removePriceLine(draftLine); draftLine = null; }
}

// Butonul „Preț curent”: linia sare la prețul curent.
function setNow() {
  const p = currentPrice();
  if (p) setDraftPrice(p);
}
$("aNow").onclick = setNow;
// Butoanele „−” și „+”: mută prețul cu 10 puncte în jos/sus (data-dir="-1" sau "1" în HTML).
for (const b of form.querySelectorAll(".step")) {
  b.onclick = () => {
    const v = Number(aPrice.value) || currentPrice() || 0;
    setDraftPrice(v + Number(b.dataset.dir) * state.point * 10);
  };
}
// Când scrii în câmpul de preț, linia se mută odată cu ce tastezi.
aPrice.addEventListener("input", () => {
  const v = Number(aPrice.value);
  if (v > 0) setDraftPrice(v, true); else updateHint();
});
aSide.addEventListener("change", updateHint);

// Tragerea liniei: interceptăm evenimentele în faza de capture, înainte să ajungă la chart,
// ca să nu se miște chartul în timp ce muți linia.
// (Evenimentele „coboară” întâi prin elementele părinte – faza de capture – și abia apoi
//  ajung la elementul atins. Ascultând pe părinte în faza de capture, le prindem primii și
//  le putem opri cu stopPropagation(), așa că biblioteca de chart nu le mai primește.)
let dragging = false;
// Poziția verticală a cursorului, relativ la marginea de sus a chartului.
const localY = (e) => e.clientY - candlesEl.getBoundingClientRect().top;
// Este cursorul suficient de aproape de linia mov ca s-o apuce?
// Toleranță: 8px cu mouse-ul, 22px cu degetul (degetul e mai puțin precis).
function nearLine(e) {
  if (panel.hidden || !draftLine) return false;
  const y = series.priceToCoordinate(draftLine.options().price);
  const tol = e.pointerType === "touch" ? 22 : 8;
  return y != null && Math.abs(localY(e) - y) <= tol;
}
// Apăsare lângă linie => începem tragerea.
// (Al treilea argument, true, înseamnă „ascultă în faza de capture”.)
candlesEl.addEventListener("pointerdown", (e) => {
  if (!nearLine(e)) return;
  dragging = true;
  try { candlesEl.setPointerCapture(e.pointerId); } catch { /* tragerea merge și fără */ }
  candlesEl.classList.add("dragging");
  e.stopPropagation();
  e.preventDefault();
}, true);
// Mișcare: dacă tragem, linia urmează cursorul; altfel doar schimbăm forma cursorului
// (săgeată verticală) când e deasupra liniei.
candlesEl.addEventListener("pointermove", (e) => {
  if (dragging) {
    setDraftPrice(series.coordinateToPrice(localY(e)));
    e.stopPropagation();
    e.preventDefault();
  } else {
    candlesEl.classList.toggle("nearline", nearLine(e));
  }
}, true);
// Eliberarea butonului / ridicarea degetului încheie tragerea.
const endDrag = (e) => {
  if (!dragging) return;
  dragging = false;
  candlesEl.classList.remove("dragging");
  e.stopPropagation();
};
candlesEl.addEventListener("pointerup", endDrag, true);
candlesEl.addEventListener("pointercancel", endDrag, true);
// Evenimentele mouse/touch „compatibile” vin după pointer*: le blocăm cât timp tragem.
// (passive: false ne permite să apelăm preventDefault(), care oprește derularea paginii pe telefon.)
for (const type of ["mousedown", "mousemove", "mouseup", "touchstart", "touchmove", "touchend"]) {
  candlesEl.addEventListener(type, (e) => {
    if (!dragging) return;
    e.stopPropagation();
    if (e.cancelable) e.preventDefault();
  }, { capture: true, passive: false });
}

// Click / tap pe chart cu fereastra deschisă -> linia sare la prețul de acolo.
// (subscribeClick e oferit de biblioteca de chart; param.point = coordonatele click-ului.)
chart.subscribeClick((param) => {
  if (panel.hidden || !param.point) return;
  setDraftPrice(series.coordinateToPrice(param.point.y));
});

// Textul de sub câmpul de preț: „↑ peste prețul curent (1.12034) cu 12 puncte”.
function updateHint() {
  const p = Number(aPrice.value), cur = currentPrice();
  const el = $("aHint");
  if (!p || !cur) { el.textContent = ""; return; }
  const pts = Math.round((p - cur) / state.point);
  if (pts === 0) { el.textContent = "Egal cu prețul curent"; el.className = "hint"; return; }
  el.textContent = `${pts > 0 ? "↑ peste" : "↓ sub"} prețul curent (${cur.toFixed(state.digits)}) cu ${Math.abs(pts)} puncte`;
  el.className = `hint ${pts > 0 ? "up" : "down"}`;
}
// Prețul curent se schimbă continuu, deci reîmprospătăm textul de două ori pe secundă.
setInterval(() => { if (!panel.hidden) updateHint(); }, 500);

// Afișează un mesaj sub formular (verde = ok, roșu = eroare), care dispare după 6 secunde.
// showMsg.t ține minte cronometrul anterior, ca un mesaj nou să nu fie șters prea devreme.
function showMsg(text, ok) {
  const el = $("aMsg");
  el.textContent = text;
  el.className = `msg ${ok ? "ok" : "err"}`;
  clearTimeout(showMsg.t);
  showMsg.t = setTimeout(() => (el.textContent = ""), 6000);
}

// Trimite o cerere la serverul nostru și întoarce răspunsul JSON.
// method = "GET" / "POST" / "DELETE"; body = datele trimise (opțional).
// Dacă serverul răspunde cu eroare, aruncă o excepție cu mesajul serverului (câmpul `detail`).
async function api(method, url, body) {
  const r = await fetch(url, {
    method, headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `Eroare ${r.status}`);
  return data;
}

// Butonul „Adaugă alarmă” (trimiterea formularului).
form.onsubmit = async (e) => {
  // Oprim comportamentul implicit al formularului (reîncărcarea paginii).
  e.preventDefault();
  const email = aEmail.value.trim();
  store.set("alertEmail", email);
  try {
    const a = await api("POST", "/api/alerts", {
      symbol: state.symbol, price: Number(aPrice.value), side: aSide.value, email, note: aNote.value,
    });
    aNote.value = "";
    showMsg(`Alarmă setată: ${a.symbol} ${a.direction === "above" ? "↑" : "↓"} ${a.price.toFixed(a.digits)}`, true);
    askNotificationPermission();
    refresh();
  } catch (err) { showMsg(err.message, false); }
};

// Butonul „Trimite e-mail de test”.
$("aTest").onclick = async () => {
  const email = aEmail.value.trim();
  if (!email) { showMsg("Completează adresa de e-mail", false); return; }
  store.set("alertEmail", email);
  showMsg("Se trimite...", true);
  try { await api("POST", "/api/alerts/test-email", { email }); showMsg(`E-mail de test trimis la ${email}`, true); }
  catch (err) { showMsg(err.message, false); }
};

// ---------- Listă + linii pe chart ----------
// Cere lista de alarme de la server și redesenează lista și liniile.
async function refresh() {
  try {
    const data = await api("GET", "/api/alerts");
    alertList = data.alerts;
    smtpOk = data.smtp;
  } catch { return; }
  $("smtpWarn").hidden = smtpOk;
  render();
}

// Construiește lista de alarme din fereastră și numărul de pe butonul 🔔.
function render() {
  const active = alertList.filter((a) => a.status === "active");
  $("alertCount").textContent = active.length;
  $("alertCount").classList.toggle("zero", active.length === 0);

  const ul = $("alertList");
  // Golim lista și o refacem de la zero.
  ul.replaceChildren();
  if (!alertList.length) {
    const li = document.createElement("li");
    li.className = "empty";
    li.textContent = "Nicio alarmă încă.";
    ul.appendChild(li);
  }
  // Alarmele active primele, apoi cele declanșate.
  const sorted = [...active, ...alertList.filter((a) => a.status !== "active")];
  for (const a of sorted) {
    const li = document.createElement("li");
    li.className = `alert ${a.status}`;
    const arrow = a.direction === "above" ? "↑" : "↓";
    // fx = formatarea unui preț cu zecimalele simbolului alarmei.
    const fx = (p) => Number(p).toFixed(a.digits ?? state.digits);
    const when = a.status === "triggered"
      ? `Atins ${fx(a.trigger_price)} · ${a.triggered_at.replace("T", " ")}`
      : `Activă · setată ${a.created_at.replace("T", " ")}`;
    // Scheletul HTML al unui rând. Textele le punem separat, cu textContent: așa, un text
    // care conține „<” sau „>” (ex. în notă) e afișat ca text, nu interpretat ca HTML.
    li.innerHTML = `
      <div class="atop">
        <span class="asym"></span>
        <span class="aprice ${a.direction === "above" ? "up" : "down"}"></span>
        <button class="iconbtn del" title="Șterge">✕</button>
      </div>
      <div class="ameta"></div>
      <div class="ameta mail"></div>
      <div class="anote"></div>`;
    li.querySelector(".asym").textContent = a.symbol;
    li.querySelector(".aprice").textContent = `${arrow} ${fx(a.price)} (${a.side})`;
    li.querySelector(".ameta").textContent = when;
    li.querySelector(".mail").textContent = a.email
      ? `✉ ${a.email}${a.email_status ? ` — ${a.email_status}` : ""}`
      : "✉ fără e-mail";
    li.querySelector(".anote").textContent = a.note || "";
    // ✕ șterge alarma de pe server, apoi reîmprospătăm lista.
    li.querySelector(".del").onclick = async () => {
      try { await api("DELETE", `/api/alerts/${a.id}`); refresh(); } catch (err) { showMsg(err.message, false); }
    };
    // Click pe simbol = trecem chartul pe simbolul alarmei.
    li.querySelector(".asym").onclick = () => {
      if (a.symbol !== state.symbol) { symInput.value = a.symbol; symInput.dispatchEvent(new Event("change")); }
    };
    ul.appendChild(li);
  }
  drawLines();
}

// Desenează câte o linie punctată 🔔 pentru fiecare alarmă activă a simbolului curent.
// Ștergem întâi liniile vechi, apoi le creăm din nou.
function drawLines() {
  for (const l of alertLines) series.removePriceLine(l);
  alertLines = alertList
    .filter((a) => a.status === "active" && a.symbol === state.symbol)
    .map((a) => series.createPriceLine({
      price: a.price, color: css("--alert"), lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: "🔔",
    }));
}

// Apelată de app.js după ce s-a încărcat un simbol (la pornire sau la schimbarea lui).
window.onSymbolLoaded = () => {
  $("aSymbol").value = state.symbol;
  syncAxisWidth();
  // Dacă fereastra e deschisă, mutăm linia mov la prețul noului simbol.
  if (!panel.hidden) { removeDraft(); setNow(); }
  drawLines();
};

// Dublu-click pe chart -> deschide fereastra cu linia la prețul de acolo
candlesEl.addEventListener("dblclick", (e) => {
  const price = series.coordinateToPrice(localY(e));
  if (price != null) openPanel(price);
});

// ---------- Notificări ----------
// Cere permisiunea de notificări a browserului, dacă încă n-a fost cerută
// ("default" = nici permisă, nici refuzată).
function askNotificationPermission() {
  if ("Notification" in window && Notification.permission === "default") {
    Notification.requestPermission().catch(() => {});
  }
}

// Un „bip” scurt generat direct de browser (fără fișier audio): un ton de 880 Hz
// care se stinge în 0,6 secunde.
function beep() {
  try {
    const ac = new AudioContext();
    const o = ac.createOscillator(), g = ac.createGain();
    o.frequency.value = 880; o.connect(g); g.connect(ac.destination);
    g.gain.setValueAtTime(0.15, ac.currentTime);
    g.gain.exponentialRampToValueAtTime(0.001, ac.currentTime + 0.6);
    o.start(); o.stop(ac.currentTime + 0.6);
  } catch { /* fără sunet */ }
}

// Afișează un mesaj plutitor jos pe ecran („toast”), care dispare la click sau după 15 secunde.
function toast(title, text) {
  const t = document.createElement("div");
  t.className = "toast";
  t.innerHTML = "<strong></strong><span></span>";
  t.querySelector("strong").textContent = title;
  t.querySelector("span").textContent = text;
  t.onclick = () => t.remove();
  $("toasts").appendChild(t);
  setTimeout(() => t.remove(), 15000);
}

// Alarmele deja anunțate în această pagină: serverul retrimite alarma și când i se
// actualizează statusul e-mailului, iar noi nu vrem să anunțăm de două ori.
const seenFired = new Set();
// Apelată de app.js când serverul trimite un eveniment de alarmă.
window.onAlertEvent = (a) => {
  refresh();
  if (a.status !== "triggered" || seenFired.has(a.id)) return;
  seenFired.add(a.id);
  const fx = (p) => Number(p).toFixed(a.digits ?? state.digits);
  const title = `🔔 ${a.symbol} ${a.direction === "above" ? "↑" : "↓"} ${fx(a.price)}`;
  const text = `Atins: ${fx(a.trigger_price)} (${a.side})${a.note ? " — " + a.note : ""}`;
  toast(title, text);
  beep();
  // Dacă dispozitivul e abonat la push, notificarea Windows vine de acolo (fără dublură).
  if (!window.pushActive?.() && "Notification" in window && Notification.permission === "granted") {
    try { new Notification(title, { body: text, tag: `alert-${a.id}` }); } catch { /* unele browsere mobile nu permit */ }
  }
};

// La încărcarea paginii: cerem lista de alarme (pentru numărul de pe buton și liniile 🔔).
refresh();
