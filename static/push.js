// Notificări push pe dispozitiv (Windows / Android), primite și cu pagina închisă.
// Folosește `api`, `store`, `aEmail`, `showMsg`, `$` din app.js / alerts.js.
// =====================================================================================
//  CE FACE ACEST FIȘIER (pe scurt)
// -------------------------------------------------------------------------------------
//  Gestionează secțiunea „Notificări pe acest dispozitiv” din fereastra de alarme:
//   1. verifică dacă browserul poate primi notificări push (cere HTTPS sau localhost);
//   2. la „Activează notificări”: cere permisiunea, pornește service worker-ul (sw.js),
//      abonează browserul la serviciul lui de push și trimite abonamentul serverului;
//   3. la „Dezactivează”: anulează abonamentul în browser și pe server;
//   4. „Trimite notificare de test”: cere serverului o notificare doar pentru acest dispozitiv.
//  Fiecare pas e afișat sub butoane (în elementul #pushHint), ca să se vadă unde s-a oprit.
// =====================================================================================

// Adresa HTTPS prin Tailscale, recomandată când pagina e deschisă pe o adresă fără HTTPS.
const HTTPS_URL = "https://calculator.tailnet.ts.net";
// Are browserul tot ce trebuie? Service worker + Push API + Notification API.
const pushSupported = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
let pushSub = null;   // abonamentul acestui browser, dacă există

// Transformă cheia publică primită de la server (text base64 „pentru adrese web”) în octeți,
// formatul cerut de pushManager.subscribe(). Înlocuim -/_ cu +/ și adăugăm „=” la final,
// apoi decodificăm cu atob().
const b64ToBytes = (b64) => {
  const s = atob(b64.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (b64.length % 4)) % 4));
  return Uint8Array.from(s, (c) => c.charCodeAt(0));
};

// O descriere scurtă a dispozitivului (ex. „Chrome pe Windows”), salvată pe server lângă
// abonament, ca să știm ce dispozitiv e. O deducem din „user agent” (textul de identificare
// trimis de browser). Ordinea contează: Edge conține și „Chrome” în text, deci îl testăm primul.
function deviceName() {
  const ua = navigator.userAgent;
  const os = /Windows/.test(ua) ? "Windows" : /Android/.test(ua) ? "Android" : /iPhone|iPad/.test(ua) ? "iOS" : /Mac/.test(ua) ? "macOS" : "Linux";
  const br = /Edg\//.test(ua) ? "Edge" : /Chrome\//.test(ua) ? "Chrome" : /Firefox\//.test(ua) ? "Firefox" : /Safari\//.test(ua) ? "Safari" : "Browser";
  return `${br} pe ${os}`;
}

// Actualizează secțiunea: eticheta colorată de stare, textul butonului principal,
// vizibilitatea butonului de test și textul explicativ (`hint`, poate conține HTML).
// `state` aici e starea notificărilor: "on" | "off" | "blocked" | "unsupported".
function renderPush(state, hint = "") {
  const pill = $("pushState"), toggle = $("pushToggle"), test = $("pushTest");
  // Pentru fiecare stare: [eticheta, clasa CSS a etichetei, textul butonului (null = fără buton)].
  const states = {
    on: ["activă", "on", "Dezactivează"],
    off: ["oprită", "", "Activează notificări"],
    blocked: ["blocată", "off", "Cum le permit?"],
    unsupported: ["indisponibilă", "off", null],
  };
  const [label, cls, btn] = states[state];
  pill.textContent = label;
  pill.className = `pill ${cls}`;
  toggle.hidden = !btn;
  if (btn) toggle.textContent = btn;
  test.hidden = state !== "on";
  $("pushHint").innerHTML = hint;
}

