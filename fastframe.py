"""Rulează heikin_ashi() din heikin_aishi.py repede, fără să schimbăm funcția.

Funcția lucrează lumânare cu lumânare cu df.iloc[i][...] și df_h.loc[i, [...]] = ...
În pandas fiecare astfel de operație costă zeci de microsecunde până la câteva milisecunde,
deci pe 10.000 de lumânări funcția durează ~75 s. Aici rulăm EXACT același cod al funcției
(același __code__), dar în locul lui `pd` îi dăm un tabel minimal pe liste Python, care știe
doar operațiile folosite de ea: DataFrame(), coloane, + - * /, .iloc[i][col] și
.loc[i, col sau [col]] = valoare. Pe 10.000 de lumânări durează câteva zeci de milisecunde.

Siguranță:
  - orice altă operație (ex. pd.concat, .shift(), max() pe coloane întregi) aruncă o eroare,
    iar atunci folosim funcția cu pandas, ca înainte;
  - la primul apel comparăm rezultatul cu cel din pandas, pe primele lumânări; dacă diferă,
    folosim mereu pandas (până la repornirea serverului).
"""

import math
import operator
import types

import pandas as pd


class Column:
    """O coloană: o listă de numere, cu operațiile aritmetice element cu element."""

    def __init__(self, values):
        self.v = list(values)

    def __len__(self):
        return len(self.v)

    def __iter__(self):
        return iter(self.v)

    def __bool__(self):
        # Ca în pandas: „if coloana:” e ambiguu.
        raise ValueError("Valoarea de adevăr a unei coloane e ambiguă")

    # Aplică operația f (ex. adunare) element cu element:
    #   - cu altă coloană: perechi (a[0], b[0]), (a[1], b[1]), ... (trebuie să aibă aceeași lungime);
    #   - cu un număr: același număr pentru fiecare element (ex. coloana / 4).
    # reverse=True când numărul / coloana e în STÂNGA operației (ex. 1 - coloana), ca ordinea să
    # rămână corectă la scădere și împărțire.
    def _op(self, other, f, reverse=False):
        if isinstance(other, Column):
            if len(other) != len(self):
                raise ValueError("Coloane de lungimi diferite")
            pairs = zip(other.v, self.v) if reverse else zip(self.v, other.v)
            return Column(f(a, b) for a, b in pairs)
        if isinstance(other, (int, float)):
            return Column(f(other, a) if reverse else f(a, other) for a in self.v)
        return NotImplemented

    # Operatorii + - * / ai Python-ului trec toți prin _op. Variantele cu „r” (__radd__ etc.)
    # se folosesc când coloana e în dreapta: 4 + coloana, 1 / coloana.
    def __add__(self, o): return self._op(o, operator.add)
    def __radd__(self, o): return self._op(o, operator.add, True)
    def __sub__(self, o): return self._op(o, operator.sub)
    def __rsub__(self, o): return self._op(o, operator.sub, True)
    def __mul__(self, o): return self._op(o, operator.mul)
    def __rmul__(self, o): return self._op(o, operator.mul, True)
    def __truediv__(self, o): return self._op(o, operator.truediv)
    def __rtruediv__(self, o): return self._op(o, operator.truediv, True)


class _Row:
    """Rândul i dintr-un tabel (ce întoarce .iloc[i]): row['open'] = valoarea din coloană."""

    def __init__(self, frame, i):
        self.frame, self.i = frame, i

    def __getitem__(self, name):
        return self.frame.cols[name].v[self.i]


# Ce întoarce tabel.iloc: tabel.iloc[i] dă rândul i, ca în pandas.
class _ILoc:
    def __init__(self, frame):
        self.frame = frame

    def __getitem__(self, i):
        if not isinstance(i, int):
            raise TypeError("fastframe: .iloc acceptă doar un număr de rând")
        n = len(self.frame)
        if not -n <= i < n:
            raise IndexError("fastframe: rând inexistent")
        # i % n transformă indicii negativi în poziții normale: iloc[-1] = ultimul rând.
        return _Row(self.frame, i % n)


class _Loc:
    """.loc[i, 'col'] / .loc[i, ['col']] = valoare. Indexul e 0..n-1, deci eticheta = poziția."""

    def __init__(self, frame):
        self.frame = frame

    # Desface cheia [rând, coloană] sau [rând, [coloane]] în (rând, listă de nume de coloane).
    # Orice altă formă (ex. felii 0:5, etichete text pe rânduri) aruncă eroare -> pandas.
    def _key(self, key):
        if not (isinstance(key, tuple) and len(key) == 2 and isinstance(key[0], int)):
            raise TypeError("fastframe: .loc acceptă doar [rând, coloană]")
        i, cols = key
        names = cols if isinstance(cols, list) else [cols]
        if not 0 <= i < len(self.frame) or not all(isinstance(c, str) for c in names):
            raise KeyError("fastframe: rând sau coloană nesuportate")
        return i, names

    def __getitem__(self, key):
        i, names = self._key(key)
        if isinstance(key[1], list):
            raise TypeError("fastframe: citirea .loc[i, [...]] nu e suportată")
        return self.frame.cols[names[0]].v[i]

    def __setitem__(self, key, value):
        i, names = self._key(key)
        if not isinstance(value, (int, float)):
            raise TypeError("fastframe: .loc primește doar un număr")
        for name in names:
            if name not in self.frame.cols:
                # Coloană nouă: ca în pandas, celelalte rânduri rămân goale (NaN).
                self.frame.cols[name] = Column([math.nan] * len(self.frame))
            self.frame.cols[name].v[i] = float(value)


