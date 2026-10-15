"""
Configurarea inițială a conexiunii cu cTrader (se rulează o singură dată):

    python ctrader_setup.py

Cere tokenul obținut din Playground-ul aplicației tale (openapi.ctrader.com), alege contul și
salvează totul în ctrader_token.json. Apoi pornești serverul normal: python server.py
"""
# =====================================================================================
#  PAȘII (pe scurt)
# -------------------------------------------------------------------------------------
#  1. Citim CTRADER_CLIENT_ID și CTRADER_CLIENT_SECRET din .env (datele aplicației tale).
#  2. Ceri tokenul din Playground și îl lipești aici (access token + refresh token).
#  3. Reînnoim imediat tokenul: așa aflăm exact când expiră (serverul îl reînnoiește apoi singur).
#  4. Afișăm conturile la care are acces tokenul și îl alegi pe cel folosit pentru prețuri.
#  5. Ne conectăm la cont și afișăm un preț live, ca verificare.
# =====================================================================================
import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from ctrader import (ACCOUNT_AUTH_REQ, GET_ACCOUNTS_REQ, AuthError, Connection, CTraderError,
                     CTraderFeed, TokenStore, app_auth, refresh_token)

for _stream in (sys.stdout, sys.stderr):
    _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")
TOKEN_FILE = Path(os.getenv("CTRADER_TOKEN_FILE", ROOT / "ctrader_token.json"))

HELP = """
Cum obții tokenul:
  1. Intră pe https://openapi.ctrader.com/apps (logat cu cTrader ID-ul tău).
  2. La aplicația ta apasă „Playground”.
  3. Alege scope „accounts” (doar citire, suficient pentru prețuri) și apasă „Get token”.
  4. Autorizează conturile, apoi copiază de pe pagina afișată Access token și Refresh token.
"""


async def main(access: str, refresh: str, symbol: str):
    client_id = os.getenv("CTRADER_CLIENT_ID", "").strip()
    client_secret = os.getenv("CTRADER_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        sys.exit("Completează CTRADER_CLIENT_ID și CTRADER_CLIENT_SECRET în .env (vezi .env.example).")

    tokens = TokenStore(TOKEN_FILE)
    tokens.data = {"accessToken": access, "refreshToken": refresh, "expiresAt": 0}

    # Lista conturilor se poate cere de pe oricare server; folosim serverul demo.
    conn = Connection(live=False)
    await conn.open()
    try:
        await app_auth(conn, client_id, client_secret)
        print("✓ Aplicația a fost autorizată")
        await refresh_token(conn, tokens)
        res = await conn.request(GET_ACCOUNTS_REQ, {"accessToken": tokens.data["accessToken"]})
    finally:
        await conn.close()

    accounts = res.get("ctidTraderAccount", [])
    if not accounts:
        sys.exit("Tokenul nu are acces la niciun cont. Autorizează cel puțin un cont în Playground.")
    print("\nConturi disponibile:")
    for i, a in enumerate(accounts, 1):
        kind = "REAL" if a.get("isLive") else "demo"
        print(f"  {i}. {a.get('brokerTitleShort', '?'):<20} login {a.get('traderLogin', '?'):<12} {kind}")
    if len(accounts) == 1:
        acc = accounts[0]
    else:
        # Implicit: primul cont demo (sau primul cont, dacă nu există demo).
        default = next((i for i, a in enumerate(accounts, 1) if not a.get("isLive")), 1)
        pick = input(f"\nCe cont folosim pentru prețuri? [{default}]: ").strip() or str(default)
        acc = accounts[int(pick) - 1]

    tokens.data.update(accountId=int(acc["ctidTraderAccountId"]), isLive=bool(acc.get("isLive")),
                       login=acc.get("traderLogin"), broker=acc.get("brokerTitleShort", ""))
    tokens.save()
    print(f"\n✓ Salvat în {TOKEN_FILE.name}")

    # Verificare: ne conectăm exact ca serverul și așteptăm un preț live.
    feed = CTraderFeed(client_id, client_secret, tokens)
    task = asyncio.create_task(feed.run())
    try:
        await asyncio.wait_for(feed.ready.wait(), 30)
        name = feed.resolve(symbol)
        if name is None:
            print(f"(simbolul {symbol} nu există la acest broker; primele simboluri: "
                  f"{', '.join(feed.symbol_names()[0][:10])})")
            return
        feed.want({name})
        q = await feed.quote(name, wait=10)
        if q:
            print(f"✓ {name}: bid {q[1]}  ask {q[2]}")
        print("\nGata. Pornește serverul cu: python server.py")
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Configurarea conexiunii cu cTrader Open API")
    parser.add_argument("--access", help="Access token din Playground")
    parser.add_argument("--refresh", help="Refresh token din Playground")
    parser.add_argument("--symbol", default="EURUSD", help="simbolul folosit la verificare")
    args = parser.parse_args()
    if not args.access or not args.refresh:
        print(HELP)
    access = args.access or input("Access token:  ").strip()
    refresh = args.refresh or input("Refresh token: ").strip()
    try:
        asyncio.run(main(access, refresh, args.symbol))
    except (AuthError, CTraderError, ConnectionError, OSError) as e:
        sys.exit(f"\nEroare: {e}")
