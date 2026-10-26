"""
Colectorul de ticuri: rulează pe mașina virtuală (VM), lângă QuestDB, independent de server.py.
Salvează în QuestDB fiecare tic cTrader al simbolurilor din COLLECT_SYMBOLS (implicit EURUSD și
XAUUSD), live, și completează istoricul de la COLLECT_START (implicit 2026-01-01) până la prezent.

Pornire:
    python collector.py
"""
# =====================================================================================
#  CE FACE ACEST FIȘIER (pe scurt)
# -------------------------------------------------------------------------------------
#  1. Se conectează la cTrader cu CTraderFeed (ctrader.py, același cod ca serverul), cu tokenul
#     propriu al VM-ului (CTRADER_TOKEN_FILE). Tokenul NU se împarte cu PC-ul: la reînnoire,
#     tokenul vechi devine invalid, deci două programe pe același token s-ar bloca reciproc.
#  2. Live: se abonează la simboluri și trimite fiecare tic în QuestDB (tickdb.py), pe loturi.
#  3. Completarea, la pornire, la fiecare reconectare și o dată pe oră:
#       - ziua de azi: de la ultimul tic salvat până acum (golul de cât a fost oprit), în bucăți
#         de CHUNK (3 ore), ca progresul să se vadă și ticurile să apară treptat;
#       - zilele trecute, de la ieri spre COLLECT_START (cea mai nouă zi întâi, pe rând pentru
#         toate simbolurile): fiecare zi încă nemarcată în tick_coverage se ia COMPLET din
#         istoricul brokerului (bid și ask), apoi se marchează complete. O zi trecută e deci mereu
#         exact istoricul brokerului, chiar dacă live s-a pierdut ceva; ticurile live identice se
#         contopesc (DEDUP în QuestDB).
#       - Fiecare bucată / zi scrisă se verifică în QuestDB (wait_ticks) înainte de a merge mai
#         departe; o zi se marchează complete DOAR dacă ticurile ei chiar se văd în tabel. Dacă nu
#         se văd (WAL suspendat / resetat), completarea se oprește și se reia după reparare.
#       - Live-ul are prioritate: istoricul se scrie în bucăți de HISTORY_STEP rânduri și, înainte
#         de fiecare (și de fiecare zi descărcată), așteaptă ca ticurile live să se vadă în QuestDB
#         în cel mult LIVE_MAX_MS (yield_to_live). Pe un VM ocupat, istoricul încetinește, live nu.
#       - Golul recent (azi + zilele lipsă din ultimele RECENT_DAYS) se descarcă fără pauze și cu
#         progres (punctul 8); zilele mai vechi, cu pauza HISTORY_PAUSE.
#     Sâmbăta fără ticuri se marchează empty. O zi lucrătoare fără ticuri (sărbătoare sau
#     început de istoric) nu se marchează: se reîncearcă la următoarea completare. După
#     MISSING_STOP zile lucrătoare goale la rând, considerăm că istoricul brokerului s-a terminat.
#  4. Discul: sub MIN_FREE_GB liberi, completarea spre trecut se oprește (live continuă).
#  5. Lumânările oficiale cTrader (M1–W1), în tabelul bars, după regulile vechiului datastore:
#       - prima dată: ultimele STORE_BARS (10.000) lumânări închise (toate, dacă brokerul are mai puține);
#       - apoi, la închiderea fiecărei lumânări (+ BAR_DELAY): lumânările noi, plus ultimele
#         VERIFY_BARS rescrise (o lumânare corectată de broker se înlocuiește, DEDUP);
#       - sub 10.000 de lumânări: completare spre trecut, până la capătul istoricului brokerului.
#     Lumânările se iau mereu de la broker, nu se construiesc din ticuri.
#  6. Tabelul symbols: zecimalele, descrierea și categoria simbolurilor colectate (pentru server.py).
#  7. Compresia: după fiecare completare (și la fiecare COMPRESS_EVERY zile din istoricul lung),
#     zilele de ticuri mai vechi de COMPRESS_AFTER_DAYS (7), în care toate simbolurile sunt
#     complete, se convertesc în Parquet comprimat (ZSTD nivel 3, setat în server.conf): fără
#     pierderi, ~6–9 octeți pe tic în loc de ~40. Zilele rămân în tabelul ticks.
#  8. Starea, în tabelul collector_status (citită de server.py pentru bara de progres din pagină):
#     sync = recuperează golul recent (progres 0–1, de când lipsesc ticuri, cât mai durează),
#     live = la zi, error = QuestDB nu aplică scrierile. La fiecare SYNC_EVERY secunde în timpul
#     recuperării, o dată pe minut în rest (serverul știe așa și dacă colectorul s-a oprit).
#  9. Supravegherea, o dată pe minut: wal_tables() (tabel suspendat / jurnal resetat) și dacă
#     ticurile live trimise acum un minut chiar apar în QuestDB. O problemă rezolvată (ex. după
#     repararea tabelelor) pornește imediat o completare, care umple tot golul.
# 10. Resursele VM-ului: VM-ul trebuie să rămână liber și pentru altceva. Colectorul rulează cu
#     prioritate scăzută (setarea task-ului programat), iar QuestDB primește aceeași prioritate
#     (QUESTDB_PRIORITY, verificată la pornire și la fiecare PRIORITY_EVERY secunde, ca să prindă
#     și o repornire a serviciului): ce rulezi în paralel pe VM primește procesorul întâi. Istoricul
#     lung stă HISTORY_PAUSE (3) ori cât lucrează (~25% din timp activ).
# =====================================================================================
import asyncio
import logging
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

