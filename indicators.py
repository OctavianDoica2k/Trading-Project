"""
Calculul indicatorilor pe server. Rulează într-un PROCES separat (ProcessPoolExecutor, vezi
server.py), ca un calcul lung să nu întârzie prețurile live: în același proces, GIL-ul Python ar
lăsa un singur fir să lucreze odată. De aceea fișierul e mic și nu importă nimic din server.py.
"""
# =====================================================================================
#  CE FACE ACEST FIȘIER (pe scurt)
# -------------------------------------------------------------------------------------
#  - SPECS: indicatorii cunoscuți de server. Pentru fiecare: câte lumânări (închise, din
#    datastore) îi trebuie în funcție de parametri, minimul acceptat și funcția de calcul.
#  - compute(id, bars, params): punctul de intrare apelat în procesul separat.
#  - „Linii de trend”: rulează funcția trace(df) din partea a 2-a a analysis.py (după linia
#    #----#), exact cum e scrisă, pe lumânările primite.
#  - „Medie mobilă”: rulează funcția medie_mobila(df, period, price) din analysis.py, cu perioada
#    și prețul (open / high / low / close) alese în pagină.
#  - „Trend multi-timeframe” (run_mtf): rulează timeframe_trends_indicator(df) din partea 1 a
#    analysis.py pe lumânările D1 din datastore (anul curent); nu e în SPECS, pentru că folosește
#    mereu D1, oricare ar fi timeframe-ul chartului (server.py îl apelează direct, vezi MtfWatch).
#  analysis.py e citit la fiecare calcul, deci o modificare în el se vede la următorul calcul,
#  fără repornirea serverului.
#  Un indicator nou: o funcție de calcul + o intrare în SPECS (și fișierul lui în static/).
# =====================================================================================
import re
from pathlib import Path

ANALYSIS = Path(__file__).with_name("analysis.py")
# Câte lumânări în spate analizează implicit „Linii de trend” (cele mai noi lumânări închise).
# Algoritmul verifică fiecare pereche de pivoturi pe toate lumânările de după ea; cu accesul rapid
# de mai jos (FastFrame) durează ~2–3 s la 1000 de lumânări.
TREND_BARS = 1000
TREND_MAX_BARS = 5000


# analysis.py citește lumânările cu df.iloc[i]['high'] de milioane de ori. Pe un DataFrame
# pandas, fiecare astfel de citire durează ~20 µs (1000 de lumânări = minute). FastFrame oferă
# exact aceleași citiri (len(df), df.iloc[i]['open' / 'high' / 'low' / 'close']), dar dintr-o
# listă pregătită dinainte, de sute de ori mai repede. Valorile rămân numpy.float64, ca în
# pandas, deci rezultatele sunt identice (inclusiv împărțirea la 0 -> inf la pivoturi egale).
class FastFrame:
    # df.iloc[i] -> dicționarul rândului i ({'open': ..., 'high': ..., 'low': ..., 'close': ...}).
    class _Rows:
        def __init__(self, rows):
            self.rows = rows

        def __getitem__(self, i):
            return self.rows[i]

    def __init__(self, df):
        # Citim fiecare coloană o singură dată (to_numpy e rapid), apoi construim lista de rânduri.
        cols = {c: df[c].to_numpy() for c in ("open", "high", "low", "close")}
        self.iloc = self._Rows([{c: a[i] for c, a in cols.items()} for i in range(len(df))])
        self._len = len(df)

    def __len__(self):
        return self._len


# Partea a 2-a din analysis.py (după linia #----#), ca arbore de sintaxă. Partea 1 nu se rulează:
# citește un CSV de pe alt calculator.
def analysis_part2():
    import ast
    src = ANALYSIS.read_text(encoding="utf-8")
    parts = re.split(r"^#-{5,}#\s*$", src, maxsplit=1, flags=re.M)
    if len(parts) < 2:
        raise ValueError("Nu găsesc linia #----# care separă partea a 2-a din analysis.py")
    # Rândurile goale din față păstrează numerele de linie din analysis.py în mesajele de eroare.
    return ast.parse("\n" * parts[0].count("\n") + parts[1], str(ANALYSIS))


# Rulează codul pe FastFrame; dacă analysis.py folosește ceva ce FastFrame nu are (ex. df['close'],
# df.sort_values), rulează din nou pe DataFrame-ul pandas obișnuit (mai lent, dar merge orice).
def run_on_frames(df, run):
    import numpy as np
    import pandas as pd
    # errstate: împărțirea la 0 (două pivoturi la același preț) dă inf fără avertismente în consolă.
    with np.errstate(divide="ignore", invalid="ignore"):
        try:
            return run({"df": FastFrame(df), "pd": pd})
        except (AttributeError, TypeError, KeyError):
            return run({"df": df, "pd": pd})


