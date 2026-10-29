"""
Optimizarea parametrilor strategiei, ca în Strategy Tester-ul MT5: completă (toate combinațiile) sau
genetică rapidă („Fast genetic based algorithm”), cu criteriul Balance max. Fiecare test e simularea din
fastsim (aceleași reguli ca testerul) pe modelling-ul ales în Setări: Open prices, 1 minute OHLC sau Real
ticks (cele două din urmă pe indexul pe minute, câteva ms pe test), rulată în paralel pe toate nucleele.
"""
# =====================================================================================
#  CE FACE ACEST FIȘIER (pe scurt)
# -------------------------------------------------------------------------------------
#  Grila: fiecare parametru bifat în pagină are o listă de valori (start, pas, stop; la prețul MA
#  și la InpCloseOnMA, valorile din listă). Un set de parametri = un „individ”, iar fiecare
#  parametru e o „genă”: indexul valorii în lista ei. N = produsul lungimilor = câte combinații.
#
#  Completă: toate cele N combinații, pe loturi.
#  Genetică (ca în videoclipul de concept, după regulile MT5):
#    - populația P = √N rotunjit la multiplu de 8, între 64 și 256;
#    - generația 0: două populații aleatoare de P indivizi, testate; rămân cei mai buni P;
#    - o generație nouă: elita (cei mai buni 10%) trece neschimbată; restul sunt copii: fiecare
#      părinte câștigă un turneu între 3 indivizi aleși la întâmplare, copilul ia fiecare genă de
#      la unul dintre părinți (50 / 50), apoi mutația (15% pe genă: jumătate un pas vecin,
#      jumătate o valoare aleatoare) și inversia (5% pe genă: indexul oglindit în listă);
#    - un copil deja testat nu se mai rulează (cache); se mai mută o dată, ca să aducă ceva nou;
#    - oprirea: cel puțin Gu = log2(N) generații (între 15 și 31), apoi se oprește când ultimele
#      Gc = Gu / 3 (rotunjit în sus) generații n-au adus un maxim nou (sau s-au testat toate).
#    - Grilele mici (N <= 2P) se rulează complet: algoritmul genetic n-ar avea ce câștiga.
#  Fitness = balanța finală; un test fără nicio tranzacție primește 0, ca să nu câștige niciodată.
#  Forward (dacă e ales în Setări): optimizarea folosește doar perioada de până la data forward, apoi
#  toate seturile profitabile (balanța finală peste depozit) se rulează și pe perioada forward, de la
#  același depozit, tot în paralel.
#  Rezultatul: toate testele (ca tab-ul Optimization results din MT5), sortabile în pagină.
# =====================================================================================
import itertools
import math
import os
import pickle
import random
import time
import uuid
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool


import backtester
import fastsim
import strategy_fast

ELITE, TOURNAMENT, MUTATION, INVERSION = 0.10, 3, 0.15, 0.05
MAX_COMBINATIONS = 10 ** 12
INT_PARAMS = ("ma_period", "tl_bars")
NUM_PARAMS = ("ma_period", "tl_bars", "rr", "risk_pct")


class Cancelled(Exception):
    pass


def num(v, dec: int = 0) -> str:
    """1 234 567 / 18 786,39: spațiu la mii, virgulă la zecimale (ca în pagină)."""
    return f"{v:,.{dec}f}".replace(",", " ").replace(".", ",")


# ---------- Grila ----------
def grid(inputs: dict, params: dict) -> list[tuple[str, list]]:
    """[(parametru, valori)] pentru parametrii optimizați; restul rămân la valoarea din `inputs`."""
    out = []
    for k, p in params.items():
        if k in NUM_PARAMS:
            start, step, stop = float(p["start"]), float(p["step"]), float(p["stop"])
            if step <= 0 or stop < start:
                raise ValueError(f"{k}: start ≤ stop și pas > 0")
            count = int(math.floor((stop - start) / step + 1e-9)) + 1
            vals = [round(start + i * step, 10) for i in range(count)]
            if k in INT_PARAMS:
                vals = sorted({int(round(v)) for v in vals})
            low = {"ma_period": 1, "tl_bars": 10}.get(k, 0)
            if vals[0] < low or (k in ("rr", "risk_pct") and vals[0] <= 0):
                raise ValueError(f"{k}: valoarea minimă e {'> 0' if k in ('rr', 'risk_pct') else low}")
        elif k == "ma_price":
            vals = [v for v in p["values"] if v in strategy_fast.PRICES]
        elif k == "close_on_ma":
            vals = [bool(v) for v in p["values"]]
        else:
            raise ValueError(f"Parametrul {k} nu se poate optimiza")
        if not vals:
            raise ValueError(f"{k}: nicio valoare")
        out.append((k, vals))
    if not out:
        raise ValueError("Bifează cel puțin un parametru de optimizat (tab-ul Parametri)")
    return out