class Frame:
    """Tabelul minimal: coloane cu nume, de aceeași lungime."""

    def __init__(self, data=None):
        # data = {nume coloană: listă de valori}; Frame() fără nimic = tabel gol.
        self.cols = {k: Column(v) for k, v in (data or {}).items()}

    def __len__(self):
        # Numărul de rânduri = lungimea oricărei coloane (toate au aceeași lungime).
        return len(next(iter(self.cols.values()))) if self.cols else 0

    def __getitem__(self, name):
        # tabel['close'] -> coloana close.
        return self.cols[name]

    # tabel['close'] = coloană -> pune (sau înlocuiește) coloana close, cu o copie a valorilor.
    def __setitem__(self, name, value):
        if not isinstance(value, Column):
            raise TypeError("fastframe: se poate atribui doar o coloană")
        if self.cols and len(value) != len(self):
            raise ValueError("fastframe: coloană de altă lungime")
        self.cols[name] = Column(value.v)

    @property
    def iloc(self):
        return _ILoc(self)

    @property
    def loc(self):
        return _Loc(self)


# Ce vede funcția în locul modulului pandas: doar DataFrame. Orice altceva (pd.concat etc.)
# aruncă AttributeError, deci se trece pe pandas.
_FAST_PD = types.SimpleNamespace(DataFrame=Frame)
COLUMNS = ("time", "open", "high", "low", "close")
_verified = {}   # funcție -> True (rezultat identic cu pandas) / False


def _fast_copy(func):
    """Aceeași funcție (același cod), dar cu `pd` = tabelul minimal."""
    g = dict(func.__globals__, pd=_FAST_PD)
    return types.FunctionType(func.__code__, g, func.__name__, func.__defaults__, func.__closure__)


# Varianta lentă, sigură: rulează funcția pe un DataFrame pandas adevărat.
# Lumânările vin cu coloana `time`, dar funcția așteaptă `time_utc` (ca în CSV-uri), deci
# o redenumim la intrare și înapoi la ieșire. Rezultatul: listă de dicționare, câte unul pe lumânare.
def _with_pandas(func, bars):
    df = pd.DataFrame(bars).rename(columns={"time": "time_utc"})
    out = func(df).rename(columns={"time_utc": "time"})
    return out[list(COLUMNS)].to_dict("records")


# Varianta rapidă: același lucru, dar pe tabelul minimal (Frame) în loc de pandas.
# Aruncă eroare dacă funcția face ceva ce Frame nu știe; run() prinde eroarea și trece pe pandas.
def _with_fast(func, bars):
    df = Frame({"time_utc": [b["time"] for b in bars],
                **{c: [float(b[c]) for b in bars] for c in ("open", "high", "low", "close")}})
    out = _fast_copy(func)(df)
    if not isinstance(out, Frame):
        raise TypeError("fastframe: funcția nu a întors un tabel")
    # Din coloane înapoi în rânduri: zip(*cols) ia câte o valoare din fiecare coloană.
    cols = [out.cols["time_utc" if c == "time" else c].v for c in COLUMNS]
    return [dict(zip(COLUMNS, row)) for row in zip(*cols)]


# True dacă cele două liste de lumânări sunt identice: aceeași oră și aceleași prețuri
# (cu o toleranță foarte mică, pentru diferențele de rotunjire dintre calcule).
def _same(a, b):
    return len(a) == len(b) and all(
        x["time"] == y["time"] and all(math.isclose(x[c], y[c], rel_tol=1e-12, abs_tol=1e-12)
                                       for c in COLUMNS[1:])
        for x, y in zip(a, b))


def run(func, bars: list[dict]) -> list[dict]:
    """Aplică func (ex. heikin_ashi) pe lumânări ({time, open, high, low, close}) și întoarce
    lumânările rezultate, în același format. Repede dacă se poate, altfel cu pandas."""
    # Funcția a picat deja verificarea (sau a folosit ceva nesuportat): direct pandas.
    if _verified.get(func) is False:
        return _with_pandas(func, bars)
    try:
        fast = _with_fast(func, bars)
    except Exception:  # noqa: BLE001 - funcția folosește ceva nesuportat: pandas
        _verified[func] = False
        return _with_pandas(func, bars)
    # Prima rulare: comparăm varianta rapidă cu pandas pe primele 50 de lumânări și ținem
    # minte rezultatul, ca verificarea să se facă o singură dată.
    if func not in _verified:
        sample = bars[:50]
        _verified[func] = _same(_with_fast(func, sample), _with_pandas(func, sample))
        if not _verified[func]:
            return _with_pandas(func, bars)
    return fast
