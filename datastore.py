"""
Datastore local: lumânările ÎNCHISE ale fiecărei perechi sursă / simbol / timeframe (M1–W1), pe disc.
Indicatorii și chartul (lumânările închise) citesc doar de aici, prin load_bars, ca să vadă
garantat aceleași date.
"""
# =====================================================================================
#  CE FACE ACEST FIȘIER (pe scurt)
# -------------------------------------------------------------------------------------
#  Structura pe disc (aceeași ca la exporturile CSV, orele în UTC real):
#
#    datastore/
#      ctrader/                     <- sursa prețurilor (cTrader și MT5 au prețuri / ore diferite)
#        EURUSD/                    <- simbolul
#          M1/                      <- timeframe-ul
#            EURUSD_M1.csv          <- time_utc,open,high,low,close,volume (cronologic, fără goluri)
#            info.json              <- descrierea fișierului (perioada, câte lumânări, starea)
#
#  Regulile unui fișier (Store.sync le aplică pe toate, la fiecare sincronizare):
#   1. Prima descărcare: ultimele STORE_BARS (10.000) lumânări închise; dacă sursa are mai puține,
#      toate câte are (istoric complet).
#   2. Actualizare: lumânările noi, închise, se ADAUGĂ la final. Cele vechi nu se șterg niciodată,
#      deci fișierul crește peste 10.000. Golul dintre ultima lumânare salvată și „acum” se cere
#      de la sursă dintr-o dată (pe bucăți, cu progres), deci fișierul nu are goluri.
#   3. Verificare: ultimele VERIFY_BARS lumânări din fișier se compară cu sursa. O lumânare
#      corectată de broker (sau salvată prea devreme) se rescrie; dacă nici prima lumânare
#      verificată nu se potrivește (ex. ore decalate), fișierul se reface de la zero.
#   4. Completare spre trecut: dacă fișierul are sub 10.000 de lumânări și sursa are istoric mai
#      vechi, se adaugă la început până la 10.000.
#  Se salvează doar lumânările închise (time + tf <= acum): cea în curs se schimbă la fiecare tic.
#  Lumânarea oficială e cerută de la sursă după închidere; nu o construim din ticurile live (un tic
#  pierdut la o reconectare ar strica maximul sau minimul).
# =====================================================================================
import asyncio
import csv
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

# Câte lumânări (cele mai noi) are cel puțin fiecare fișier, dacă sursa le are.
STORE_BARS = 10_000
# Câte lumânări de la capătul fișierului se verifică la fiecare sincronizare.
VERIFY_BARS = 5
# Lumânări cerute într-o bucată la descărcare (după fiecare bucată se raportează progresul).
CHUNK = 2000
# Dacă sursa nu are 10.000 de lumânări, reîncercăm completarea spre trecut cel mult o dată pe zi
# (ex. terminalul MT5 primește între timp mai mult istoric).
HISTORY_RETRY = 86400
CSV_HEADER = ["time_utc", "open", "high", "low", "close", "volume"]
# Versiunea regulilor de mai sus, scrisă în info.json. Un fișier fără ea (salvat de codul vechi:
# „un an de pe chart”, ore MT5 cu decalajul de acum) nu e de încredere și se descarcă din nou.
FORMAT = 2
# Numele timeframe-urilor salvate (secunde -> eticheta din numele fișierului / folderului).
LABELS = {60: "M1", 300: "M5", 900: "M15", 1800: "M30", 3600: "H1", 14400: "H4", 86400: "D1",
          604800: "W1"}


# Secunde UTC -> text, forma din CSV: 1767225600 -> „2026-01-01 00:00:00”.
def utc_text(t: int) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# Invers: textul din CSV -> secunde UTC.
def utc_seconds(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp())