# ---------- Testele, în procesele de lucru ----------
# Procesele de lucru pornesc greu (numba + funcțiile compilate: ~10–30 s pentru toate), deci rămân
# pornite de la o optimizare la alta (în procesul testerului) și primesc doar datele noi: perioada
# pregătită (fastsim.Prepared) se scrie o dată pe disc, iar fiecare proces o citește la primul lot.
_POOL: ProcessPoolExecutor | None = None
_POOL_SIZE = 0
_PREP = None
_PREP_ID = None


def workers_count(wanted=None) -> int:
    return max(1, min((os.cpu_count() or 2) - 1, int(wanted or 64)))


def get_pool(workers: int) -> ProcessPoolExecutor:
    global _POOL, _POOL_SIZE
    if _POOL is None or _POOL_SIZE != workers:
        if _POOL is not None:
            _POOL.shutdown(cancel_futures=True)
        _POOL, _POOL_SIZE = ProcessPoolExecutor(workers, initializer=backtester.warmup), workers
    return _POOL


def _ping(seconds: float) -> int:
    time.sleep(seconds)
    return os.getpid()


def warm_pool(workers=None) -> int:
    """Pornește procesele de lucru dinainte (server.py, când pagina alege o optimizare). Nu așteaptă după
    ele: procesele pornesc în fundal, iar o optimizare cerută între timp își pune testele la coadă."""
    n = workers_count(workers)
    if n > 1:
        pool = get_pool(n)
        for _ in range(n):
            pool.submit(_ping, 0.2)
    return n


def _evaluate_batch(path: str, prep_id: str, sets: list[dict]) -> list[dict]:
    global _PREP, _PREP_ID
    if _PREP_ID != prep_id:
        with open(path, "rb") as f:
            _PREP, _PREP_ID = pickle.load(f), prep_id
    return [fastsim.simulate(_PREP, s) for s in sets]


def fitness(r: dict) -> float:
    return r["balance"] if r["trades"] else 0.0


class Runner:
    """Rulează seturile de parametri (în paralel) și ține minte fiecare rezultat (cache)."""

    def __init__(self, prep, inputs: dict, keys: list[str], values: list[list], workers: int, cancelled, log):
        self.prep, self.inputs, self.keys, self.values = prep, inputs, keys, values
        self.cancelled, self.log = cancelled, log
        self.cache: dict[tuple, dict] = {}
        self.passes: list[dict] = []
        self.workers = workers
        self.id = uuid.uuid4().hex
        self.path = backtester.CACHE / f"opt_{self.id}.pkl"
        if workers > 1:
            backtester.CACHE.mkdir(exist_ok=True)
            with open(self.path, "wb") as f:
                pickle.dump(prep, f, protocol=pickle.HIGHEST_PROTOCOL)
            self.pool = get_pool(workers)
        else:
            self.pool = None
            backtester.warmup()

    def close(self):
        self.path.unlink(missing_ok=True)

    def _evaluate(self, sets: list[dict]) -> list[dict]:
        global _POOL
        if self.pool is None:
            return [fastsim.simulate(self.prep, s) for s in sets]
        # Seturile cu aceleași (MA, preț, TLBars) merg în același lot: procesul le refolosește semnalele.
        order = sorted(range(len(sets)), key=lambda i: tuple(str(sets[i].get(k)) for k in ("ma_period", "ma_price", "tl_bars")))
        size = max(1, math.ceil(len(sets) / (self.workers * 3)))
        chunks = [[sets[i] for i in order[q:q + size]] for q in range(0, len(order), size)]
        try:
            futs = [self.pool.submit(_evaluate_batch, str(self.path), self.id, c) for c in chunks]
            flat = [r for f in futs for r in f.result()]
            out = [None] * len(sets)
            for i, r in zip(order, flat):
                out[i] = r
            return out
        except BrokenProcessPool:
            _POOL = None                     # un proces a căzut: data viitoare se pornesc altele
            raise

    def params(self, genes: tuple) -> dict:
        return {k: self.values[i][g] for i, (k, g) in enumerate(zip(self.keys, genes))}

    def run(self, genomes: list[tuple], gen: int) -> list[dict]:
        """Rezultatele genomurilor (din cache sau testate acum, în paralel)."""
        if self.cancelled():
            raise Cancelled()
        todo = list(dict.fromkeys(g for g in genomes if g not in self.cache))
        if todo:
            results = self._evaluate([{**self.inputs, **self.params(g)} for g in todo])
            for g, r in zip(todo, results):
                p = {"pass": len(self.passes) + 1, "gen": gen, "values": self.params(g), **r, "fitness": fitness(r)}
                self.cache[g] = p
                self.passes.append(p)
        return [self.cache[g] for g in genomes]


