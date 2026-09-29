import asyncio
import aiohttp
import uuid
import time
from config import KALSHI_API_KEY_ID, KALSHI_PRIVATE_KEY
from fast_main import KalshiClient

async def test():
    client = KalshiClient(KALSHI_API_KEY_ID, KALSHI_PRIVATE_KEY)
    async with aiohttp.ClientSession() as session:
        for stp in ["dc", "cmo", "cto", "cb", "DC", "CMO", "cancel_rest", "cancel_maker", "decrement_and_cancel"]:
            payload = {
                "ticker": "KXGOLDH-26SEP2915-T4147.99",
                "client_order_id": str(uuid.uuid4()),
                "action": "buy", "side": "yes", "count": 1, "yes_price": 5, "type": "limit",
                "time_in_force": "gtc",
                "self_trade_prevention": stp,
                "buy_to_close": False,
                "post_only": True
            }
            timestamp = int(time.time() * 1000)
            sig = client._sign("POST", "/trade-api/v2/portfolio/events/orders", timestamp)
            headers = {"KALSHI-ACCESS-KEY": KALSHI_API_KEY_ID, "KALSHI-ACCESS-TIMESTAMP": str(timestamp), "KALSHI-ACCESS-SIGNATURE": sig, "Content-Type": "application/json"}
            url = "https://api.elections.kalshi.com/trade-api/v2/portfolio/events/orders"
            async with session.post(url, headers=headers, json=payload) as resp:
                res = await resp.json()
                print(f"Testing STP: {stp} -> {res.get('error', res.get('order', 'SUCCESS'))}")

asyncio.run(test())