# Corpul funcției trace(df) rulat ca un script (cu `return` transformat în atribuire), ca să avem
# la final și variabilele ei interne: maxim / minim (pivoturile), pe lângă dreptele valide întoarse.
def trace_body(tree):
    import ast
    fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "trace"), None)
    if fn is None:
        raise ValueError("Nu găsesc funcția trace(df) în analysis.py")

    class ReturnToAssign(ast.NodeTransformer):
        def visit_FunctionDef(self, node):   # return-urile funcțiilor interioare rămân ale lor
            return node
        visit_AsyncFunctionDef = visit_Lambda = visit_FunctionDef

        def visit_Return(self, node):
            return ast.copy_location(ast.Assign(
                targets=[ast.Name("__result__", ast.Store())],
                value=node.value or ast.Constant(None)), node)

    body = [ReturnToAssign().visit(n) for n in fn.body]
    # Un `return` din mijlocul funcției ar opri-o; aici doar atribuie și continuă. trace() are un
    # singur return, la final, deci rezultatul e același.
    mod = ast.fix_missing_locations(ast.Module(body=body, type_ignores=[]))
    return compile(mod, str(ANALYSIS), "exec")


# Calculul indicatorului „Linii de trend”: rulează trace() din analysis.py pe lumânările primite
# și întoarce dreptele gata de desenat în pagină (punctul de start, al doilea pivot, capătul).
def run_trend_analysis(bars: list[dict], params: dict) -> dict:
    import pandas as pd  # doar aici, ca serverul să nu depindă de pandas

    df = pd.DataFrame(bars, columns=["time", "open", "high", "low", "close"])
    tree = analysis_part2()
    code = trace_body(tree)
    # Funcțiile și importurile din partea a 2-a (ex. pivoti, drepte_valide, np), pe care le folosește trace().
    defs: dict = {}
    exec(compile(tree, str(ANALYSIS), "exec"), defs)

    # Rulează codul lui trace() cu variabilele din ns (df, pd) și întoarce toate variabilele de la final.
    def run(ns):
        ns = {**defs, **ns}
        exec(code, ns)
        return ns
    ns = run_on_frames(df, run)
    dr_max_valid, dr_min_valid = ns["__result__"]

    # Fiecare dreaptă validă e o pereche (i, j) de poziții din maxim / minim; un punct e
    # (indexul lumânării în df, prețul). O desenăm de la primul punct până la ultima lumânare
    # închisă; `slope` = variația pe o lumânare, cu care pagina o prelungește până la lumânarea
    # în curs (pozițiile de pe chart sunt aceleași ca în datastore, deci dreapta rămâne dreaptă).
    last = len(df) - 1

    # points = pivoturile (maxim sau minim), pairs = perechile (i, j) valide -> lista de drepte.
    def lines(points, pairs):
        out = []
        for i, j in pairs:
            (x1, y1), (x2, y2) = points[i], points[j]
            slope = (y2 - y1) / (x2 - x1)
            out.append({"from": {"time": bars[x1]["time"], "value": float(y1)},
                        "via": {"time": bars[x2]["time"], "value": float(y2)},
                        "to": {"time": bars[last]["time"], "value": float(y1 + slope * (last - x1))},
                        "slope": float(slope)})
        return out

    return {"max": lines(ns["maxim"], dr_max_valid),
            "min": lines(ns["minim"], dr_min_valid),
            "pivots": {"max": len(ns["maxim"]), "min": len(ns["minim"])},
            # Intervalul de preț al lumânărilor analizate: sliderul măsoară distanța liniilor
            # față de prețul curent ca procent din el.
            "range": [float(df["low"].min()), float(df["high"].max())]}


# Câte lumânări analizează „Linii de trend”: valoarea `bars` aleasă în pagină, limitată între
# 7 (minimul pentru un pivot: 3 lumânări în stânga, 3 în dreapta) și TREND_MAX_BARS.
# O valoare greșită (text, lipsă) -> TREND_BARS.
def trend_bars(params: dict) -> int:
    try:
        n = int(params.get("bars", TREND_BARS))
    except (TypeError, ValueError):
        n = TREND_BARS
    return min(max(n, 7), TREND_MAX_BARS)


