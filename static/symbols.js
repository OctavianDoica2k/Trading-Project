// Meniul de simboluri: butonul simbolului din bara de sus deschide o listă cu toate simbolurile
// sursei de prețuri active (MT5 sau cTrader), grupate pe categorii, cu căutare live.
// =====================================================================================
//  CE FACE ACEST FIȘIER (pe scurt)
// -------------------------------------------------------------------------------------
//  - Cere de la server (/api/symbols) simbolurile sursei active, cu descrierea și categoria lor
//    (MT5: folderele din terminal, ex. Forex / Main; cTrader: clasa de active, ex. EU Shares / France).
//  - Le afișează ca un arbore: categoriile se deschid / închid cu un click.
//  - Căutarea filtrează lista la fiecare literă scrisă, după nume sau descriere; categoriile cu
//    rezultate apar deschise. Săgețile sus / jos aleg un rezultat, Enter îl deschide, Esc închide.
//  - Se reface automat când se schimbă sursa prețurilor (app.js apelează loadSymbols).
//
//  Folosește din app.js: $, state, store, selectSymbol.
// =====================================================================================

// Elementele HTML ale meniului: butonul din bara de sus, fereastra, căsuța de căutare,
// lista (arborele) și rândul de informații de deasupra listei.
const symBtn = $("symBtn");
const symPanel = $("symPanel");
const symSearch = $("symSearch");
const symTree = $("symTree");
const symInfo = $("symInfo");

// Simbolurile sursei active: [{ s: nume, d: descriere, g: [categorie, subcategorie...] }].
let symItems = [];
let symLoaded = false;   // a sosit lista de la server
// Categoriile deschise de utilizator (cheia = calea categoriei, ex. "Forex/Main").
// Se păstrează în browser, ca meniul să se redeschidă la fel.
const symOpen = new Set(JSON.parse(store.get("symOpen", "[]")));
// Indexul rezultatului ales cu săgețile (între rândurile de simbol afișate).
let symActive = -1;

// Cere simbolurile sursei active. Lista de sugestii a exportului (<datalist id="symbols">) se
// completează tot de aici: întâi simbolurile vizibile (Market Watch), apoi restul.
function loadSymbols() {
  fetch("/api/symbols").then((r) => r.json()).then(({ visible, all, tree }) => {
    const dl = $("symbols");
    dl.replaceChildren();
    for (const s of [...visible, ...all.filter((x) => !visible.includes(x))]) {
      const o = document.createElement("option"); o.value = s; dl.appendChild(o);
    }
    symItems = tree;
    symLoaded = tree.length > 0;
    if (!symPanel.hidden) renderSymbols();
  }).catch(() => {});
}

// Textul pentru comparare: litere mici, fără „/”, spații și puncte (ex. „EUR/USD” = „eurusd”).
const symNorm = (x) => x.toLowerCase().replace(/[\s/._-]/g, "");
// Textul sigur pentru HTML (descrierile vin de la broker).
const symEsc = (x) => x.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
// Evidențiază în `text` prima apariție a căutării `q` (fără deosebire între litere mari și mici).
function symMark(text, q) {
  const i = q ? text.toLowerCase().indexOf(q.toLowerCase()) : -1;
  if (i < 0) return symEsc(text);
  return symEsc(text.slice(0, i)) + "<mark>" + symEsc(text.slice(i, i + q.length)) + "</mark>" +
    symEsc(text.slice(i + q.length));
}

// Construiește arborele de categorii din simbolurile date (păstrând ordinea lor):
// nod = { name, key, kids: Map(nume -> nod), items: [simboluri], count }.
function symBuildTree(items) {
  const root = { kids: new Map(), items: [], count: 0 };
  for (const it of items) {
    let node = root;
    node.count++;
    let key = "";
    for (const g of it.g) {
      key = key ? `${key}/${g}` : g;
      if (!node.kids.has(g)) node.kids.set(g, { name: g, key, kids: new Map(), items: [], count: 0 });
      node = node.kids.get(g);
      node.count++;
    }
    node.items.push(it);
  }
  return root;
}