for _stream in (sys.stdout, sys.stderr):
    _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

from ctrader import PERIODS, CTraderError, CTraderFeed, TokenStore  # noqa: E402 - după load_dotenv
from tickdb import TickDB  # noqa: E402

SOURCE = "ctrader"
SYMBOLS = [s.strip() for s in os.getenv("COLLECT_SYMBOLS", "EURUSD,XAUUSD").split(",") if s.strip()]
START = datetime.fromisoformat(os.getenv("COLLECT_START", "2026-01-01")).replace(tzinfo=timezone.utc)
TOKEN_FILE = Path(os.getenv("CTRADER_TOKEN_FILE", ROOT / "ctrader_token.json"))
# Folderul cu datele QuestDB (pentru verificarea spațiului liber).
DATA_DIR = os.getenv("QUESTDB_DATA_DIR", "C:\\questdb\\data")
MIN_FREE_GB = float(os.getenv("COLLECT_MIN_FREE_GB", "3"))
DAY = 86400_000
# O zi se ia ca „completă” abia la atâtea ms după ce s-a terminat (brokerul finalizează istoricul).
SETTLE = 10 * 60_000
# Zile lucrătoare goale la rând după care considerăm că istoricul brokerului s-a terminat.
MISSING_STOP = 5
# Cât de departe în urmă căutăm ultimul bid / ask dinaintea unei zile (peste weekend).
PRIME_LOOKBACK = 4 * DAY
REFILL_EVERY = 3600
# Completarea zilei de azi, în bucăți de atâtea ms.
CHUNK = 3 * 3600_000
# Golul „recent”: azi + zilele lipsă din ultimele atâtea zile (descărcate fără pauză, cu progres).
RECENT_DAYS = 7
# Un gol recent mai mic decât atât (completarea obișnuită, la oră) nu se arată ca recuperare.
SYNC_MIN = 5 * 60_000
# Cât de des se scrie starea în timpul recuperării, respectiv în rest (punctul 8), și cât de des
# se verifică QuestDB (punctul 9).
SYNC_EVERY = 2
HEARTBEAT = 60
# Istoricul cedează live-ului (vezi yield_to_live): un tic live trebuie să se vadă în QuestDB în
# cel mult atâtea ms; istoricul se scrie în bucăți de HISTORY_STEP rânduri, verificând între ele.
LIVE_MAX_MS = 500
HISTORY_STEP = 10_000
# După fiecare zi din istoric, o pauză de HISTORY_PAUSE ori cât a durat ziua (3 = un sfert din timp
# descarcă, restul stă), ca procesorul VM-ului să nu fie ocupat tot timpul (punctul 10).
HISTORY_PAUSE = float(os.getenv("COLLECT_HISTORY_PAUSE", "3"))
# Prioritatea procesului QuestDB (java din folderul lui DATA_DIR); gol = nu se schimbă.
QUESTDB_PRIORITY = os.getenv("COLLECT_QUESTDB_PRIORITY", "BelowNormal").strip()
PRIORITY_EVERY = 600
# Zilele de ticuri mai vechi de atâtea zile (și complete) se comprimă (vezi punctul 7).
COMPRESS_AFTER_DAYS = int(os.getenv("COLLECT_COMPRESS_DAYS", "7"))
# În timpul istoricului lung, compresia rulează la fiecare atâtea zile descărcate.
COMPRESS_EVERY = 10
# Lumânări: câte păstrăm cel puțin, câte de la capăt rescriem la fiecare actualizare și cât
# așteptăm după închiderea unei lumânări înainte s-o cerem (brokerul o finalizează).
STORE_BARS = 10_000
VERIFY_BARS = 5
BAR_DELAY = 2