# ---------- Algoritmii ----------
def complete(runner: Runner, sizes: list[int], progress) -> None:
    n = math.prod(sizes)
    batch = max(256, runner.workers * 64)
    it = itertools.product(*(range(s) for s in sizes))
    done = 0
    while True:
        genomes = list(itertools.islice(it, batch))
        if not genomes:
            return
        runner.run(genomes, 0)
        done += len(genomes)
        progress(done / n)


def genetic(runner: Runner, sizes: list[int], rng: random.Random, progress, log, live) -> dict:
    n = math.prod(sizes)
    pop_size = min(256, max(64, int(round(math.sqrt(n) / 8)) * 8))
    gu = min(31, max(15, int(round(math.log2(n)))))
    gc = math.ceil(gu / 3)
    elite = math.ceil(pop_size * ELITE)
    log(f"Genetic: populația {pop_size}, cel puțin {gu} generații, oprire după {gc} generații fără un maxim nou")

    def random_genome():
        return tuple(rng.randrange(s) for s in sizes)

    def mutate(g, force=False):
        g = list(g)
        for i, s in enumerate(sizes):
            r = rng.random()
            if r < MUTATION:
                g[i] = min(s - 1, max(0, g[i] + rng.choice((-1, 1)))) if rng.random() < 0.5 else rng.randrange(s)
            elif r < MUTATION + INVERSION:
                g[i] = s - 1 - g[i]
        if force:                                      # cel puțin o genă schimbată
            i = rng.randrange(len(sizes))
            if sizes[i] > 1:
                g[i] = (g[i] + rng.randrange(1, sizes[i])) % sizes[i]
        return tuple(g)

    def unique_random(count, taken):
        out = []
        for _ in range(count * 20):
            if len(out) >= count:
                break
            g = random_genome()
            if g not in taken:
                taken.add(g)
                out.append(g)
        return out

    # Generația 0: două populații aleatoare; rămân cei mai buni pop_size.
    taken: set = set()
    first = unique_random(2 * pop_size, taken)
    results = runner.run(first, 0)
    pop = [g for g, _ in sorted(zip(first, results), key=lambda x: -x[1]["fitness"])[:pop_size]]
    best = max(runner.passes, key=lambda p: p["fitness"])
    last_better, gen = 0, 0

    def report():
        live({"gen": gen, "tested": len(runner.passes), "combinations": n, "best": best["fitness"],
              "best_pass": best["pass"], "best_values": best["values"]})
        log(f"Generația {gen}: {num(len(runner.passes))} teste, cel mai bun {num(best['fitness'], 2)} (testul {best['pass']})")
    report()
    while len(runner.cache) < n:
        gen += 1
        ranked = sorted(pop, key=lambda g: -runner.cache[g]["fitness"])
        children: list[tuple] = []
        seen = set(ranked[:elite])
        pick = lambda: max(rng.sample(ranked, min(TOURNAMENT, len(ranked))), key=lambda g: runner.cache[g]["fitness"])
        while len(children) < pop_size - elite:
            a, b = pick(), pick()
            child = mutate(tuple(x if rng.random() < 0.5 else y for x, y in zip(a, b)))
            for _ in range(5):
                if child not in runner.cache and child not in seen:
                    break
                child = mutate(child, force=True)
            seen.add(child)
            children.append(child)
        runner.run(children, gen)
        pop = ranked[:elite] + children
        top = max(runner.passes, key=lambda p: p["fitness"])
        if top["fitness"] > best["fitness"]:
            best, last_better = top, gen
        report()
        progress(min(0.99, gen / max(gu, last_better + gc)))
        if gen >= gu and gen - last_better >= gc:
            break
    return {"population": pop_size, "generations": gen, "min_generations": gu, "convergence": gc}