// Verifică situația curentă și afișează starea corectă. Se apelează la încărcarea paginii
// și după fiecare activare/dezactivare.
async function refreshPush() {
  // „Context securizat” = pagină pe HTTPS sau pe localhost. Altfel browserul interzice push-ul.
  if (!window.isSecureContext) {
    renderPush("unsupported",
      `Notificările push cer HTTPS. Deschide aplicația la <a href="${HTTPS_URL}">${HTTPS_URL.replace("https://", "")}</a>` +
      " (sau pe acest PC la http://localhost:8000).");
    return;
  }
  if (!pushSupported) {
    renderPush("unsupported", "Browserul nu suportă notificări push. Pe iPhone: Share → Add to Home Screen, apoi deschide aplicația de acolo.");
    return;
  }
  // Utilizatorul a refuzat odată notificările: browserul nu mai întreabă, trebuie permis manual.
  if (Notification.permission === "denied") {
    renderPush("blocked", BLOCKED_HINT);
    return;
  }
  try {
    // Înregistrăm service worker-ul (dacă e deja înregistrat, primim înregistrarea existentă)
    // și verificăm dacă browserul are deja un abonament push.
    const reg = await navigator.serviceWorker.register("/sw.js");
    pushSub = await reg.pushManager.getSubscription();
    if (pushSub) {
      const { subscribed } = await api("POST", "/api/push/status", { endpoint: pushSub.endpoint });
      if (!subscribed) await sendSubscription(); // serverul l-a uitat (ex: alt PC) -> îl retrimitem
    }
  } catch (err) {
    renderPush("unsupported", `Nu pot porni notificările: ${err.message}`);
    return;
  }
  renderPush(pushSub ? "on" : "off", pushSub ? ownerHint() :
    "Primești alarmele ca notificări Windows, chiar dacă pagina e închisă (browserul trebuie să ruleze).");
}

// Textul care explică ce alarme primește dispozitivul (în funcție de e-mailul din formular).
// `.replace(/</g, "&lt;")` împiedică interpretarea e-mailului ca HTML.
function ownerHint() {
  const email = aEmail.value.trim();
  return email
    ? `Primești pe acest dispozitiv alarmele setate pentru <b>${email.replace(/</g, "&lt;")}</b>.`
    : "Fără e-mail completat mai sus, acest dispozitiv primește <b>toate</b> alarmele.";
}

// Trimite serverului abonamentul acestui browser, împreună cu e-mailul și numele dispozitivului.
async function sendSubscription() {
  if (!pushSub) return;
  await api("POST", "/api/push/subscribe", {
    subscription: pushSub.toJSON(), owner: aEmail.value.trim(), device: deviceName(),
  });
}

// Instrucțiunile afișate când notificările sunt blocate pentru site.
const BLOCKED_HINT =
  "Notificările sunt blocate pentru acest site. Apasă pe iconița 🔒 (sau ⓘ) din stânga adresei → " +
  "<b>Notificări → Permite</b>, apoi reîncarcă pagina.";

// Afișează pasul curent sub butoane și îl scrie și în consola browserului (F12), fără etichetele HTML.
const step = (text) => { $("pushHint").innerHTML = text; console.info("[push]", text.replace(/<[^>]+>/g, "")); };

// Activarea notificărilor, pas cu pas. Orice problemă aruncă o eroare cu mesaj clar,
// afișat apoi de handler-ul butonului.
async function enablePush() {
  // Edge (implicit) și uneori Chrome nu deschid fereastra de permisiune, ci pun doar un clopoțel
  // în bara de adrese, iar cererea rămâne în așteptare până dai click pe el.
  // De aceea, dacă după 2,5 secunde n-a venit niciun răspuns, explicăm unde trebuie apăsat.
  step("Aștept permisiunea browserului…");
  const hintTimer = setTimeout(() => step(
    "Aștept permisiunea… Dacă nu a apărut nicio fereastră, apasă pe <b>clopoțelul</b> (sau iconița 🔒) " +
    "din bara de adrese, sus, și alege <b>Permite</b>."), 2500);
  let perm;
  try { perm = await Notification.requestPermission(); } finally { clearTimeout(hintTimer); }
  if (perm === "denied") throw new Error("permisiune refuzată");
  if (perm !== "granted") throw new Error("permisiunea nu a fost acordată (fereastra a fost închisă)");

  // Pornim service worker-ul și așteptăm să fie activ (`ready`).
  step("Pornesc serviciul de notificări…");
  const reg = await navigator.serviceWorker.register("/sw.js");
  await navigator.serviceWorker.ready;

  // Abonarea propriu-zisă. userVisibleOnly: true = promitem că fiecare mesaj push afișează
  // o notificare vizibilă (Chrome cere asta). applicationServerKey = cheia noastră publică VAPID,
  // care leagă abonamentul de serverul nostru.
  step("Abonez acest dispozitiv…");
  const { publicKey } = await api("GET", "/api/push/key");
  try {
    pushSub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(publicKey) });
  } catch (err) {
    throw new Error(`browserul nu s-a putut abona la serviciul de push (${err.name}: ${err.message}). ` +
      "Brave/Opera/Vivaldi au uneori push-ul dezactivat: încearcă în Chrome sau Edge.");
  }
  await sendSubscription();
}

