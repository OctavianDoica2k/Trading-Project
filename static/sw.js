// Service worker: primește notificările push și le afișează chiar dacă pagina e închisă.
// =====================================================================================
//  CE ESTE UN SERVICE WORKER
// -------------------------------------------------------------------------------------
//  Un script special pe care browserul îl păstrează „în fundal”, separat de pagină.
//  Nu are acces la pagină (butoane, chart), dar poate fi trezit de browser la anumite
//  evenimente – aici: sosirea unui mesaj push și click-ul pe o notificare.
//  `self` = service worker-ul însuși (echivalentul lui `window` dintr-o pagină).
//  Este înregistrat din push.js, cu navigator.serviceWorker.register("/sw.js").
// =====================================================================================

// install: o versiune nouă a acestui fișier a fost descărcată. skipWaiting() o activează
// imediat, fără să aștepte închiderea tuturor tab-urilor vechi.
self.addEventListener("install", () => self.skipWaiting());
// activate: versiunea nouă preia imediat controlul paginilor deja deschise (clients.claim).
// waitUntil = „nu considera evenimentul terminat până nu se termină operația dată”.
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

// push: a sosit un mesaj de la serverul nostru (trimis din push.py).
// Conținutul e JSON: {title, body, tag, url}. Browserul l-a decriptat deja pentru noi.
self.addEventListener("push", (event) => {
  let data = {};
  // Dacă mesajul nu e JSON valid, îl afișăm ca text simplu.
  try { data = event.data ? event.data.json() : {}; } catch { data = { body: event.data && event.data.text() }; }
  const title = data.title || "Nexus";
  // Afișăm notificarea în sistem (centrul de notificări Windows / bara de notificări Android).
  event.waitUntil(self.registration.showNotification(title, {
    body: data.body || "",
    tag: data.tag,              // aceeași alarmă nu apare de două ori
    // renotify: chiar dacă o notificare cu același tag e deja afișată, anunțăm din nou (sunet).
    renotify: true,
    requireInteraction: true,   // rămâne pe ecran până o închizi
    // Adresa de deschis la click o păstrăm în datele notificării.
    data: { url: data.url || "/" },
  }));
});

// notificationclick: utilizatorul a dat click pe notificare.
// Dacă aplicația e deja deschisă într-un tab, o aducem în față pe simbolul alarmei;
// altfel deschidem un tab nou.
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  // Transformăm adresa relativă (ex. "/?symbol=EURUSD") într-una completă, pe site-ul nostru.
  const url = new URL(event.notification.data?.url || "/", self.location.origin).href;
  event.waitUntil((async () => {
    // Toate ferestrele/tab-urile browserului care aparțin site-ului nostru.
    const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const w of wins) {
      if (new URL(w.url).origin === self.location.origin) {
        await w.focus();
        if ("navigate" in w) await w.navigate(url);
        return;
      }
    }
    await self.clients.openWindow(url);
  })());
});
