import os
import sys
import json
from pathlib import Path
import asyncio
import websockets
from datetime import datetime

from dotenv import load_dotenv

#se creaza path-ul folderului parinte
ROOT = Path(__file__).parent

load_dotenv(ROOT / ".env")   


# obtin client_id si client_secret din .env

client_id = os.getenv("CTRADER_CLIENT_ID", "").strip()
client_secret = os.getenv("CTRADER_CLIENT_SECRET", "").strip()
if not client_id or not client_secret:
    sys.exit("Completează CTRADER_CLIENT_ID și CTRADER_CLIENT_SECRET în .env ...")

TOKEN_FILE = ROOT / "ctrader_token.json"

#obtin access_token, account_id si is_live din ctrader_token.json
if not TOKEN_FILE.exists():
    sys.exit("Lipsește ctrader_token.json - rulează: python ctrader_setup.py")
token_data = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))

access_token = token_data["accessToken"]
account_id = int(token_data["accountId"])
is_live = bool(token_data.get("isLive"))

#obtin simbolul
SYMBOL = "EURUSD"
HOST = "wss://live.ctraderapi.com:5036" if is_live else "wss://demo.ctraderapi.com:5036"

BROWSER_PORT = 8765
clients = set()

#trimite un mesaj catre cTrader
async def send(ws, payload_type, payload):
    await ws.send(json.dumps({"payloadType": payload_type, "payload": payload}))

#citeste mesajele pana il primeste pe cel asteptat
async def wait_for(ws, payload_type):
    while True:
        msg = json.loads(await ws.recv())
        if msg["payloadType"] == payload_type:
            return msg.get("payload", {})
        if msg["payloadType"] in (50, 2142):
            sys.exit(f"Eroare de la cTrader: {msg['payload']}")

#se asigura ca nu suntem deconectati de la server
async def heartbeat(ws):
    while True:
        await asyncio.sleep(10)
        await send(ws, 51, {})

#tine evidenta browserelor conectate
async def browser_handler(ws):
    clients.add(ws)
    try:
        await ws.wait_closed()
    finally:
        clients.discard(ws)

async def main():

    await websockets.serve(browser_handler, "localhost", BROWSER_PORT)

    async with websockets.connect(HOST) as ws:
        # 1. autorizez aplicatia
        await send(ws, 2100, {"clientId": client_id, "clientSecret": client_secret})
        await wait_for(ws, 2101)

        # 2. autorizez contul
        await send(ws, 2102, {"ctidTraderAccountId": account_id, "accessToken": access_token})
        await wait_for(ws, 2103)

        # 3. cer lista de simboluri si caut SYMBOL
        await send(ws, 2114, {"ctidTraderAccountId": account_id})
        res = await wait_for(ws, 2115)

        symbol_id = None
        for s in res["symbol"]:
            if s.get("symbolName") == SYMBOL:
                symbol_id = s["symbolId"]
                break

        if symbol_id is None:
            sys.exit(f"Simbolul {SYMBOL} nu exista la acest broker")

        # 4. ma abonez la preturile simbolului
        await send(ws, 2127, {"ctidTraderAccountId": account_id, "symbolId": [symbol_id]})
        await wait_for(ws, 2128)
        print(f"Abonat la {SYMBOL} (symbolId {symbol_id}). Astept tick-uri...")

        # 5. pornesc heartbeat-ul in paralel
        asyncio.create_task(heartbeat(ws))

        # 6. citesc tick-urile la nesfarsit
        bid = None
        ask = None
        while True:
            msg = json.loads(await ws.recv())

            #daca e orice altfel de mesaj inafara de tick, il ignora
            if msg["payloadType"] != 2131:
                continue

            p = msg["payload"]

            #daca e orice altfel de simbol inafara de cel cautat, il ignora
            if p.get("symbolId") != symbol_id:
                continue

            #imi extrage bid-ul si ask-ul
            if "bid" in p:
                bid = p["bid"] / 100000
            if "ask" in p:
                ask = p["ask"] / 100000

            ora = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            print(f"{ora}  {SYMBOL}  id={symbol_id}  bid={bid}  ask={ask}")

            websockets.broadcast(clients, f"{ora}  {SYMBOL}  bid={bid}  ask={ask}")

asyncio.run(main())

