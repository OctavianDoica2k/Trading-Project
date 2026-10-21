"""
Datele indicatorului „Trend multi-timeframe”: lumânările D1 ale simbolului din datastore
(datastore/<sursă>/<SIMBOL>/D1/<SIMBOL>_D1.csv), din anul curent, în forma pe care o citește
timeframe_trends_indicator() din analysis.py.
"""
# =====================================================================================
#  CE FACE ACEST FIȘIER (pe scurt)
# -------------------------------------------------------------------------------------
#  Baza de date e datastore-ul D1 al simbolului, cu regulile lui (datastore.py): dacă nu există,
#  se creează (ultimele 10.000 de lumânări închise); dacă e învechit, se verifică capătul și se
#  adaugă zilele lipsă până la ultima zi închisă. Server.py face asta (Store.sync) înainte de
#  fiecare calcul; aici doar alegem zilele anului și le punem data zilei de tranzacționare.
#
#  Datastore-ul păstrează ora UTC a deschiderii („2026-09-30 21:00:00” = ziua de 1 octombrie,
#  pentru că lumânarea D1 a brokerului începe la miezul nopții ORA SERVERULUI). analysis.py
#  primește în schimb data zilei („2026-10-01 00:00:00”): altfel 1 octombrie ar apărea ca
#  30 septembrie, iar filtrul de lună l-ar scoate din lună. Fișierul din datastore nu se schimbă.
# =====================================================================================
from datetime import date, datetime, timezone


# Data zilei de tranzacționare a unei lumânări D1 care începe la `t` (secunde UTC).
# MT5: miezul nopții în fusul orar al serverului (MT5_SERVER_TZ, feed.tz). cTrader nu are un fus
# orar al serverului: ziua începe la 21:00–22:00 UTC (17:00 New York) sau la 00:00 UTC, deci o
# deschidere după 18:00 UTC aparține zilei următoare.
def day_of(feed, t: int) -> date:
    tz = getattr(feed, "tz", None)
    if tz is not None:
        return datetime.fromtimestamp(t, tz).date()
    return datetime.fromtimestamp(t + 6 * 3600, timezone.utc).date()


def year_rows(feed, bars: list[dict], year: int) -> list[list]:
    """Rândurile anului `year` (time_utc = data zilei, open, high, low, close, volume)."""
    rows = []
    for b in bars:
        d = day_of(feed, b["time"])
        if d.year == year:
            rows.append([f"{d.isoformat()} 00:00:00", b["open"], b["high"], b["low"], b["close"],
                         b.get("volume", 0)])
    return rows