log = logging.getLogger("collector")


def setup_logging():
    fmt = logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S")
    file = RotatingFileHandler(ROOT / "collector.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    console = logging.StreamHandler(sys.stdout)
    for h in (file, console):
        h.setFormatter(fmt)
        log.addHandler(h)
    log.setLevel(logging.INFO)


def day_text(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


# Combină ticurile bid și ask (vin separat de la cTrader) în [timp_ms, bid, ask], la fiecare moment
# cu ultimul bid și ultimul ask cunoscute; bid și ask schimbate în aceeași milisecundă = un tic
# (aceeași regulă ca merge_ticks din server.py). last = [bid, ask] de dinaintea intervalului.
def merge(bid: list[tuple[int, float]], ask: list[tuple[int, float]], last: list) -> list[list]:
    events = sorted([(t, 0, p) for t, p in bid] + [(t, 1, p) for t, p in ask])
    out: list[list] = []
    for t, side, p in events:
        last[side] = p
        if last[0] is None or last[1] is None:
            continue
        if out and out[-1][0] == t:
            out[-1] = [t, last[0], last[1]]
        else:
            out.append([t, last[0], last[1]])
    return out


# Progresul recuperării golului recent: cât s-a făcut din total (ponderat cu timpul acoperit:
# o bucată de azi cât durează, o zi trecută DAY, sâmbăta — aproape fără ticuri — puțin).
@dataclass
class Progress:
    total: float
    gap_from: int                       # ms: de când lipsesc ticuri
    done: float = 0.0
    started: float = field(default_factory=time.monotonic)

    @property
    def ratio(self) -> float:
        return min(1.0, self.done / self.total) if self.total else 1.0

    # Secunde estimate până la capăt, după ritmul de până acum.
    def eta(self) -> int:
        r = self.ratio
        return int((time.monotonic() - self.started) * (1 - r) / r) if r > 0.02 else 0


def day_weight(day_ms: int) -> float:
    return DAY / 24 if datetime.fromtimestamp(day_ms / 1000, timezone.utc).weekday() == 5 else DAY


class Collector:
    def __init__(self):
        self.db = TickDB(SOURCE, log=log.info)
        self.feed = CTraderFeed(os.getenv("CTRADER_CLIENT_ID", "").strip(),
                                os.getenv("CTRADER_CLIENT_SECRET", "").strip(), TokenStore(TOKEN_FILE))
        self.feed.on_ticks = self.db.live
        self.feed.on_status = self._on_status
        self.wake = asyncio.Event()      # reconectare -> completăm golul
        self.connected_at = 0            # ms; de aici încolo ticurile vin live
        self.progress: Progress | None = None   # recuperarea în curs (punctul 8)
        self.problem = ""                # QuestDB nu aplică scrierile (punctul 9)
        self._status_at = 0.0
        self.yielded = 0.0               # s: cât a așteptat istoricul după live (yield_to_live)

    def _on_status(self, up: bool):
        log.info(f"[cTrader] {'conectat' if up else 'deconectat'}")
        if up:
            self.connected_at = int(time.time() * 1000)
            self.wake.set()

    # Ultimul bid / ask dinaintea momentului start_ms (ca primul tic al zilei să aibă ambele prețuri).
    async def prime(self, name: str, start_ms: int) -> list:
        last = [None, None]
        for i, side in enumerate(("BID", "ASK")):
            page = await self.feed.ticks(name, side, start_ms - PRIME_LOOKBACK, start_ms - 1, max_pages=1)
            if page:
                last[i] = page[-1][1]
        return last

    # Ticurile [start_ms, end_ms) din istoricul brokerului, combinate în [timp, bid, ask].
    async def fetch(self, name: str, start_ms: int, end_ms: int) -> list[list]:
        last = await self.prime(name, start_ms)
        bid = await self.feed.ticks(name, "BID", start_ms, end_ms - 1, max_pages=1_000_000)
        ask = await self.feed.ticks(name, "ASK", start_ms, end_ms - 1, max_pages=1_000_000)
        bid = [x for x in bid if start_ms <= x[0] < end_ms]
        ask = [x for x in ask if start_ms <= x[0] < end_ms]
        # Sortarea a sute de mii de evenimente, pe un fir separat: bucla rămâne liberă pentru
        # ticurile live (altfel ar întârzia câteva sute de ms la fiecare zi descărcată).
        return await asyncio.to_thread(merge, bid, ask, last)

    def disk_ok(self) -> bool:
        free = shutil.disk_usage(DATA_DIR).free / 1e9
        if free < MIN_FREE_GB:
            log.info(f"[DISC] doar {free:.1f} GB liberi (minimum {MIN_FREE_GB}); opresc completarea spre trecut")
            return False
        return True

    # ---------- Starea (punctul 8) ----------
    async def publish(self, force: bool = False):
        now = time.monotonic()
        if not force and now - self._status_at < SYNC_EVERY:
            return
        self._status_at = now
        p = self.progress
        if self.problem:
            args = ("error", p.ratio if p else 0.0, p.gap_from if p else None, 0, self.problem)
        elif p:
            args = ("sync", p.ratio, p.gap_from, p.eta(), "")
        else:
            args = ("live", 1.0, None, 0, "")
        try:
            await asyncio.to_thread(self.db.write_status, *args)
        except Exception as e:  # noqa: BLE001 - QuestDB indisponibil: data viitoare
            log.info(f"[STARE] nu s-a putut scrie: {type(e).__name__}: {e}")

    async def advance(self, weight: float):
        if self.progress:
            self.progress.done += weight
            await self.publish()

    # ---------- Completarea (punctul 3) ----------
    # Scrie ticurile și așteaptă să se vadă în QuestDB (altfel RuntimeError: nu marcăm nimic).
    # Se scrie în bucăți de HISTORY_STEP rânduri; înainte de fiecare, live-ul trebuie să fie la zi.
    async def store(self, name: str, lo: int, hi: int, rows: list[list]):
        for i in range(0, len(rows), HISTORY_STEP):
            await self.yield_to_live()
            await asyncio.to_thread(self.db.write_rows, name, rows[i:i + HISTORY_STEP])
        if rows:
            await asyncio.to_thread(self.db.wait_ticks, name, lo, hi, len(rows))

    # Prioritatea live-ului: cât timp ticurile live trimise acum LIVE_MAX_MS nu se văd încă în
    # QuestDB (VM-ul e ocupat), istoricul așteaptă. Timpul așteptat se adună în self.yielded.
    async def yield_to_live(self):
        started = time.monotonic()
        while not await asyncio.to_thread(self.db.live_ok, LIVE_MAX_MS):
            await asyncio.sleep(0.5)
        self.yielded += time.monotonic() - started

    # Golul zilei de azi pentru un simbol: începutul lui și ultimul tic salvat înainte de
    # conectare (pentru „de când lipsesc ticuri”). Ticurile live de după conectare nu contează.
    async def today_gap(self, name: str, now: int) -> tuple[int, int | None]:
        today = now // DAY * DAY
        last = await asyncio.to_thread(self.db.last_tick, name, self.connected_at or now)
        return (today if last is None or last < today else last - 60_000), last

    async def fill_today(self, name: str, start: int, now: int):
        started, total = time.monotonic(), 0
        for lo in range(start, now, CHUNK):
            hi = min(lo + CHUNK, now)
            rows = await self.fetch(name, lo, hi)
            await self.store(name, lo, hi, rows)
            total += len(rows)
            await self.advance(hi - lo)
        log.info(f"[AZI] {name}: {total} ticuri de la {datetime.fromtimestamp(start / 1000, timezone.utc):%H:%M:%S} "
                 f"UTC ({time.monotonic() - started:.0f}s)")

    # O zi trecută, completă. Întoarce False dacă ziua n-are ticuri (și nu e sâmbătă).
    async def fill_day(self, name: str, day: int, recent: bool) -> bool:
        started = time.monotonic()
        await self.yield_to_live()
        rows = await self.fetch(name, day, day + DAY)
        weekday = datetime.fromtimestamp(day / 1000, timezone.utc).weekday()
        if rows:
            await self.store(name, day, day + DAY, rows)
            await asyncio.to_thread(self.db.mark_day, name, day, "complete", len(rows))
            took = time.monotonic() - started
            log.info(f"[ISTORIC] {name} {day_text(day)}: {len(rows)} ticuri ({took:.0f}s)")
            # Pauză după fiecare zi veche: VM-ul (un singur nucleu) rămâne liber pentru ticurile
            # live, pe care serverul le citește la fiecare 50 ms. Golul recent merge fără pauză.
            if not recent:
                await asyncio.sleep(took * HISTORY_PAUSE)
            return True
        if weekday == 5:   # sâmbătă: piața e închisă
            await asyncio.to_thread(self.db.mark_day, name, day, "empty", 0)
            return True
        log.info(f"[ISTORIC] {name} {day_text(day)}: fără ticuri (sărbătoare sau început de istoric)")
        return False

    async def fill(self, names: list[str]):
        problems = await asyncio.to_thread(self.db.wal_problems)
        if problems:
            raise RuntimeError("QuestDB: " + "; ".join(problems))
        now = int(time.time() * 1000)
        today = now // DAY * DAY
        recent_from = today - RECENT_DAYS * DAY
        stop = int(START.timestamp() * 1000)
        # Planul: golul de azi al fiecărui simbol și zilele trecute nemarcate.
        gaps, lasts, missing = {}, [], {}
        for name in names:
            gaps[name], last = await self.today_gap(name, now)
            if last is not None:
                lasts.append(last)
            done = await asyncio.to_thread(self.db.done_days, name)
            missing[name] = [d for d in range(today - DAY, stop - 1, -DAY)
                             if d not in done and d + DAY + SETTLE <= now]
        recent_days = [d for days in missing.values() for d in days if d >= recent_from]
        total = sum(now - g for g in gaps.values()) + sum(day_weight(d) for d in recent_days)
        if total >= SYNC_MIN:
            gap_from = max(min(lasts + recent_days + list(gaps.values())), recent_from)
            self.progress = Progress(total, gap_from)
            log.info(f"[RECUPERARE] lipsesc ticuri de la "
                     f"{datetime.fromtimestamp(gap_from / 1000, timezone.utc):%Y-%m-%d %H:%M} UTC")
            await self.publish(force=True)
        try:
            # Întâi prezentul pentru toate simbolurile, apoi trecutul, cea mai nouă zi întâi.
            for name in names:
                await self.fill_today(name, gaps[name], now)
            empty = {name: 0 for name in names}
            days = sorted({d for days in missing.values() for d in days}, reverse=True)
            old = [d for d in days if d < recent_from]
            old_started, old_done = time.monotonic(), 0
            for day in days:
                recent = day >= recent_from
                if not recent and self.progress:
                    await self.finish_progress()
                # Istoricul lung: la fiecare COMPRESS_EVERY zile, comprimăm ce s-a terminat (discul
                # nu ține zeci de zile necomprimate) și scriem în log cât a rămas.
                if not recent and old_done and old_done % COMPRESS_EVERY == 0:
                    await self.compress(names)
                    rate = (time.monotonic() - old_started) / old_done
                    log.info(f"[ISTORIC] progres: {old_done} / {len(old)} zile (până la {day_text(day)}), "
                             f"~{rate:.0f}s pe zi, mai sunt ~{rate * (len(old) - old_done) / 3600:.1f} h; "
                             f"așteptat după live: {self.yielded:.0f}s în total")
                for name in names:
                    if day not in missing[name] or empty[name] >= MISSING_STOP:
                        continue
                    if not recent and not self.disk_ok():
                        await self.compress(names)
                        return
                    if await self.fill_day(name, day, recent):
                        empty[name] = 0
                    else:
                        empty[name] += 1
                        if empty[name] >= MISSING_STOP:
                            log.info(f"[ISTORIC] {name}: {MISSING_STOP} zile lucrătoare goale la rând; istoricul "
                                     f"brokerului pare să înceapă după {day_text(day)}")
                    if recent:
                        await self.advance(day_weight(day))
                if not recent:
                    old_done += 1
            if self.progress:
                await self.finish_progress()
        finally:
            # Oprită de o eroare: starea nu mai e „sync” (eroarea o anunță run / monitor).
            if self.progress:
                self.progress = None
                await self.publish(force=True)
        log.info("[COMPLETARE] gata")
        await self.compress(names)

    async def finish_progress(self):
        took = time.monotonic() - self.progress.started
        self.progress = None
        log.info(f"[RECUPERARE] golul recent e complet ({took:.0f}s)")
        await self.publish(force=True)

    # Punctul 7: comprimă zilele vechi și complete (o singură comandă pentru toate).
    async def compress(self, names: list[str]):
        try:
            before = int(time.time() * 1000) - COMPRESS_AFTER_DAYS * DAY
            days = await asyncio.to_thread(self.db.compressible_days, names, before)
            if not days:
                return
            started = time.monotonic()
            await asyncio.to_thread(self.db.compress_days, days)
            log.info(f"[COMPRESIE] {len(days)} zile convertite în Parquet ZSTD ({days[0]} … {days[-1]}), "
                     f"{time.monotonic() - started:.0f}s")
        except Exception as e:  # noqa: BLE001 - reîncercăm la următoarea completare
            log.info(f"[COMPRESIE] eșuată: {type(e).__name__}: {e}")

    # ---------- Simboluri ----------
    # Zecimalele, descrierea și categoria simbolurilor colectate, în tabelul symbols.
    async def save_symbols(self, names: list[str]):
        groups = {it["s"]: " / ".join(it["g"]) for it in self.feed.symbol_tree()}
        items = []
        for name in names:
            info = await self.feed.info(name)
            items.append({"name": name, "digits": info["digits"], "description": info["description"],
                          "category": groups.get(name, "")})
        await asyncio.to_thread(self.db.write_symbols, items)

    # ---------- Lumânări oficiale ----------
    # Aduce la zi seria (simbol, tf) din bars (regulile de la punctul 5). Întoarce starea nouă.
    async def sync_bars(self, name: str, tf: int, st: dict | None) -> dict:
        now = time.time()
        end = int(now * 1000) + 1

        def closed(bars):
            return [b for b in bars if b["time"] + tf <= now]
        if st is None:
            # Prima dată: ultimele STORE_BARS închise (+1: lumânarea în curs, care se scoate).
            bars = closed(await self.feed.trendbars(name, tf, STORE_BARS + 1, end))[-STORE_BARS:]
            if not bars:
                raise ConnectionError(f"brokerul nu are lumânări {PERIODS[tf]} pentru {name}")
            st = {"bars": len(bars), "first": bars[0]["time"], "last": bars[-1]["time"],
                  "complete": len(bars) < STORE_BARS}
            log.info(f"[LUMÂNĂRI] {name} {PERIODS[tf]}: descărcate {len(bars)}")
        else:
            # Ultimele VERIFY_BARS (rescrise) + cele noi.
            bars = closed(await self.feed.bars_range(name, tf, (st["last"] - VERIFY_BARS * tf) * 1000, end))
            added = len([b for b in bars if b["time"] > st["last"]])
            st = dict(st, bars=st["bars"] + added, last=max([st["last"]] + [b["time"] for b in bars]))
            if added > 1:
                log.info(f"[LUMÂNĂRI] {name} {PERIODS[tf]}: +{added}")
        # Completare spre trecut, până la STORE_BARS (sau până la capătul istoricului brokerului).
        if st["bars"] < STORE_BARS and not st["complete"]:
            need = STORE_BARS - st["bars"]
            older = [b for b in await self.feed.trendbars(name, tf, need, st["first"] * 1000)
                     if b["time"] < st["first"]]
            if older:
                bars = older + bars
                st = dict(st, bars=st["bars"] + len(older), first=older[0]["time"])
                log.info(f"[LUMÂNĂRI] {name} {PERIODS[tf]}: +{len(older)} mai vechi")
            st["complete"] = len(older) < need
        if bars:
            await asyncio.to_thread(self.db.write_bars, name, tf, bars)
        await asyncio.to_thread(self.db.mark_bars, name, tf, st["bars"], st["first"], st["last"], st["complete"])
        return st

    # Ține la zi toate seriile: la fiecare minut (+ BAR_DELAY) actualizează seriile în care s-a
    # închis o lumânare nouă de la ultima salvată; o dată pe oră, pe toate (D1 / W1 se mută cu o
    # oră la trecerea pe ora de vară / iarnă).
    async def keep_bars(self, names: list[str]):
        state: dict[tuple[str, int], dict | None] = {}
        while not state:
            try:
                for name in names:
                    for tf in PERIODS:
                        state[(name, tf)] = await asyncio.to_thread(self.db.bar_status, name, tf)
            except Exception as e:  # noqa: BLE001 - QuestDB indisponibil: reîncercăm
                log.info(f"[LUMÂNĂRI] starea seriilor nu s-a putut citi: {type(e).__name__}: {e}")
                state.clear()
                await asyncio.sleep(10)
        last_full = 0.0
        while True:
            await self.feed.ready.wait()
            now = time.time()
            full = now - last_full >= 3600
            for key, st in state.items():
                name, tf = key
                if not (full or st is None or now >= st["last"] + 2 * tf):
                    continue
                try:
                    state[key] = await self.sync_bars(name, tf, st)
                except (ConnectionError, CTraderError, asyncio.TimeoutError) as e:
                    log.info(f"[LUMÂNĂRI] {name} {PERIODS[tf]}: {type(e).__name__}: {e}")
                except Exception as e:  # noqa: BLE001 - QuestDB / rețea: reîncercăm la minutul următor
                    log.info(f"[LUMÂNĂRI] {name} {PERIODS[tf]}: eroare {type(e).__name__}: {e}")
            if full:
                last_full = now
            # Până la începutul minutului următor + BAR_DELAY.
            await asyncio.sleep(60 - time.time() % 60 + BAR_DELAY)

    async def stats(self):
        while True:
            await asyncio.sleep(600)
            log.info(f"[LIVE] în coadă {self.db.pending()}, aruncate {self.db.dropped}")

    # Punctul 9: o dată pe minut, dacă QuestDB chiar aplică ce scriem; plus starea (punctul 8).
    async def monitor(self):
        while True:
            await asyncio.sleep(HEARTBEAT)
            try:
                problems = await asyncio.to_thread(self.db.wal_problems)
                now = time.time()
                for sym, (t, sent) in list(self.db.flushed.items()):
                    if 30 < now - sent < 600 and not await asyncio.to_thread(self.db.tick_count, sym, t, t + 1):
                        problems.append(f"ticks: ticurile live {sym} trimise acum {now - sent:.0f} s nu apar în QuestDB")
            except Exception as e:  # noqa: BLE001 - QuestDB indisponibil: verificăm data viitoare
                log.info(f"[VERIFICARE] QuestDB nu răspunde: {type(e).__name__}: {e}")
                continue
            text = "; ".join(problems)
            if text != self.problem:
                if text:
                    log.info(f"[VERIFICARE] QuestDB NU aplică scrierile: {text}")
                else:
                    log.info("[VERIFICARE] QuestDB aplică din nou scrierile; completez golul")
                    self.wake.set()
                self.problem = text
            await self.publish(force=True)

    async def run(self):
        while True:
            try:
                await asyncio.to_thread(self.db.ensure_tables)
                break
            except Exception as e:  # noqa: BLE001 - QuestDB încă pornește
                log.info(f"[QUESTDB] indisponibil ({type(e).__name__}: {e}); reîncerc în 10 s")
                await asyncio.sleep(10)
        self.db.start()
        feed_task = asyncio.create_task(self.feed.run())
        asyncio.create_task(self.stats())
        asyncio.create_task(self.monitor())
        log.info(f"[START] simboluri {', '.join(SYMBOLS)}, istoric de la {START:%Y-%m-%d}, QuestDB {self.db.host}")
        bars_task = None
        try:
            while True:
                await self.feed.ready.wait()
                names = []
                for s in SYMBOLS:
                    name = self.feed.resolve(s)
                    if name is None:
                        log.info(f"[cTrader] simbolul {s} nu există la acest broker")
                    else:
                        names.append(name)
                self.feed.want(set(names))
                self.wake.clear()
                try:
                    await self.save_symbols(names)
                    if bars_task is None:
                        bars_task = asyncio.create_task(self.keep_bars(names))
                    await self.fill(names)
                except (ConnectionError, CTraderError, asyncio.TimeoutError) as e:
                    log.info(f"[COMPLETARE] întreruptă: {type(e).__name__}: {e}")
                except Exception as e:  # noqa: BLE001 - QuestDB / rețea: reîncercăm mai târziu
                    log.info(f"[COMPLETARE] eroare: {type(e).__name__}: {e}")
                    if str(e).startswith("QuestDB"):
                        self.problem = str(e)
                        await self.publish(force=True)
                # Următoarea completare: la o reconectare, când QuestDB își revine (punctul 9) sau
                # peste o oră (ziua de ieri devine completă).
                try:
                    await asyncio.wait_for(self.wake.wait(), REFILL_EVERY)
                except asyncio.TimeoutError:
                    pass
        finally:
            feed_task.cancel()


if __name__ == "__main__":
    setup_logging()
    try:
        asyncio.run(Collector().run())
    except KeyboardInterrupt:
        pass