# ---------- Medie mobilă ----------
SMA_PRICES = ("close", "open", "high", "low")
SMA_MAX_PERIOD = 1000
# Câte lumânări închise primește medie_mobila: cele afișate pe chart, plus perioada (ca media să
# existe de la prima lumânare analizată).
SMA_BARS = 2000


# Parametrii mediei mobile din pagină, verificați: perioada (1 … SMA_MAX_PERIOD, implicit 20)
# și prețul folosit (close / open / high / low, implicit close).
def sma_params(params: dict) -> tuple[int, str]:
    try:
        period = int(params.get("period", 20))
    except (TypeError, ValueError):
        period = 20
    price = params.get("price", "close")
    return min(max(period, 1), SMA_MAX_PERIOD), price if price in SMA_PRICES else "close"


# Câte lumânări cere media mobilă: cele afișate (SMA_BARS) plus perioada.
def sma_bars(params: dict) -> int:
    return SMA_BARS + sma_params(params)[0]


# Calculul indicatorului „Medie mobilă”: rulează medie_mobila() din analysis.py și întoarce
# punctele (oră, valoare) de desenat ca linie pe chart.
def run_moving_average(bars: list[dict], params: dict) -> dict:
    import pandas as pd

    period, price = sma_params(params)
    df = pd.DataFrame(bars, columns=["time", "open", "high", "low", "close"])
    # Rulăm partea a 2-a din analysis.py: după asta, ns conține funcțiile definite acolo.
    ns = {}
    exec(compile(analysis_part2(), str(ANALYSIS), "exec"), ns)
    if "medie_mobila" not in ns:
        raise ValueError("Nu găsesc funcția medie_mobila(df, period, price) în analysis.py")
    vals = run_on_frames(df, lambda env: ns["medie_mobila"](env["df"], period, price))
    # Primele period-1 lumânări nu au medie (None): nu se desenează.
    points = [{"time": b["time"], "value": float(v)} for b, v in zip(bars, vals) if v is not None]
    return {"points": points, "period": period, "price": price}


# ---------- Trend multi-timeframe ----------
# Rulează partea 1 din analysis.py (înainte de #----#) exact cum e scrisă, cu o singură schimbare:
# în locul fișierului din pd.read_csv(r'...'), df primește lumânările D1 ale anului curent din
# datastore (aceleași coloane ca fișierul: time_utc, open, high, low, close, volume; vezi mtf.py).
# Apoi rulează timeframe_trends_indicator(df) și citește cele patru valori întoarse: ziua,
# săptămâna, luna, anul.
#
# Funcția se rulează instrucțiune cu instrucțiune: dacă una se oprește cu o eroare (ex. IndexError
# în primele zile de tranzacționare ale lunii), variabilele ei se șterg, deci doar valorile care
# depind de ea ies „—”; restul se afișează. Un maxim egal cu minimul (împărțire la 0) dă tot „—”.
MTF_FUNC = "timeframe_trends_indicator"
MTF_KEYS = ("day", "week", "month", "year")


# Numele variabilelor cărora o instrucțiune le dă valoare (ex. `mhigh = ...` -> {"mhigh"}).
# Când instrucțiunea dă eroare, le ștergem, ca să nu rămână o valoare veche sau pe jumătate.
def _stored_names(node) -> set[str]:
    import ast
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}


# Textul unei erori, așa cum apare în pagină: „IndexError: ... (analysis.py, linia 42)”.
def _err(e: BaseException, line: int | None = None) -> str:
    where = f" (analysis.py, linia {line})" if line else ""
    return f"{type(e).__name__}: {e}{where}"


# Coloanele tabelului D1 dat lui analysis.py (aceleași ca în CSV-urile exportate).
MTF_COLUMNS = ["time_utc", "open", "high", "low", "close", "volume"]


