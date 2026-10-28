// Fereastra „Strategy Tester” de peste chart (butonul Tester din dreapta sus).
// =====================================================================================
//  CE FACE ACEST FIȘIER (pe scurt)
// -------------------------------------------------------------------------------------
//  - Testerul e pagina /tester (tester.html / tester.js), încărcată într-un iframe la prima
//    deschidere a ferestrei, cu ?embed=1 (fără bara ei de sus: titlul e aici).
//  - ✕ / Esc / click în afara ferestrei o ascund, dar nu o distrug: un test pornit continuă, iar
//    butonul Tester arată un punct cât rulează. ⤢ = pe tot ecranul; ↗ = testerul într-un tab nou.
//  - Pe desktop fereastra se mută trăgând de bara de titlu.
//  - Pagina din iframe trimite prin postMessage starea testului și cererea de închidere (Esc).
// =====================================================================================
(() => {
  const win = $("testerWin"), box = win.querySelector(".twinbox"), frame = $("testerFrame");
  const head = win.querySelector(".twinhead");

  function open() {
    if (!frame.src) frame.src = "/tester?embed=1";
    win.hidden = false;
    frame.focus();
  }
  const close = () => { win.hidden = true; };
  $("testerBtn").onclick = () => (win.hidden ? open() : close());
  $("testerClose").onclick = close;
  $("testerMax").onclick = () => {
    box.classList.toggle("max");
    box.style.left = box.style.top = "";
  };
  // Click pe fundalul întunecat (în afara ferestrei) = închidere.
  win.addEventListener("mousedown", (e) => { if (e.target === win) close(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !win.hidden) close(); });

  // Mesajele paginii din iframe (aceeași origine): starea testului și Esc apăsat acolo.
  window.addEventListener("message", (e) => {
    if (e.origin !== location.origin || !e.data || typeof e.data !== "object") return;
    if (e.data.type === "tester-close") close();
    if (e.data.type === "tester-state") {
      $("testerState").textContent = e.data.text || "";
      $("testerState").classList.toggle("on", !!e.data.running);
      $("testerDot").hidden = !e.data.running;
    }
  });

  // Mutarea ferestrei: trăgând de bara de titlu (nu și când e mărită sau pe telefon).
  head.addEventListener("pointerdown", (e) => {
    if (e.target.closest("button, a") || box.classList.contains("max") || window.innerWidth < 760) return;
    const r = box.getBoundingClientRect();
    const dx = e.clientX - r.left, dy = e.clientY - r.top;
    frame.style.pointerEvents = "none";   // altfel iframe-ul „înghite” mișcările mouse-ului
    head.setPointerCapture(e.pointerId);
    const move = (ev) => {
      box.style.left = `${Math.max(0, Math.min(window.innerWidth - 120, ev.clientX - dx))}px`;
      box.style.top = `${Math.max(0, Math.min(window.innerHeight - 40, ev.clientY - dy))}px`;
    };
    const up = () => {
      frame.style.pointerEvents = "";
      head.removeEventListener("pointermove", move);
      head.removeEventListener("pointerup", up);
    };
    head.addEventListener("pointermove", move);
    head.addEventListener("pointerup", up);
  });
})();
