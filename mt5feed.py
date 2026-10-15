"""
Sursă de prețuri din MetaTrader 5 (ex. FBS): terminalul MT5 trebuie să fie pornit și logat pe PC.

Are aceeași interfață ca CTraderFeed (ctrader.py), ca server.py să poată folosi oricare dintre ele
(se alege cu PRICE_SOURCE în .env).
"""
# =====================================================================================
#  CE FACE ACEST FIȘIER (pe scurt)
# -------------------------------------------------------------------------------------
#  - Se atașează la terminalul MT5 deschis pe PC (mt5.initialize nu are nevoie de parolă).
#  - Un fir de execuție separat („thread”) întreabă terminalul la fiecare 10 ms dacă au apărut
#    ticuri noi pentru simbolurile urmărite și le trimite serverului.
#  - Oferă istoricul (ticuri, lumânări) și informațiile simbolurilor, la cerere.
#
#  Ora: MT5 dă timpii în ora SERVERULUI brokerului (la FBS UTC+2 iarna, UTC+3 vara), scrisă ca
#  și cum ar fi UTC. Restul aplicației lucrează în UTC real, așa că scădem diferența (o aflăm
#  comparând cel mai recent tic cu ora reală, vezi _estimate_offset).
# =====================================================================================
import asyncio
import os
import threading
import time

import MetaTrader5 as mt5

# Cât de des interogăm terminalul pentru ticuri noi (secunde).
POLL_INTERVAL = 0.010

# Timeframe-urile pentru care MT5 are lumânări gata calculate (secunde -> constanta MT5).
TIMEFRAMES = {
    60: mt5.TIMEFRAME_M1, 300: mt5.TIMEFRAME_M5, 900: mt5.TIMEFRAME_M15, 1800: mt5.TIMEFRAME_M30,
    3600: mt5.TIMEFRAME_H1, 14400: mt5.TIMEFRAME_H4, 86400: mt5.TIMEFRAME_D1,
    604800: mt5.TIMEFRAME_W1,
}
# Terminalul păstrează cel mult „Max bars in chart” lumânări pe timeframe (implicit 100.000: doar
# ~3 luni de M1). La M1 și M5, perioadele mai vechi se construiesc din ticuri (care merg mult mai
# departe în trecut). Lumânările rezultate sunt identice cu cele MT5. Alternativ: în MT5,
# Tools > Options > Charts > Max bars in chart = Unlimited (și repornit terminalul).
TICK_FALLBACK = {60, 300}

# Pachetul MetaTrader5 nu e garantat thread-safe: toate apelurile trec prin acest lock.
# Un „lock” (lacăt) asigură că un singur fir de execuție vorbește cu MT5 la un moment dat.
mt5_lock = threading.Lock()