# rows = lumânările D1 ale anului curent din datastore, ca liste în ordinea MTF_COLUMNS.
# Întoarce cele 4 valori (zi / săptămână / lună / an), erorile pe fiecare și detaliile perioadelor.
def run_mtf(rows: list[list]) -> dict:
    import ast
    import contextlib
    import io
    import math

    import numpy as np
    import pandas as pd

    src = ANALYSIS.read_text(encoding="utf-8")
    part1 = re.split(r"^#-{5,}#\s*$", src, maxsplit=1, flags=re.M)[0]
    tree = ast.parse(part1, str(ANALYSIS))

    # Singura schimbare: pd.read_csv(...) devine tabelul din datastore.
    class FromDatastore(ast.NodeTransformer):
        def visit_Call(self, node):
            self.generic_visit(node)
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr == "read_csv":
                return ast.copy_location(ast.Name("__datastore_df__", ast.Load()), node)
            return node
    tree = ast.fix_missing_locations(FromDatastore().visit(tree))

    fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == MTF_FUNC), None)
    if fn is None:
        raise ValueError(f"Nu găsesc funcția {MTF_FUNC}(df) în analysis.py")

    def run(stmts, ns) -> str:
        """Rulează instrucțiunile una câte una; întoarce prima eroare (sau "")."""
        first = ""
        for st in stmts:
            try:
                exec(compile(ast.Module(body=[st], type_ignores=[]), str(ANALYSIS), "exec"), ns)
            except Exception as e:  # noqa: BLE001 - eroarea apare în pagină, pe caseta afectată
                first = first or _err(e, getattr(st, "lineno", None))
                for n in _stored_names(st):
                    ns.pop(n, None)
        return first

    ns: dict = {"__name__": "__analysis__", "__datastore_df__": pd.DataFrame(rows, columns=MTF_COLUMNS)}
    out = io.StringIO()
    with contextlib.redirect_stdout(out), np.errstate(all="ignore"):
        # Partea 1, la nivelul fișierului (citirea CSV-ului, importurile, definiția funcției).
        load_err = run(tree.body, ns)
        if not isinstance(ns.get("df"), pd.DataFrame):
            raise ValueError(load_err or "analysis.py nu citește baza de date (pd.read_csv) în variabila df")
        df = ns["df"]
        # Corpul funcției, cu parametrul df.
        body = [s for s in fn.body if not isinstance(s, ast.Return)]
        ret = next((s for s in fn.body if isinstance(s, ast.Return)), None)
        fns = {**ns, fn.args.args[0].arg if fn.args.args else "df": df}
        error = run(body, fns)
        exprs = ret.value.elts if ret is not None and isinstance(ret.value, ast.Tuple) else []
        if len(exprs) != 4:
            raise ValueError(f"{MTF_FUNC}() trebuie să întoarcă 4 valori: ziua, săptămâna, luna, anul")
        values, errors = {}, {}
        for key, e in zip(MTF_KEYS, exprs):
            try:
                v = float(eval(compile(ast.Expression(e), str(ANALYSIS), "eval"), fns))
            except Exception as ex:  # noqa: BLE001
                v, errors[key] = None, error or _err(ex, e.lineno)
            if v is not None and not math.isfinite(v):
                v, errors[key] = None, "maximul este egal cu minimul (împărțire la 0)"
            values[key] = v

    # Detaliile calculului (pentru casetele din pagină), din aceleași variabile ca analysis.py.
    def period(frame, hi, lo):
        try:
            f = frame.reset_index(drop=True)
            return {"start": str(f.iloc[0]["time_utc"])[:10], "end": str(f.iloc[-1]["time_utc"])[:10],
                    "n": len(f), "open": float(f.iloc[0]["open"]), "close": float(f.iloc[-1]["close"]),
                    "high": float(hi), "low": float(lo),
                    "own_high": float(f["high"].max()), "own_low": float(f["low"].min())}
        except Exception:  # noqa: BLE001 - perioada nu s-a putut calcula (eroarea e deja în errors)
            return None
    g = fns.get
    details = {
        "year": period(df, g("yhigh"), g("ylow")) if "yhigh" in fns else None,
        "month": period(g("lastmonth"), g("mhigh"), g("mlow")) if "mhigh" in fns else None,
        "week": period(g("lastweek"), g("whigh"), g("wlow")) if "whigh" in fns else None,
        "day": period(df.iloc[[-1]], df.iloc[-1]["high"], df.iloc[-1]["low"]) if len(df) else None,
    }
    return {"values": values, "errors": errors, "details": details, "rows": len(df),
            "stdout": out.getvalue()[-2000:]}


# id -> bars(params): câte lumânări îi trebuie; min: sub atâtea nu se poate calcula; run: calculul.
SPECS = {
    "trend": {"bars": trend_bars, "min": 7, "run": run_trend_analysis},
    "sma": {"bars": sma_bars, "min": 1, "run": run_moving_average},
}


# Punctul de intrare din procesul separat: alege calculul după id ("trend" / "sma") și îl rulează.
def compute(ind_id: str, bars: list[dict], params: dict) -> dict:
    return SPECS[ind_id]["run"](bars, params)
