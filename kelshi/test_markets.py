import asyncio
import aiohttp
import websockets
import json
from config import KALSHI_API_KEY_ID, KALSHI_PRIVATE_KEY
from kalshi_api import KalshiClient
import time

client = KalshiClient(KALSHI_API_KEY_ID, KALSHI_PRIVATE_KEY, env_mode='prod')

async def test():
    timestamp = int(time.time() * 1000)
    signature = client._sign('GET', '/trade-api/ws/v2', timestamp)
    headers = {
        'KALSHI-ACCESS-KEY': KALSHI_API_KEY_ID,
        'KALSHI-ACCESS-TIMESTAMP': str(timestamp),
        'KALSHI-ACCESS-SIGNATURE': signature
    }
    try:
        async with websockets.connect('wss://api.elections.kalshi.com/trade-api/ws/v2', additional_headers=headers) as ws:
            await ws.send(json.dumps({'id': 1, 'cmd': 'subscribe', 'params': {'channels': ['ticker']}}))
            print("Subscribed. Waiting for 3 seconds...")
            for _ in range(5):
                msg = await asyncio.wait_for(ws.recv(), timeout=3.0)
                print("GOT:", msg[:100])
    except Exception as e:
        print("Timeout or Error:", e)

asyncio.run(test())