// Dezactivarea: anulăm abonamentul în browser și îl ștergem de pe server.
// .catch(() => {}) = ignorăm erorile; vrem ca dezactivarea să reușească oricum local.
async function disablePush() {
  if (!pushSub) return;
  const endpoint = pushSub.endpoint;
  await pushSub.unsubscribe().catch(() => {});
  await api("POST", "/api/push/unsubscribe", { endpoint }).catch(() => {});
  pushSub = null;
}

// Butonul principal: „Activează notificări” / „Dezactivează” / „Cum le permit?”.
$("pushToggle").onclick = async () => {
  const btn = $("pushToggle");
  // Butonul e dezactivat cât rulează o operație, ca dublu-click-ul să n-o pornească de două ori.
  if (btn.disabled) return;
  btn.disabled = true;
  try {
    if (Notification.permission === "denied") {
      step(BLOCKED_HINT);           // browserul nu mai întreabă: trebuie permis manual
      return;
    }
    const wasOn = !!pushSub;
    await (wasOn ? disablePush() : enablePush());
    await refreshPush();
    if (!wasOn && pushSub) {
      $("pushHint").innerHTML = "✅ Notificări activate. " + ownerHint() +
        " Apasă „Trimite notificare de test” ca să verifici.";
    }
  } catch (err) {
    // Eroarea apare în consola browserului (pentru depanare) și, explicată, sub butoane.
    console.error("[push]", err);
    await refreshPush().catch(() => {});
    $("pushHint").innerHTML = `<span class="down">Nu s-a putut activa: ${String(err.message).replace(/</g, "&lt;")}</span>` +
      (Notification.permission === "denied" ? "<br>" + BLOCKED_HINT : "");
  } finally {
    // `finally` rulează mereu: reactivăm butonul indiferent de rezultat.
    btn.disabled = false;
  }
};
// Butonul „Trimite notificare de test”.
$("pushTest").onclick = async () => {
  try {
    step("Trimit notificarea de test…");
    await api("POST", "/api/push/test", { endpoint: pushSub.endpoint });
    step("✅ Trimisă — ar trebui să apară în colțul din dreapta jos. Dacă nu apare: Windows " +
      "<b>Settings → System → Notifications</b> trebuie să fie activat pentru browser, iar „Do not disturb” oprit.");
  } catch (err) {
    step(`<span class="down">Eroare: ${String(err.message).replace(/</g, "&lt;")}</span>`);
  }
};

// Dacă schimbi adresa de e-mail, abonamentul se actualizează ca să primești alarmele potrivite.
aEmail.addEventListener("change", () => {
  if (pushSub) sendSubscription().then(() => renderPush("on", ownerHint())).catch(() => {});
});

// Pagina deschisă nu mai afișează și ea o notificare dacă vine deja una prin push.
// (alerts.js apelează această funcție înainte de a afișa propria notificare.)
window.pushActive = () => !!pushSub;

// La încărcarea paginii: afișăm starea notificărilor pentru acest dispozitiv.
refreshPush();
