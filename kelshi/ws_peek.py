import asyncio
import websockets
import json
import time
from kalshi_api import KalshiClient
from config import KALSHI_API_KEY_ID, KALSHI_PRIVATE_KEY

async def peek():
    client = KalshiClient(KALSHI_API_KEY_ID, KALSHI_PRIVATE_KEY)
    timestamp = int(time.time() * 1000)
    signature = client._sign("GET", "/trade-api/ws/v2", timestamp)
    
    headers = {
        "KALSHI-ACCESS-KEY": KALSHI_API_KEY_ID,
        "KALSHI-ACCESS-TIMESTAMP": str(timestamp),
        "KALSHI-ACCESS-SIGNATURE": signature
    }
    
    uri = "wss://api.elections.kalshi.com/trade-api/ws/v2"
    async with websockets.connect(uri, additional_headers=headers) as ws:
        sub = {
            "id": 1,
            "cmd": "subscribe",
            "params": {"channels": ["ticker"]}
        }
        await ws.send(json.dumps(sub))
        
        while True:
            msg = await ws.recv()
            data = json.loads(msg)
            if data.get("type") == "ticker":
                print(json.dumps(data, indent=2))
                break

asyncio.run(peek())