# ---------- Optimizarea ----------
def optimize(settings: dict, inputs: dict, opt: dict, progress=None, log=print, cancelled=lambda: False,
             live=lambda d: None) -> dict:
    t_start = time.time()
    journal: list[str] = []

    def say(msg):
        journal.append(f"{time.strftime('%H:%M:%S')}  {msg}")
        log(msg)

    def prog(phase, f):
        if progress:
            progress(phase, f)
        if cancelled():
            raise Cancelled()

    if settings["modelling"] not in backtester.MODES:
        raise ValueError(f"Modelling necunoscut: {settings['modelling']}")
    if opt.get("criterion", "balance") != "balance":
        raise ValueError("Criteriul disponibil: Balance max")
    axes = grid(inputs, opt.get("params", {}))
    keys, values = [k for k, _ in axes], [v for _, v in axes]
    sizes = [len(v) for v in values]
    n = math.prod(sizes)
    if n > MAX_COMBINATIONS:
        raise ValueError(f"Prea multe combinații ({n:.3g})")
    mode = opt.get("mode", "genetic")
    seed = int(opt["seed"]) if opt.get("seed") not in (None, "") else random.randrange(1, 10 ** 6)
    max_window = max([int(inputs["tl_bars"])] + [int(v) for k, vs in axes if k == "tl_bars" for v in vs])
    forward_ms = settings.get("forward_ms")
    back = {**settings, "end_ms": forward_ms} if forward_ms else settings
    say(f"Optimizare {'genetică' if mode == 'genetic' else 'completă'} pe {backtester.MODES[settings['modelling']]}, "
        f"Balance max: {num(n)} combinații ({' × '.join(f'{k} {s}' for k, s in zip(keys, sizes))}), sămânța {seed}")

    from dotenv import load_dotenv
    load_dotenv(backtester.ROOT / ".env")
    from tickdb import TickDB
    db = TickDB(backtester.SOURCE, log=lambda *_: None)
    prog("data", 0.0)
    prep = fastsim.prepare(back, max_window, db, log=say, progress=prog)
    workers = workers_count(opt.get("workers"))
    say(f"Rulez testele pe {workers} procese")
    prog("optimize", 0.0)
    runner = Runner(prep, inputs, keys, values, workers, cancelled, say)
    info = {}
    stopped = False
    try:
        if mode == "genetic" and n > 2 * min(256, max(64, int(round(math.sqrt(n) / 8)) * 8)):
            info = genetic(runner, sizes, random.Random(seed), lambda f: prog("optimize", f), say, live)
        else:
            if mode == "genetic":
                say("Grila e mică: o rulez completă (toate combinațiile)")
                mode = "complete"
            complete(runner, sizes, lambda f: prog("optimize", f))
    except Cancelled:
        if not runner.passes:
            raise
        say("Oprit: păstrez testele făcute până acum")
        stopped = True
    finally:
        runner.close()
    passes = runner.passes
    ranked = sorted(passes, key=lambda p: -p["fitness"])
    say(f"Gata: {num(len(passes))} teste din {num(n)} ({num(len(passes) / n * 100, 1)}%) în "
        f"{num(time.time() - t_start, 1)} s; cel mai bun: {num(ranked[0]['fitness'], 2)} (testul {ranked[0]['pass']})")

    forward_count = 0
    if forward_ms and ranked and not stopped:
        good = [p for p in ranked if p["trades"] and p["balance"] > float(settings["deposit"])]
        say(f"Forward: rulez cele {num(len(good))} seturi profitabile de la {backtester.ms_text(forward_ms)[:10]}")
        frunner = None
        try:
            prog("forward", 0.0)
            fwd = fastsim.prepare({**settings, "start_ms": forward_ms}, max_window, db, log=lambda *_: None)
            frunner = Runner(fwd, inputs, keys, values, workers, cancelled, say)
            step = max(500, workers * 100)
            for i in range(0, len(good), step):
                part = good[i:i + step]
                for p, r in zip(part, frunner._evaluate([{**inputs, **p["values"]} for p in part])):
                    p["forward"] = r
                prog("forward", min(1.0, (i + step) / len(good)))
        except Cancelled:
            say("Forward oprit: păstrez seturile rulate până acum")
        finally:
            if frunner:
                frunner.close()
        good = [p for p in good if "forward" in p]
        forward_count = len(good)
        both = sum(1 for p in good if p["forward"]["trades"] and p["forward"]["balance"] > float(settings["deposit"]))
        say(f"Forward gata: {num(both)} din {num(len(good))} seturi profitabile rămân profitabile și în forward")
    if progress:
        progress("optimize", 1.0)        # fără verificarea opririi: rezultatele de până acum se întorc
    return {
        "kind": "optimize", "settings": {**settings, "start_ms": back["start_ms"]}, "inputs": inputs,
        "modelling": settings["modelling"],
        "mode": mode, "criterion": "balance", "seed": seed, "combinations": n, "keys": keys,
        "values": {k: v for k, v in axes}, "passes": passes, "journal": journal, "seconds": round(time.time() - t_start, 1),
        "back_end_ms": prep.settings["end_ms"], "forward_ms": forward_ms, "currency": settings.get("currency", "USD"),
        "deposit": float(settings["deposit"]), "forward_count": forward_count, **info,
    }