class Store:
    """Fișierul unei perechi sursă / simbol / timeframe, cu lumânările ținute și în memorie."""

    def __init__(self, root: Path, source: str, name: str, tf: int):
        # Numele simbolului fără caractere interzise în numele de fișiere (ex. „US30/USD” -> „US30_USD”).
        safe = re.sub(r"[^\w.-]", "_", name)
        self.source, self.name, self.tf, self.label = source, name, tf, LABELS[tf]
        self.dir = root / source / safe / self.label
        self.path = self.dir / f"{safe}_{self.label}.csv"
        self.info_path = self.dir / "info.json"
        # Un singur lock pentru citire și scriere: nimeni nu citește un fișier pe jumătate scris.
        self.lock = asyncio.Lock()
        self.bars: list[dict] | None = None   # încărcate la prima nevoie (vezi _ensure_loaded)
        self.meta: dict = {}                  # conținutul info.json
        self.digits = 5
        self._added = 0                       # lumânări adăugate la ultima sincronizare (info.json)
        # Crește la fiecare schimbare a lumânărilor (cheia cache-ului de indicatori din server.py).
        self.version = 0
        # Ultima stare, trimisă paginilor (bara de progres de pe chart).
        self.status: dict = {"state": "idle"}

    # ---------- Disc ----------
    # Citește CSV-ul și info.json de pe disc. Fișier lipsă = listă goală (urmează prima descărcare).
    def _read(self) -> tuple[list[dict], dict]:
        bars: list[dict] = []
        if self.path.exists():
            with self.path.open(newline="", encoding="utf-8") as f:
                rows = csv.reader(f)
                next(rows, None)   # sărim rândul cu numele coloanelor
                for r in rows:
                    if len(r) < 6:   # rând incomplet (ex. gol la final): îl ignorăm
                        continue
                    bars.append({"time": utc_seconds(r[0]), "open": float(r[1]), "high": float(r[2]),
                                 "low": float(r[3]), "close": float(r[4]),
                                 "volume": int(r[5]) if r[5] else 0})
        try:
            meta = json.loads(self.info_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}
        return bars, meta

    # Lumânări -> rânduri CSV, cu prețurile scrise cu exact `digits` zecimale (ex. 1.08623).
    def _rows(self, bars: list[dict]) -> list[list]:
        d = self.digits
        return [[utc_text(b["time"]), f"{b['open']:.{d}f}", f"{b['high']:.{d}f}", f"{b['low']:.{d}f}",
                 f"{b['close']:.{d}f}", b.get("volume", 0)] for b in bars]

    # Rescrie tot fișierul (prima descărcare, completare spre trecut, corecturi). Întâi .tmp, apoi
    # redenumire: fișierul nu rămâne niciodată pe jumătate scris.
    def _write_all(self, bars: list[dict]):
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(CSV_HEADER)
            w.writerows(self._rows(bars))
        tmp.replace(self.path)

    # Adaugă lumânări noi la finalul fișierului (modul „a” = append), fără să-l rescrie.
    def _append(self, bars: list[dict]):
        with self.path.open("a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(self._rows(bars))

    # info.json se scrie din memorie (nu recitim fișierul CSV, care la M1 poate avea zeci de MB).
    def _write_info(self, added: int):
        bars = self.bars or []
        now = int(time.time())
        self.meta.update({
            "source": self.source, "symbol": self.name, "timeframe": self.label, "seconds": self.tf,
            "format": FORMAT, "digits": self.digits, "columns": CSV_HEADER, "timezone": "UTC",
            "first_bar": utc_text(bars[0]["time"]) if bars else "",
            "last_bar": utc_text(bars[-1]["time"]) if bars else "",
            "bars": len(bars), "target": STORE_BARS,
            "created": self.meta.get("created") or utc_text(now),
            "updated": utc_text(now), "last_added": added,
        })
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.info_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.meta, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.info_path)

    # Încarcă fișierul în memorie, o singură dată (citirea de pe disc rulează într-un fir separat).
    async def _ensure_loaded(self):
        if self.bars is None:
            self.bars, self.meta = await asyncio.to_thread(self._read)
            self.digits = self.meta.get("digits", self.digits)

    # Eliberează memoria când nicio pagină nu mai are deschisă perechea (fișierul rămâne).
    async def unload(self):
        async with self.lock:
            self.bars = None

    # ---------- Citire (pentru chart și indicatori) ----------
    # Ultimele n lumânări (sau toate, fără n). Întoarce o copie, ca apelantul să nu modifice lista.
    async def load_bars(self, n: int | None = None) -> list[dict]:
        async with self.lock:
            await self._ensure_loaded()
            return list(self.bars[-n:] if n else self.bars)

    # Cel mult n lumânări deschise înainte de `before` (secunde), pentru derularea chartului.
    async def load_before(self, before: int, n: int) -> list[dict]:
        async with self.lock:
            await self._ensure_loaded()
            bars = self.bars
            lo, hi = 0, len(bars)
            while lo < hi:                       # căutare binară: prima lumânare cu time >= before
                mid = (lo + hi) // 2
                if bars[mid]["time"] < before:
                    lo = mid + 1
                else:
                    hi = mid
            return bars[max(0, lo - n):lo]

    # Ora deschiderii ultimei lumânări salvate (sau None dacă fișierul e gol / neîncărcat).
    def last_time(self) -> int | None:
        return self.bars[-1]["time"] if self.bars else None

    # ---------- Progres ----------
    # Procentul bazei de date: lumânări salvate / 10.000 (100% și cu mai puține, dacă sursa nu
    # are mai mult istoric).
    def _pct(self, count: int) -> float:
        if count >= STORE_BARS or self.meta.get("history_complete"):
            return 1.0
        return count / STORE_BARS

    # Actualizează starea (self.status) și, dacă există, o trimite prin report() către pagini.
    # state: idle / syncing / ready / error; phase: download / rebuild / update / history;
    # progress: cât din pasul curent s-a făcut (0..1).
    def _set(self, report, state: str, phase: str = "", progress: float = 1.0, count: int | None = None,
             error: str = ""):
        bars = self.bars or []
        count = len(bars) if count is None else count
        self.status = {
            "state": state, "phase": phase, "progress": round(progress, 4),
            "pct": round(self._pct(count), 4), "bars": count, "target": STORE_BARS,
            "complete": bool(self.meta.get("history_complete")),
            "first": bars[0]["time"] if bars else None, "last": bars[-1]["time"] if bars else None,
            "file": self.path.as_posix(), "error": error,
        }
        if report:
            report(dict(self.status))

    # ---------- Sincronizare ----------
    async def sync(self, feed, digits: int, report=None) -> bool:
        """Aduce fișierul la zi (regulile 1–4 de sus). Întoarce True dacă s-a schimbat ceva."""
        async with self.lock:
            self.digits = digits
            self._added = 0
            try:
                await self._ensure_loaded()
                # Fișier salvat după reguli vechi -> îl descărcăm din nou; fișier gol -> prima
                # descărcare; altfel -> verificare + actualizare.
                if self.bars and self.meta.get("format") != FORMAT:
                    print(f"[DATASTORE] {self.source}/{self.name}/{self.label}: fișier în formatul vechi; "
                          "îl descarc din nou")
                    self.bars, self.meta = [], {}
                    changed = await self._download(feed, report, phase="rebuild")
                elif not self.bars:
                    changed = await self._download(feed, report)
                else:
                    changed = await self._update(feed, report)
                # Regula 4, dacă fișierul are încă sub 10.000 de lumânări.
                if self.bars and len(self.bars) < STORE_BARS and self._history_due():
                    changed = await self._backfill(feed, report) or changed
                if changed:
                    self.version += 1
                if changed or not self.info_path.exists():
                    await asyncio.to_thread(self._write_info, self._added)
                self._set(report, "ready")
                return changed
            except Exception as e:  # noqa: BLE001 - pagina primește eroarea ca text
                self._set(report, "error", error=str(e) or type(e).__name__)
                raise

    # Doar lumânările închise: deschiderea + durata timeframe-ului a trecut deja.
    def _closed(self, bars: list[dict]) -> list[dict]:
        now = time.time()
        return [b for b in bars if b["time"] + self.tf <= now]

    # E cazul să încercăm completarea spre trecut? Da, dacă nu știm că istoricul sursei s-a
    # terminat, sau dacă ultima verificare a fost acum mai mult de HISTORY_RETRY (o zi).
    def _history_due(self) -> bool:
        if not self.meta.get("history_complete"):
            return True
        return time.time() - self.meta.get("history_checked", 0) > HISTORY_RETRY

    # Regula 1: ultimele STORE_BARS lumânări închise, cerute de la prezent spre trecut, pe bucăți.
    async def _download(self, feed, report, phase: str = "download") -> bool:
        got: list[dict] = []
        end = int(time.time() * 1000) + 1
        complete = False
        self._set(report, "syncing", phase, 0.0, count=0)
        while len(got) < STORE_BARS:
            need = min(CHUNK, STORE_BARS - len(got))
            # +1: prima bucată conține de obicei și lumânarea în curs, pe care o scoatem.
            chunk = await feed.trendbars(self.name, self.tf, need + (0 if got else 1), end)
            chunk = self._rounded(self._closed(chunk))
            # Fără dubluri: din bucata nouă păstrăm doar ce e mai vechi decât ce avem deja.
            if got:
                chunk = [b for b in chunk if b["time"] < got[0]["time"]]
            got = chunk + got
            self._set(report, "syncing", phase, min(1.0, len(got) / STORE_BARS), count=len(got))
            if len(chunk) < need:
                complete = True      # sursa nu mai are istoric
                break
            end = got[0]["time"] * 1000   # următoarea bucată: înaintea celei mai vechi lumânări
        got = got[-STORE_BARS:]
        if not got:
            raise ConnectionError(f"sursa nu are lumânări {self.label} pentru {self.name}")
        await asyncio.to_thread(self._write_all, got)
        self.bars = got
        self.meta["history_complete"] = complete
        self.meta["history_checked"] = int(time.time())
        self._added = len(got)
        print(f"[DATASTORE] {self.source}/{self.name}/{self.label}: descărcate {len(got)} lumânări"
              + (" (tot istoricul sursei)" if complete else ""))
        return True

    # Toate lumânările închise de la start_ms încoace, cerute pe bucăți (progresul = cât din
    # interval s-a acoperit). Folosită pentru golul dintre fișier și „acum”.
    async def _fetch_since(self, feed, start_ms: int, report) -> list[dict]:
        end = int(time.time() * 1000) + 1
        step = self.tf * 1000 * CHUNK   # durata unei bucăți de CHUNK lumânări, în ms
        out: list[dict] = []
        t = start_ms
        big = end - start_ms > step     # progres doar dacă e nevoie de mai multe bucăți
        while t < end:
            to = min(t + step, end)
            out += await feed.bars_range(self.name, self.tf, t, to)
            t = to
            if big:
                self._set(report, "syncing", "update", (t - start_ms) / (end - start_ms))
        return self._closed(out)

    # Două lumânări sunt identice: aceeași oră, același volum și aceleași prețuri.
    @staticmethod
    def _same(a: dict, b: dict) -> bool:
        return (a["time"] == b["time"] and a["volume"] == b["volume"]
                and all(abs(a[k] - b[k]) < 1e-9 for k in ("open", "high", "low", "close")))

    # Prețurile rotunjite la zecimalele simbolului (ca în fișier), ca să le putem compara exact.
    def _rounded(self, bars: list[dict]) -> list[dict]:
        d = self.digits
        return [{"time": b["time"], "open": round(b["open"], d), "high": round(b["high"], d),
                 "low": round(b["low"], d), "close": round(b["close"], d),
                 "volume": int(b.get("volume", 0))} for b in bars]

    # Regulile 2 și 3: verifică ultimele lumânări și adaugă ce lipsește până la zi.
    async def _update(self, feed, report) -> bool:
        bars = self.bars
        k = min(VERIFY_BARS, len(bars))
        tail = bars[-k:]   # ultimele k lumânări salvate (cele verificate)
        # De la sursă: tot de la prima lumânare verificată până acum.
        fresh = self._rounded(await self._fetch_since(feed, tail[0]["time"] * 1000, report))
        last = bars[-1]["time"]
        overlap = [b for b in fresh if b["time"] <= last]   # lumânări pe care le avem deja
        newer = [b for b in fresh if b["time"] > last]      # lumânări noi
        # Cele de verificat diferă de sursă (sau lipsesc unele)?
        if overlap and not (len(overlap) == k and all(map(self._same, overlap, tail))):
            if not self._same(overlap[0], tail[0]):
                # Nici prima lumânare verificată nu se potrivește: datele vechi nu sunt de încredere
                # (ex. ore decalate). Refacem fișierul de la zero.
                print(f"[DATASTORE] {self.source}/{self.name}/{self.label}: lumânările salvate nu se "
                      f"potrivesc cu sursa ({utc_text(tail[0]['time'])}); refac fișierul")
                self.bars = []
                self.meta.pop("history_complete", None)
                return await self._download(feed, report, phase="rebuild")
            # Doar capătul diferă (lumânare corectată / salvată prea devreme): îl rescriem.
            fixed = bars[:-k] + fresh
            await asyncio.to_thread(self._write_all, fixed)
            self.bars = fixed
            self._added = len(newer)
            print(f"[DATASTORE] {self.source}/{self.name}/{self.label}: corectat capătul "
                  f"(de la {utc_text(tail[0]['time'])}), +{len(newer)} lumânări")
            return True
        # Capătul e corect: doar adăugăm lumânările noi (dacă există).
        if not newer:
            return False
        await asyncio.to_thread(self._append, newer)
        bars.extend(newer)
        self._added = len(newer)
        if len(newer) > 1:
            print(f"[DATASTORE] {self.source}/{self.name}/{self.label}: +{len(newer)} lumânări")
        return True

    # Regula 4: completează spre trecut până la STORE_BARS (sau până la capătul istoricului sursei).
    async def _backfill(self, feed, report) -> bool:
        got: list[dict] = []
        first = self.bars[0]["time"]
        complete = False
        self._set(report, "syncing", "history", 0.0)
        missing = STORE_BARS - len(self.bars)
        while len(got) < missing:
            need = min(CHUNK, missing - len(got))
            # Cerem lumânările dinaintea celei mai vechi pe care o avem (din fișier sau din got).
            end = (got[0]["time"] if got else first) * 1000
            chunk = [b for b in await feed.trendbars(self.name, self.tf, need, end) if b["time"] * 1000 < end]
            got = self._rounded(chunk) + got
            self._set(report, "syncing", "history", len(got) / missing, count=len(self.bars) + len(got))
            if len(chunk) < need:
                complete = True
                break
        self.meta["history_complete"] = complete
        self.meta["history_checked"] = int(time.time())
        if not got:
            return False
        bars = got + self.bars
        await asyncio.to_thread(self._write_all, bars)
        self.bars = bars
        self._added += len(got)
        print(f"[DATASTORE] {self.source}/{self.name}/{self.label}: +{len(got)} lumânări mai vechi"
              + (" (tot istoricul sursei)" if complete else ""))
        return True


class Datastore:
    """Toate fișierele datastore-ului; câte un obiect Store pentru fiecare pereche folosită."""

    def __init__(self, root: Path):
        self.root = root
        self.stores: dict[tuple[str, str, int], Store] = {}

    # Store-ul pentru sursa / simbolul / timeframe-ul dat; îl creează la prima cerere, apoi îl
    # refolosește (același obiect = același lock și aceleași lumânări în memorie).
    def get(self, source: str, name: str, tf: int) -> Store:
        key = (source, name, tf)
        if key not in self.stores:
            self.stores[key] = Store(self.root, source, name, tf)
        return self.stores[key]