// Desenează lista: tot arborele (fără căutare) sau doar rezultatele căutării, cu categoriile lor.
function renderSymbols() {
  const raw = symSearch.value.trim();
  const q = symNorm(raw);
  if (!symLoaded) {
    symInfo.textContent = "Se încarcă simbolurile...";
    symTree.innerHTML = "";
    return;
  }
  let items = symItems;
  if (q) {
    // Potrivire în nume sau în descriere. Simbolurile al căror nume ÎNCEPE cu căutarea vin
    // primele în fiecare categorie (ex. „eur” -> EURUSD înaintea lui AUDEUR).
    items = symItems.filter((it) => symNorm(it.s).includes(q) || symNorm(it.d).includes(q));
    const rank = (it) => (symNorm(it.s).startsWith(q) ? 0 : symNorm(it.s).includes(q) ? 1 : 2);
    items = items.map((it, i) => [rank(it), i, it]).sort((a, b) => a[0] - b[0] || a[1] - b[1]).map((x) => x[2]);
  }
  symInfo.textContent = q
    ? (items.length ? `${items.length} rezultate pentru „${raw}”` : `Niciun simbol pentru „${raw}”`)
    : `${items.length} simboluri`;

  // Fără căutare, deschidem implicit categoria simbolului curent.
  const current = symItems.find((it) => it.s === state.symbol);
  const curKeys = new Set();
  if (current) current.g.reduce((k, g) => { const key = k ? `${k}/${g}` : g; curKeys.add(key); return key; }, "");

  // Construim HTML-ul recursiv: pentru fiecare categorie un rând (deschis / închis), apoi, dacă e
  // deschisă, conținutul ei; la final simbolurile nodului. depth = nivelul (pentru indentare).
  const html = [];
  const drawNode = (node, depth) => {
    for (const kid of node.kids.values()) {
      const open = q || symOpen.has(kid.key) || (!symOpen.size && curKeys.has(kid.key));
      html.push(`<button type="button" class="symgroup${open ? " open" : ""}" data-key="${symEsc(kid.key)}" ` +
        `style="--depth:${depth}" aria-expanded="${Boolean(open)}"><span class="chev">▸</span>` +
        `<span class="gname">${symEsc(kid.name)}</span><span class="gcount">${kid.count}</span></button>`);
      if (open) drawNode(kid, depth + 1);
    }
    for (const it of node.items) {
      html.push(`<button type="button" class="symrow${it.s === state.symbol ? " cur" : ""}" role="option" ` +
        `data-s="${symEsc(it.s)}" style="--depth:${depth}"><b>${symMark(it.s, raw)}</b>` +
        `<small>${symMark(it.d, raw)}</small></button>`);
    }
  };
  drawNode(symBuildTree(items), 0);
  symTree.innerHTML = html.join("");
  // La căutare, primul rezultat e ales implicit (Enter îl deschide).
  symActive = q && items.length ? 0 : -1;
  symMarkActive();
}

// Evidențiază rândul ales cu săgețile și îl aduce în zona vizibilă.
function symMarkActive() {
  const rows = symTree.querySelectorAll(".symrow");
  rows.forEach((r, i) => r.classList.toggle("active", i === symActive));
  rows[symActive]?.scrollIntoView({ block: "nearest" });
}

// Deschide meniul sub butonul simbolului.
function openSymbols() {
  symPanel.hidden = false;
  symBtn.setAttribute("aria-expanded", "true");
  placeSymbols();
  symSearch.value = "";
  renderSymbols();
  if (!symLoaded) loadSymbols();
  // Rândul simbolului curent, la vedere.
  symTree.querySelector(".symrow.cur")?.scrollIntoView({ block: "center" });
  symSearch.focus();
}
// Închide meniul.
function closeSymbols() {
  symPanel.hidden = true;
  symBtn.setAttribute("aria-expanded", "false");
}
// Pe desktop meniul stă sub buton; pe telefon ocupă lățimea ecranului (vezi style.css).
function placeSymbols() {
  const r = symBtn.getBoundingClientRect();
  const w = Math.min(420, window.innerWidth - 16);
  symPanel.style.top = `${r.bottom + 6}px`;
  symPanel.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - w - 8))}px`;
  symPanel.style.width = `${w}px`;
}

symBtn.addEventListener("click", () => (symPanel.hidden ? openSymbols() : closeSymbols()));
// Căutarea: lista se reface la fiecare literă.
symSearch.addEventListener("input", renderSymbols);
// Tastele în căsuța de căutare: săgeți = alegerea rezultatului, Enter = deschide, Esc = închide.
symSearch.addEventListener("keydown", (e) => {
  const rows = symTree.querySelectorAll(".symrow");
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    if (!rows.length) return;
    symActive = e.key === "ArrowDown" ? Math.min(symActive + 1, rows.length - 1) : Math.max(symActive - 1, 0);
    symMarkActive();
  } else if (e.key === "Enter") {
    e.preventDefault();
    const row = rows[symActive];
    if (row) { closeSymbols(); selectSymbol(row.dataset.s); }
  } else if (e.key === "Escape") {
    closeSymbols();
    symBtn.focus();
  }
});
// Click pe o categorie: o deschide / închide. Click pe un simbol: îl deschide pe chart.
symTree.addEventListener("click", (e) => {
  const group = e.target.closest(".symgroup");
  if (group) {
    if (symSearch.value.trim()) return;   // la căutare, categoriile rămân deschise
    const key = group.dataset.key;
    // Prima deschidere manuală păstrează și categoriile simbolului curent (deschise implicit).
    if (!symOpen.size) {
      for (const g of symTree.querySelectorAll(".symgroup.open")) symOpen.add(g.dataset.key);
    }
    if (symOpen.has(key)) {
      // Închidem categoria împreună cu subcategoriile ei.
      for (const k of [...symOpen]) if (k === key || k.startsWith(`${key}/`)) symOpen.delete(k);
    } else {
      symOpen.add(key);
    }
    store.set("symOpen", JSON.stringify([...symOpen]));
    renderSymbols();
    return;
  }
  const row = e.target.closest(".symrow");
  if (row) { closeSymbols(); selectSymbol(row.dataset.s); }
});
// Click în afara meniului: meniul se închide. La redimensionarea ferestrei îl repoziționăm.
document.addEventListener("pointerdown", (e) => {
  if (!symPanel.hidden && !symPanel.contains(e.target) && !symBtn.contains(e.target)) closeSymbols();
});
window.addEventListener("resize", () => { if (!symPanel.hidden) placeSymbols(); });
