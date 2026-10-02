"""
Kalshi Fast Async WebSocket HFT Market Maker (Local/Paper/Live)
"""

import asyncio
import aiohttp
import websockets
import json
import time
import logging
import uuid
import socket
import collections
from typing import Dict, List, Optional

from config import KALSHI_API_KEY_ID, KALSHI_PRIVATE_KEY, TRADE_ENV
from kalshi_api import KalshiClient
from market_making_core import (
    OrderBook,
    VolatilityCircuitBreaker,
    InventoryRiskManager,
    CollateralManager,
    Quote,
    CONTRACT_PAYOUT,
    MIN_TICK_PRICE,
    MAX_TICK_PRICE,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

# --- CORE PARAMETERS ---
SPREAD_THRESHOLD = 2
MAX_POSITION_PER_MARKET = 2
MAX_VOLATILITY_CENTS = 3
VOLATILITY_WINDOW_SEC = 5
HALT_DURATION_SEC = 15
INITIAL_CAPITAL_CENTS = 300  # $3.00

is_live_trading = (TRADE_ENV.lower() == "prod")

circuit_breaker = VolatilityCircuitBreaker(
    max_volatility_cents=MAX_VOLATILITY_CENTS,
    window_sec=VOLATILITY_WINDOW_SEC,
    halt_duration_sec=HALT_DURATION_SEC,
)
risk_manager = InventoryRiskManager(
    max_position=MAX_POSITION_PER_MARKET,
    min_spread_to_quote=SPREAD_THRESHOLD,
)
collateral_mgr = CollateralManager(initial_capital_cents=INITIAL_CAPITAL_CENTS)

resting_orders: Dict[str, Dict[str, Optional[dict]]] = collections.defaultdict(dict)
paper_balance = 10000.0
ticker_locks = collections.defaultdict(asyncio.Lock)


async def kalshi_async_request(session: aiohttp.ClientSession, client: KalshiClient,
                               method: str, path: str, data: Optional[dict] = None) -> Optional[dict]:
    if not is_live_trading:
        await asyncio.sleep(0.03)
        return {"order": {"order_id": str(uuid.uuid4())}}

    timestamp = int(time.time() * 1000)
    signature = client._sign(method, "/trade-api/v2" + path, timestamp)
    headers = {
        "KALSHI-ACCESS-KEY": KALSHI_API_KEY_ID,
        "KALSHI-ACCESS-TIMESTAMP": str(timestamp),
        "KALSHI-ACCESS-SIGNATURE": signature,
        "Content-Type": "application/json"
    }
    url = f"https://api.elections.kalshi.com/trade-api/v2{path}"

    try:
        if method == "GET":
            async with session.get(url, headers=headers) as resp:
                return await resp.json()
        elif method == "POST":
            async with session.post(url, headers=headers, json=data) as resp:
                return await resp.json()
        elif method == "DELETE":
            async with session.delete(url, headers=headers) as resp:
                return await resp.json()
    except Exception as e:
        logger.error(f"HTTP Error on {method} {path}: {e}")
        return None


async def cancel_order_async(session: aiohttp.ClientSession, client: KalshiClient, order_id: str,
                             ticker: str, side: str, price: int):
    if side == "bid":
        collateral_mgr.refund_bid(price, 1)
    else:
        collateral_mgr.refund_ask(price, 1)

    if order_id:
        await kalshi_async_request(session, client, "DELETE", f"/portfolio/orders/{order_id}")


async def cancel_all_for_ticker(session: aiohttp.ClientSession, client: KalshiClient, ticker: str):
    orders = resting_orders.get(ticker, {})
    tasks = []
    for side in ["bid", "ask"]:
        info = orders.get(side)
        if info and info.get("id"):
            tasks.append(cancel_order_async(session, client, info["id"], ticker, side, info["price"]))
            orders[side] = None
    if tasks:
        await asyncio.gather(*tasks)


async def handle_market_update(session: aiohttp.ClientSession, client: KalshiClient,
                               ticker: str, best_yes_bid: int, best_yes_ask: int):
    global paper_balance
    now = time.time()

    valid_prefixes = ["KXBTC", "KXETH", "KXSOLD", "KXDOGE", "KXSP500", "KXNASDAQ", "KXNFL", "KXWTI", "KXGOLD", "KXSILVER"]
    if not any(ticker.startswith(p) for p in valid_prefixes):
        return

    async with ticker_locks[ticker]:
        ob = OrderBook.from_prices(ticker, best_yes_bid, best_yes_ask)
        if not ob.is_valid() or ob.is_crossed():
            return

        mid_price = ob.mid_price()

        # 1. Paper Matching
        if not is_live_trading:
            cur_quotes = resting_orders[ticker]
            bid_info = cur_quotes.get("bid")
            if bid_info and best_yes_ask <= bid_info["price"]:
                paper_balance -= bid_info["price"] / 100.0
                risk_manager.apply_fill(ticker, "bid", 1)
                collateral_mgr.on_bid_fill(bid_info["price"], 1)
                cur_quotes["bid"] = None
                profit = paper_balance - 10000.0
                logger.info(f"$$$ FILL ALERT: BUY {ticker} at {bid_info['price']}c | Profit: ${profit:+.2f} | Pos: {risk_manager.get_position(ticker)}")

            ask_info = cur_quotes.get("ask")
            if ask_info and best_yes_bid >= ask_info["price"]:
                paper_balance += ask_info["price"] / 100.0
                risk_manager.apply_fill(ticker, "ask", 1)
                collateral_mgr.on_ask_fill(ask_info["price"], 1)
                cur_quotes["ask"] = None
                profit = paper_balance - 10000.0
                logger.info(f"$$$ FILL ALERT: SELL {ticker} at {ask_info['price']}c | Profit: ${profit:+.2f} | Pos: {risk_manager.get_position(ticker)}")

        # 2. Circuit Breaker
        if circuit_breaker.on_price_update(ticker, mid_price, now):
            logger.warning(f"🚨 VOLATILITY SPIKE ON {ticker}! Halting for {HALT_DURATION_SEC}s.")
            await cancel_all_for_ticker(session, client, ticker)
            return

        if circuit_breaker.is_halted(ticker, now):
            return

        # 3. Compute Quote
        quote = risk_manager.compute_quote(ob)
        if not quote.valid:
            return

        cur_bid = resting_orders[ticker].get("bid")
        cur_ask = resting_orders[ticker].get("ask")

        # 4. Smart Order Management (Queue Priority Retained)
        if quote.quote_bid:
            if cur_bid and cur_bid.get("price") == quote.bid_price:
                pass  # Price unchanged -> retain order in queue!
            else:
                if cur_bid:
                    await cancel_order_async(session, client, cur_bid["id"], ticker, "bid", cur_bid["price"])
                    resting_orders[ticker]["bid"] = None

                single_bid_quote = Quote(ticker)
                single_bid_quote.bid_price = quote.bid_price
                single_bid_quote.quote_ask = False
                if collateral_mgr.can_afford_quote(single_bid_quote):
                    collateral_mgr.reserve_for_quote(single_bid_quote)
                    payload = {
                        "ticker": ticker,
                        "client_order_id": f"b_{int(now*1000)}",
                        "action": "buy",
                        "side": "yes",
                        "count": 1,
                        "yes_price": quote.bid_price,
                        "type": "limit",
                        "time_in_force": "good_till_canceled",
                        "self_trade_prevention_type": "maker",
                        "post_only": True
                    }
                    res = await kalshi_async_request(session, client, "POST", "/portfolio/orders", data=payload)
                    order_id = res.get("order", {}).get("order_id") if res else None
                    resting_orders[ticker]["bid"] = {"id": order_id, "price": quote.bid_price}
        else:
            if cur_bid:
                await cancel_order_async(session, client, cur_bid["id"], ticker, "bid", cur_bid["price"])
                resting_orders[ticker]["bid"] = None

        if quote.quote_ask:
            if cur_ask and cur_ask.get("price") == quote.ask_price:
                pass  # Price unchanged -> retain order in queue!
            else:
                if cur_ask:
                    await cancel_order_async(session, client, cur_ask["id"], ticker, "ask", cur_ask["price"])
                    resting_orders[ticker]["ask"] = None

                single_ask_quote = Quote(ticker)
                single_ask_quote.ask_price = quote.ask_price
                single_ask_quote.quote_bid = False
                if collateral_mgr.can_afford_quote(single_ask_quote):
                    collateral_mgr.reserve_for_quote(single_ask_quote)
                    payload = {
                        "ticker": ticker,
                        "client_order_id": f"s_{int(now*1000)}",
                        "action": "sell",
                        "side": "yes",
                        "count": 1,
                        "yes_price": quote.ask_price,
                        "type": "limit",
                        "time_in_force": "good_till_canceled",
                        "self_trade_prevention_type": "maker",
                        "post_only": True
                    }
                    res = await kalshi_async_request(session, client, "POST", "/portfolio/orders", data=payload)
                    order_id = res.get("order", {}).get("order_id") if res else None
                    resting_orders[ticker]["ask"] = {"id": order_id, "price": quote.ask_price}
        else:
            if cur_ask:
                await cancel_order_async(session, client, cur_ask["id"], ticker, "ask", cur_ask["price"])
                resting_orders[ticker]["ask"] = None


async def sync_inventory_loop(session: aiohttp.ClientSession, client: KalshiClient):
    while True:
        if is_live_trading:
            try:
                res = await kalshi_async_request(session, client, "GET", "/portfolio/positions")
                if res and "positions" in res:
                    for pos in res["positions"]:
                        count = pos.get("position", 0)
                        ticker = pos.get("ticker")
                        if ticker:
                            risk_manager.set_position(ticker, count)

                bal_res = await kalshi_async_request(session, client, "GET", "/portfolio/balance")
                if bal_res and "balance" in bal_res:
                    collateral_mgr.reset(bal_res["balance"])
            except Exception as e:
                logger.error(f"Failed to sync inventory/balance: {e}")
        await asyncio.sleep(5)


async def main():
    logger.info("Starting Python HFT Market Maker...")
    if is_live_trading:
        logger.warning("🚨 REAL MONEY TRADING ACTIVE 🚨")
    else:
        logger.info("🟢 PAPER TRADING MODE ACTIVE")

    client = KalshiClient(KALSHI_API_KEY_ID, KALSHI_PRIVATE_KEY)

    timestamp = int(time.time() * 1000)
    signature = client._sign("GET", "/trade-api/ws/v2", timestamp)
    headers = {
        "KALSHI-ACCESS-KEY": KALSHI_API_KEY_ID,
        "KALSHI-ACCESS-TIMESTAMP": str(timestamp),
        "KALSHI-ACCESS-SIGNATURE": signature
    }

    uri = "wss://api.elections.kalshi.com/trade-api/ws/v2"

    connector = aiohttp.TCPConnector(limit=100, force_close=False, enable_cleanup_closed=True)
    async with aiohttp.ClientSession(connector=connector) as session:
        asyncio.create_task(sync_inventory_loop(session, client))

        async with websockets.connect(uri, additional_headers=headers) as ws:
            logger.info("Connected to Kalshi WebSocket. Subscribing to ticker channel...")
            sub = {
                "id": 1,
                "cmd": "subscribe",
                "params": {"channels": ["ticker"]}
            }
            await ws.send(json.dumps(sub))

            tick_count = 0
            while True:
                msg = await ws.recv()
                tick_count += 1
                if tick_count % 1000 == 0:
                    logger.info(f"Heartbeat: Processed {tick_count} live market updates...")

                data = json.loads(msg)
                if data.get("type") == "ticker":
                    payload = data.get("msg", {})
                    ticker = payload.get("market_ticker")

                    if ticker and "CROSSCATEGORY" not in ticker:
                        best_bid = payload.get("yes_bid") or OrderBook.parse_dollar_or_cents(payload.get("yes_bid_dollars"))
                        best_ask = payload.get("yes_ask") or OrderBook.parse_dollar_or_cents(payload.get("yes_ask_dollars"))

                        if best_bid > 0 and best_ask > 0:
                            asyncio.create_task(
                                handle_market_update(session, client, ticker, best_bid, best_ask)
                            )


async def graceful_shutdown():
    logger.info("🛑 Safety shutdown: Canceling resting orders...")
    client = KalshiClient(KALSHI_API_KEY_ID, KALSHI_PRIVATE_KEY)
    connector = aiohttp.TCPConnector(limit=100, force_close=False, enable_cleanup_closed=True)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = []
        for ticker in list(resting_orders.keys()):
            tasks.append(cancel_all_for_ticker(session, client, ticker))
        if tasks:
            await asyncio.gather(*tasks)
    logger.info("✅ All live orders canceled. Safe exit.")


if __name__ == "__main__":
    from config import enforce_single_instance
    enforce_single_instance(port=18333)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        asyncio.run(graceful_shutdown())
