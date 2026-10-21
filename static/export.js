// Fereastra „Export date în CSV” (butonul 💾 din dreapta sus).
// =====================================================================================
//  CE FACE ACEST FIȘIER (pe scurt)
// -------------------------------------------------------------------------------------
//  - Utilizatorul alege simbolul, timeframe-ul (sau ticurile brute) și perioada (start / final).
//  - Pornește exportul pe server (POST /api/export); serverul scrie fișierul în fundal, în
//    folderul exporturi_csv/<simbol>/, iar pagina afișează progresul până la final.
//  - La final apare calea fișierului și un link de descărcare (util de pe telefon).
//
//  Folosește variabilele definite în app.js: $, state, TFS.
// =====================================================================================
// Tot codul e într-o funcție care se execută imediat, ca variabilele de aici (panel, form,
// showMsg...) să nu se amestece cu cele cu același nume din alerts.js.
(() => {
  const panel = $("exportPanel");
  const form = $("exportForm");
  const eTf = $("eTf"), eStart = $("eStart"), eEnd = $("eEnd"), eSymbol = $("eSymbol");
  const msgEl = $("eMsg"), bar = $("eProgress"), goBtn = $("eGo");

  // Opțiunile de timeframe: ticurile brute (bid / ask, valoarea 0) și toate timeframe-urile chartului.
  eTf.add(new Option("Ticuri (bid / ask)", "0"));
  for (const [tf, label] of TFS) eTf.add(new Option(label, String(tf)));

  // O dată calendaristică <-> textul „AAAA-LL-ZZ” folosit de câmpurile de tip date (ora locală).
  const ymd = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const parse = (s) => new Date(`${s}T00:00:00`);

  // Implicit: ultimul an, până azi. Datele din viitor nu se pot alege.
  function setDefaults() {
    const today = new Date();
    const start = new Date(today);
    start.setFullYear(today.getFullYear() - 1);
    eEnd.value = ymd(today);
    eStart.value = ymd(start);
    eStart.max = eEnd.max = ymd(today);
  }
  setDefaults();

  // Scurtăturile de perioadă: data de start = data de final minus N zile / luni.
  for (const b of panel.querySelectorAll(".presets button")) {
    b.onclick = () => {
      const d = parse(eEnd.value || ymd(new Date()));
      if (b.dataset.days) d.setDate(d.getDate() - Number(b.dataset.days));
      else d.setMonth(d.getMonth() - Number(b.dataset.months));
      eStart.value = ymd(d);
      updateHint();
    };
  }

  // Explicație sub câmpuri: câte zile sunt și, pentru ticuri / lumânări sub un minut, că
  // exportul (construit din ticuri) poate dura pe perioade lungi.
  function updateHint() {
    const hint = $("eHint");
    if (!eStart.value || !eEnd.value) { hint.textContent = ""; return; }
    const days = Math.round((parse(eEnd.value) - parse(eStart.value)) / 86400000) + 1;
    if (days < 1) { hint.textContent = "Data de start trebuie să fie înaintea datei de final."; return; }
    let text = `${days} ${days === 1 ? "zi" : "zile"}`;
    const tf = Number(eTf.value);
    if (tf < 60 && days > 7) text += " · din ticuri: pe perioade lungi exportul durează mai mult (minute)";
    hint.textContent = text;
  }
  for (const el of [eStart, eEnd, eTf]) el.addEventListener("change", updateHint);

  // Mesajul de sub formular (verde = ok, roșu = eroare); poate conține HTML (link-ul de descărcare).
  function showMsg(html, ok) {
    msgEl.className = `msg ${ok ? "ok" : "err"}`;
    msgEl.innerHTML = html;
  }
  // Textul de la server ajunge în pagină ca text, nu ca HTML.
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

  // ---------- Deschidere / închidere ----------
  function open() {
    // Fereastra de alarme și cea de export ocupă același loc: închidem alarmele.
    if (!$("alertPanel").hidden) $("alertClose").click();
    panel.hidden = false;
    // Dacă nu rulează niciun export, fereastra pornește cu simbolul și timeframe-ul chartului.
    if (!running) {
      eSymbol.value = state.symbol;
      eTf.value = String(state.tf);
      eStart.max = eEnd.max = ymd(new Date());
    }
    updateHint();
  }
  const close = () => { panel.hidden = true; };
  $("exportBtn").onclick = () => (panel.hidden ? open() : close());
  $("exportClose").onclick = close;
  $("alertBtn").addEventListener("click", close);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !panel.hidden) close(); });

  // ---------- Exportul ----------
  let running = false;   // un export e în curs (butonul „Exportă” e blocat)

  // Butonul „Exportă”: trimite cererea serverului, apoi urmărește progresul până la final.
  form.onsubmit = async (e) => {
    e.preventDefault();
    if (running) return;
    const start = eStart.value, end = eEnd.value;
    if (parse(end) < parse(start)) { showMsg("Data de start trebuie să fie înaintea datei de final.", false); return; }
    // Perioada acoperă zilele întregi, în ora locală: de la începutul zilei de start până la
    // sfârșitul zilei de final.
    const endDay = parse(end);
    endDay.setDate(endDay.getDate() + 1);
    const body = {
      symbol: eSymbol.value.trim(), tf: Number(eTf.value), start, end,
      start_ms: parse(start).getTime(), end_ms: endDay.getTime(),
    };
    running = true;
    goBtn.disabled = true;
    showMsg("Pornesc exportul...", true);
    try {
      const r = await fetch("/api/export", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.detail || r.statusText);
      await follow(data);
    } catch (err) {
      showMsg(esc(err.message), false);
    } finally {
      running = false;
      goBtn.disabled = false;
      bar.hidden = true;
    }
  };

  // Urmărește exportul până se termină: la fiecare 0,7 s cere starea lui și mută bara de progres.
  async function follow(job) {
    bar.hidden = false;
    for (;;) {
      bar.firstElementChild.style.width = `${Math.round(job.progress * 100)}%`;
      if (job.status === "done") {
        showMsg(`Salvat: <code>${esc(job.file)}</code> (${job.rows.toLocaleString("ro-RO")} rânduri) · ` +
          `<a href="/api/export/${job.id}/file" download="${esc(job.name)}">Descarcă</a>`, true);
        return;
      }
      if (job.status === "error") throw new Error(`Exportul a eșuat: ${job.error}`);
      showMsg(`Export în curs: ${Math.round(job.progress * 100)}% · ` +
        `${job.rows.toLocaleString("ro-RO")} rânduri`, true);
      await new Promise((res) => setTimeout(res, 700));
      const r = await fetch(`/api/export/${job.id}`);
      if (!r.ok) throw new Error("Serverul nu mai știe de acest export (a fost repornit?)");
      job = await r.json();
    }
  }
})();