class MT5Feed:
    """Prețuri și istoric din terminalul MT5 (aceeași interfață ca CTraderFeed)."""

    def __init__(self):
        self.on_ticks = None     # callback(simbol, [[timp_ms, bid, ask], ...]) în bucla asyncio
        self.on_status = None    # callback(conectat: bool)
        self.ready = asyncio.Event()
        self.loop: asyncio.AbstractEventLoop | None = None
        # Simbolurile terminalului: nume normalizat -> nume exact; plus listele pentru sugestii.
        self._lookup: dict[str, str] = {}
        self._visible: list[str] = []
        self._all: list[str] = []
        self._details: dict[str, dict] = {}
        # Ultimul preț [timp_ms, bid, ask] (în UTC) al fiecărui simbol urmărit.
        self.quotes: dict[str, list] = {}
        self.wanted: set[str] = set()
        self._want_raw: set[str] = set()
        # Diferența (secunde) dintre ora serverului MT5 și UTC. Se poate fixa din .env, altfel
        # se estimează automat.
        fixed = os.getenv("MT5_UTC_OFFSET_HOURS", "").strip()
        self._fixed_offset = int(float(fixed) * 3600) if fixed else None
        self.offset = self._fixed_offset or 0
        self._lost: asyncio.Event | None = None
        self._stop = threading.Event()

    # ---------- Conversii de timp ----------
    def _to_utc_ms(self, server_ms: int) -> int:
        return int(server_ms) - self.offset * 1000

    def _to_server_s(self, utc_ms: int) -> int:
        return int(utc_ms // 1000) + self.offset

    # Ora serverului MT5 față de UTC: cel mai recent tic din Market Watch ar trebui să fie „acum”
    # (la crypto, chiar și în weekend). Diferența, rotunjită la jumătate de oră, e fusul orar.
    def _estimate_offset(self):
        if self._fixed_offset is not None:
            return
        newest = 0
        for name in self._visible:
            tick = mt5.symbol_info_tick(name)
            if tick is not None:
                newest = max(newest, tick.time)
        if not newest:
            return
        diff = newest - time.time()
        offset = round(diff / 1800) * 1800
        # Acceptăm estimarea doar dacă ultimul tic e recent (cel mult 10 minute „vechime”).
        if abs(diff - offset) < 600:
            if offset != self.offset:
                print(f"[MT5] ora serverului: UTC{offset / 3600:+g}")
            self.offset = offset

    # ---------- Simboluri ----------
    @staticmethod
    def _norm(name: str) -> str:
        return name.upper().replace("/", "").replace(" ", "")

    def resolve(self, name: str) -> str | None:
        return self._lookup.get(self._norm(name))

    def symbol_names(self) -> tuple[list[str], list[str]]:
        return list(self._visible), list(self._all)

    async def info(self, name: str) -> dict:
        if name in self._details:
            return self._details[name]
        self._check_ready()

        def get():
            with mt5_lock:
                # symbol_select îl adaugă în Market Watch (altfel MT5 nu dă cotații pentru el).
                mt5.symbol_select(name, True)
                return mt5.symbol_info(name)
        info = await asyncio.to_thread(get)
        if info is None:
            raise ConnectionError(f"MT5 nu are date pentru {name}")
        d = {"name": name, "digits": info.digits, "point": info.point,
             "description": info.description or name}
        self._details[name] = d
        return d

    def _check_ready(self):
        if not self.ready.is_set():
            raise ConnectionError("Nu sunt conectat la MT5")

    # ---------- Prețuri ----------
    def want(self, names: set[str]):
        self._want_raw = set(names)
        self.wanted = {r for n in names if (r := self.resolve(n))}

    async def quote(self, name: str, wait: float = 3.0) -> list | None:
        if name in self.quotes:
            return list(self.quotes[name])
        self._check_ready()

        def get():
            with mt5_lock:
                mt5.symbol_select(name, True)
                return mt5.symbol_info_tick(name)
        tick = await asyncio.to_thread(get)
        if tick is None or tick.time_msc == 0:
            return None
        return [self._to_utc_ms(tick.time_msc), tick.bid, tick.ask]

    # ---------- Istoric ----------
    # Ticurile unei laturi ("BID"/"ASK") între start_ms și end_ms (UTC), cronologic:
    # [(timp_ms, preț), ...]. max_pages există doar pentru compatibilitate cu CTraderFeed.
    async def ticks(self, name: str, side: str, start_ms: int, end_ms: int,
                    max_pages: int = 10) -> list[tuple[int, float]]:
        self._check_ready()
        field = "bid" if side == "BID" else "ask"

        def get():
            with mt5_lock:
                return mt5.copy_ticks_range(name, self._to_server_s(start_ms),
                                            self._to_server_s(end_ms) + 1, mt5.COPY_TICKS_ALL)
        ticks = await asyncio.to_thread(get)
        if ticks is None:
            return []
        # Unele ticuri au preț doar pe o latură (cealaltă e 0) – le sărim.
        return [(self._to_utc_ms(t["time_msc"]), float(t[field])) for t in ticks if t[field] > 0]

    # Ultimele `count` lumânări gata calculate de MT5 (M1 și peste), deschise înainte de end_ms.
    async def trendbars(self, name: str, tf: int, count: int, end_ms: int) -> list[dict]:
        self._check_ready()

        def get():
            with mt5_lock:
                mt5.symbol_select(name, True)
                # `count` lumânări spre trecut, pornind de la momentul dat (ora serverului).
                return mt5.copy_rates_from(name, TIMEFRAMES[tf], self._to_server_s(end_ms - 1), count)
        bars = self._bars(await asyncio.to_thread(get), end_ms=end_ms)
        # Istoricul terminalului s-a terminat: la M1/M5 completăm din ticuri, cel mult 3 zile
        # odată (restul vine la următoarea cerere, când chartul e derulat mai departe).
        if len(bars) < count and tf in TICK_FALLBACK:
            stop = bars[0]["time"] * 1000 if bars else end_ms
            span = min((count - len(bars)) * tf * 1000, 3 * 86400_000)
            older = await asyncio.to_thread(self._tick_bars, name, tf, stop - span, stop)
            bars = older[-(count - len(bars)):] + bars if older else bars
        return bars

    # Toate lumânările (M1 și peste) deschise între start_ms (inclusiv) și end_ms (exclusiv), UTC.
    async def bars_range(self, name: str, tf: int, start_ms: int, end_ms: int) -> list[dict]:
        self._check_ready()

        def get():
            with mt5_lock:
                mt5.symbol_select(name, True)
                rates = mt5.copy_rates_range(name, TIMEFRAMES[tf], self._to_server_s(start_ms),
                                             self._to_server_s(end_ms - 1))
                # Are terminalul o lumânare de la start_ms sau dinainte? copy_rates_from întoarce
                # lumânarea de la acel moment sau, dacă istoricul începe mai târziu, prima lumânare
                # disponibilă (ori nimic).
                first = mt5.copy_rates_from(name, TIMEFRAMES[tf], self._to_server_s(start_ms), 1) \
                    if tf in TICK_FALLBACK else None
                return rates, first
        rates, first = await asyncio.to_thread(get)
        bars = self._bars(rates, start_ms, end_ms)
        # Partea mai veche decât istoricul terminalului se construiește din ticuri (M1/M5).
        if tf in TICK_FALLBACK:
            native = (int(first[0]["time"]) - self.offset) * 1000 if first is not None and len(first) else end_ms
            if native > start_ms:
                stop = min(end_ms, native)
                older = await asyncio.to_thread(self._tick_bars, name, tf, start_ms, stop)
                bars = older + [b for b in bars if b["time"] * 1000 >= stop]
        return bars

    # Lumânări construite din ticurile bid, pentru perioadele fără lumânări gata calculate.
    # Intervalele sunt aliniate în ora serverului, ca lumânările MT5 (rezultatul e identic cu
    # lumânările MT5, inclusiv volumul). Ticurile se citesc în bucăți
    # de o oră, ca lock-ul MT5 să nu blocheze mult timp prețurile live.
    def _tick_bars(self, name: str, tf: int, start_ms: int, end_ms: int) -> list[dict]:
        out: list[dict] = []
        t = start_ms
        while t < end_ms:
            to = min(t + 3600_000, end_ms)
            with mt5_lock:
                ticks = mt5.copy_ticks_range(name, self._to_server_s(t), self._to_server_s(to) + 1,
                                             mt5.COPY_TICKS_INFO)
            t = to
            if ticks is None or len(ticks) == 0:
                continue
            # Doar ticurile care schimbă bid-ul (așa își calculează MT5 lumânările și volumul).
            ticks = ticks[(ticks["bid"] > 0) & ((ticks["flags"] & mt5.TICK_FLAG_BID) != 0)]
            for msc, bid in zip(ticks["time_msc"].tolist(), ticks["bid"].tolist()):
                utc = msc - self.offset * 1000
                if not start_ms <= utc < end_ms:
                    continue
                bt = msc // 1000 // tf * tf - self.offset
                b = out[-1] if out else None
                if b and b["time"] == bt:
                    b["high"] = max(b["high"], bid)
                    b["low"] = min(b["low"], bid)
                    b["close"] = bid
                    b["volume"] += 1
                else:
                    out.append({"time": bt, "open": bid, "high": bid, "low": bid, "close": bid, "volume": 1})
        # Dacă start_ms nu e aliniat, prima lumânare ar fi incompletă (deschisă înainte de start_ms).
        return [b for b in out if start_ms <= b["time"] * 1000 < end_ms]

    # Lumânările MT5 (ora serverului) -> dicționare în UTC; păstrăm doar cele din [start, end).
    def _bars(self, rates, start_ms: int = 0, end_ms: int | None = None) -> list[dict]:
        if rates is None:
            return []
        out = []
        for r in rates:
            t = int(r["time"]) - self.offset
            if t * 1000 < start_ms or (end_ms is not None and t * 1000 >= end_ms):
                continue
            out.append({"time": t, "open": float(r["open"]), "high": float(r["high"]),
                        "low": float(r["low"]), "close": float(r["close"]),
                        "volume": int(r["tick_volume"])})
        return out

    # ---------- Conexiunea ----------
    # Rulează cât timp merge serverul: se atașează la terminal și, dacă legătura se pierde
    # (terminal închis), reîncearcă la fiecare 5 secunde.
    async def run(self):
        self.loop = asyncio.get_running_loop()
        while True:
            ok = await asyncio.to_thread(self._connect)
            if not ok:
                await asyncio.sleep(5)
                continue
            self._lost = asyncio.Event()
            self._stop.clear()
            self.ready.set()
            self.want(self._want_raw)
            if self.on_status:
                self.on_status(True)
            thread = threading.Thread(target=self._poll_loop, daemon=True)
            thread.start()
            try:
                await self._lost.wait()
            finally:
                self._stop.set()
                self.ready.clear()
                self.quotes.clear()
            if self.on_status:
                self.on_status(False)
            print("[MT5] conexiune pierdută, reîncerc...")
            await asyncio.sleep(1)

    # Se atașează la terminal și citește lista de simboluri (rulează într-un fir separat).
    def _connect(self) -> bool:
        with mt5_lock:
            if not mt5.initialize():
                print(f"[MT5] initialize() a eșuat: {mt5.last_error()} (terminalul e pornit și logat?)")
                return False
            acc = mt5.account_info()
            syms = mt5.symbols_get() or ()
            self._all = [s.name for s in syms]
            self._visible = [s.name for s in syms if s.visible]
            self._lookup = {self._norm(n): n for n in self._all}
            self._details.clear()
            self._estimate_offset()
        print(f"[MT5] conectat: {acc.server} / {acc.login}" if acc else "[MT5] conectat")
        if self._fixed_offset is None and self.offset == 0:
            print("[MT5] nu am putut afla ora serverului (piață închisă?); "
                  "o poți fixa în .env cu MT5_UTC_OFFSET_HOURS")
        return True

    # Firul de interogare: la fiecare 10 ms cere ticurile noi ale simbolurilor urmărite.
    def _poll_loop(self):
        # last_msc: pentru fiecare simbol, momentul (în ora serverului, ms) ultimului tic trimis.
        last_msc: dict[str, int] = {}
        next_check = 0.0
        next_offset = time.monotonic() + 600
        while not self._stop.is_set():
            symbols = set(self.wanted)
            # Uităm simbolurile care nu mai interesează pe nimeni.
            for sym in list(last_msc):
                if sym not in symbols:
                    del last_msc[sym]
            for sym in symbols:
                try:
                    self._poll_symbol(sym, last_msc)
                except Exception as e:  # noqa: BLE001 - firul nu trebuie să moară
                    print(f"[MT5] eroare la {sym}: {e}")
            # O dată pe secundă: terminalul mai e conectat? (și, la 10 minute, recalculăm ora
            # serverului: se schimbă la trecerea pe ora de vară/iarnă)
            if time.monotonic() >= next_check:
                next_check = time.monotonic() + 1
                with mt5_lock:
                    alive = mt5.terminal_info() is not None
                    if alive and time.monotonic() >= next_offset:
                        next_offset = time.monotonic() + 600
                        self._estimate_offset()
                if not alive:
                    self.loop.call_soon_threadsafe(self._lost.set)
                    return
            time.sleep(POLL_INTERVAL)

    # Verifică dacă au apărut ticuri noi pentru UN simbol și le trimite în bucla asyncio.
    def _poll_symbol(self, symbol: str, last_msc: dict[str, int]):
        with mt5_lock:
            last = last_msc.get(symbol)
            # Prima dată când vedem simbolul: îl adăugăm în Market Watch și reținem ultimul tic
            # (nu retrimitem istoricul – acesta e trimis separat, la conectarea paginii).
            if last is None:
                mt5.symbol_select(symbol, True)
                tick = mt5.symbol_info_tick(symbol)
                if tick is not None:
                    last_msc[symbol] = tick.time_msc
                    if tick.bid > 0 and tick.ask > 0:
                        q = [self._to_utc_ms(tick.time_msc), tick.bid, tick.ask]
                        self.loop.call_soon_threadsafe(self.quotes.__setitem__, symbol, q)
                return
            # Cerem toate ticurile din secunda ultimului tic încoace și păstrăm doar cele noi,
            # ca să nu pierdem ticuri când vin mai multe între două interogări.
            ticks = mt5.copy_ticks_from(symbol, last // 1000, 2000, mt5.COPY_TICKS_ALL)
        if ticks is None or len(ticks) == 0:
            return
        new = ticks[ticks["time_msc"] > last]
        if len(new) == 0:
            return
        last_msc[symbol] = int(new["time_msc"][-1])
        data = [[self._to_utc_ms(t["time_msc"]), float(t["bid"]), float(t["ask"])] for t in new]
        self.loop.call_soon_threadsafe(self._emit, symbol, data)

    # Rulează în bucla asyncio: reține ultimul preț și anunță serverul.
    def _emit(self, symbol: str, data: list[list]):
        if symbol not in self.wanted:
            return
        last = data[-1]
        if last[1] > 0 and last[2] > 0:
            self.quotes[symbol] = last
        if self.on_ticks:
            self.on_ticks(symbol, data)
