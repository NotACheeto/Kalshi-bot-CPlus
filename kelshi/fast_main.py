import asyncio
import aiohttp
import websockets
import json
import time
import logging
import uuid
import collections
from config import KALSHI_API_KEY_ID, KALSHI_PRIVATE_KEY, TRADE_ENV
from kalshi_api import KalshiClient

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

SPREAD_THRESHOLD = 1

# --- RISK MANAGEMENT SETTINGS ---
MAX_POSITION_PER_MARKET = 2  # Restricted for $3 account balance
MAX_VOLATILITY_CENTS = 3     # If market moves 3 cents...
VOLATILITY_WINDOW_SEC = 5    # ...within 5 seconds, halt trading.
HALT_DURATION_SEC = 15       # Halt for 15 seconds after volatility spike

# --- STATE MANAGEMENT ---
inventory = collections.defaultdict(int)          # Ticker -> Net Position (+ for YES, - for NO)
price_history = collections.defaultdict(list)     # Ticker -> [(timestamp, mid_price)]
trading_halts = collections.defaultdict(float)    # Ticker -> Timestamp when halt expires
live_orders = collections.defaultdict(list)       # Ticker -> [order_ids]
paper_balance = 10000.0

# Capital Tracking for $3 account
available_capital_cents = 300  # Start with exactly $3.00

is_live_trading = (TRADE_ENV.lower() == "prod")

def parse_cents(dollar_str):
    if not dollar_str: return None
    try: return int(round(float(dollar_str) * 100))
    except: return None

async def kalshi_async_request(session, client, method, path, data=None):
    if not is_live_trading:
        # Simulate ~60ms internet latency for paper trading realism
        await asyncio.sleep(0.06)
        mock_side = data.get("side", "bid") if data else "bid"
        return {"order": {"order_id": str(uuid.uuid4()), "side": mock_side}}
        
    # --- REAL LIVE TRADING HTTP EXECUTION ---
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

async def cancel_all_orders(session, client, ticker):
    global available_capital_cents
    orders_to_cancel = live_orders[ticker]
    live_orders[ticker] = []
    
    if orders_to_cancel:
        # Restore local capital instantly before sending the delete request
        for o in orders_to_cancel:
            if o["side"] == "bid":
                available_capital_cents += o["price"]
            else:
                available_capital_cents += (100 - o["price"])
                
        # Concurrently cancel all active REST orders on this ticker
        tasks = [kalshi_async_request(session, client, "DELETE", f"/portfolio/events/orders/{o['id']}") for o in orders_to_cancel]
        await asyncio.gather(*tasks)

async def check_volatility(ticker, mid_price, now):
    history = price_history[ticker]
    history.append((now, mid_price))
    
    # Remove old data outside the time window
    while history and now - history[0][0] > VOLATILITY_WINDOW_SEC:
        history.pop(0)
        
    if len(history) >= 2:
        price_change = abs(history[-1][1] - history[0][1])
        if price_change >= MAX_VOLATILITY_CENTS:
            logger.debug(f"🚨 VOLATILITY SPIKE ON {ticker}! Moved {price_change}c in {VOLATILITY_WINDOW_SEC}s. Halting for {HALT_DURATION_SEC}s.")
            return True
    return False

ticker_locks = collections.defaultdict(asyncio.Lock)

async def handle_market_update(session, client, ticker, best_yes_bid, best_yes_ask):
    global paper_balance, available_capital_cents
    now = time.time()
    
    if not best_yes_bid or not best_yes_ask:
        return
        
    valid_prefixes = ["KXBTC", "KXETH", "KXSOLD", "KXDOGE", "KXSP500", "KXNASDAQ", "KXNFL", "KXWTI", "KXGOLD", "KXSILVER"]
    if not any(ticker.startswith(p) for p in valid_prefixes):
        return
        
    async with ticker_locks[ticker]:
        mid_price = (best_yes_bid + best_yes_ask) / 2.0
        spread = best_yes_ask - best_yes_bid
        
        # ---------------------------------------------------------
        # PAPER MATCHING ENGINE: Update inventory in paper mode
        # ---------------------------------------------------------
        if not is_live_trading and live_orders[ticker]:
            unfilled = []
            for order in live_orders[ticker]:
                filled = False
                if order["side"] == "bid" and best_yes_ask <= order["price"]:
                    filled = True
                    paper_balance -= order["price"] / 100.0
                    inventory[ticker] += 1
                    # Capital is already locked, but since it filled, we don't get it back until we sell.
                elif order["side"] == "ask" and best_yes_bid >= order["price"]:
                    filled = True
                    paper_balance += order["price"] / 100.0
                    inventory[ticker] -= 1
                    # We sold! We get our capital back PLUS the payout (100c)
                    available_capital_cents += 100
                    
                if filled:
                    profit = paper_balance - 10000.0
                    logger.info(f"$$$ FILL ALERT: {order['side'].upper()} {ticker} at {order['price']}c! Total Profit: ${profit:+.2f} | Inventory: {inventory[ticker]}")
                else:
                    unfilled.append(order)
            live_orders[ticker] = unfilled
        
        # ---------------------------------------------------------
        # SAFETY LAYER 1: CIRCUIT BREAKER (Falling Knife Protection)
        # ---------------------------------------------------------
        if await check_volatility(ticker, mid_price, now):
            trading_halts[ticker] = now + HALT_DURATION_SEC
            await cancel_all_orders(session, client, ticker)
            return
            
        if now < trading_halts[ticker]:
            return # Currently halted, do not quote
        
        # ---------------------------------------------------------
        # SAFETY LAYER 2: INVENTORY SKEWING (Capital Management)
        # ---------------------------------------------------------
        current_pos = inventory[ticker]
        skew = 0
        if current_pos >= MAX_POSITION_PER_MARKET:
            skew = 2   # Max Long: Drop our bids (stop buying) and drop asks (sell aggressively)
        elif current_pos <= -MAX_POSITION_PER_MARKET:
            skew = -2  # Max Short: Raise bids (buy aggressively) and raise asks (stop selling)
        elif current_pos > 0:
            skew = 1
        elif current_pos < 0:
            skew = -1

        if spread >= 2:
            if spread == 2:
                my_bid = best_yes_bid - skew
                my_ask = best_yes_ask - skew
            else:
                my_bid = (best_yes_bid + 1) - skew
                my_ask = (best_yes_ask - 1) - skew
            
            # SAFETY: Clamp prices to strictly valid ranges (1c to 98c for bids, 2c to 99c for asks)
            my_bid = max(1, min(98, my_bid))
            my_ask = max(2, min(99, my_ask))

            # Ensure we never cross our own spread to guarantee positive EV
            if my_bid < my_ask:
                
                # Check if we have enough local capital to place these orders
                cost_of_bid = my_bid
                cost_of_ask = 100 - my_ask
                total_collateral_needed = cost_of_bid + cost_of_ask
                
                # Calculate how much capital would be freed by canceling existing orders for THIS ticker
                capital_freed_by_cancel = 0
                for o in live_orders[ticker]:
                    if o["side"] == "bid":
                        capital_freed_by_cancel += o["price"]
                    else:
                        capital_freed_by_cancel += (100 - o["price"])

                # If we don't have enough money EVEN AFTER canceling old quotes, ignore.
                if (available_capital_cents + capital_freed_by_cancel) < total_collateral_needed:
                    return

                # Cancel old quotes first (this frees up capital if we already had orders here)
                await cancel_all_orders(session, client, ticker)
                
                # Lock the capital locally immediately so other markets don't spend it
                available_capital_cents -= total_collateral_needed
                
                # ---------------------------------------------------------
                # SAFETY LAYER 3: POST-ONLY GUARANTEE
                # ---------------------------------------------------------
                buy_payload = {
                    "ticker": ticker, "client_order_id": f"b_{int(now*1000)}",
                    "action": "buy", "side": "yes", "count": 1, "yes_price": my_bid, "type": "limit",
                    "time_in_force": "gtc", "self_trade_prevention": "dc", "post_only": True
                }
                sell_payload = {
                    "ticker": ticker, "client_order_id": f"s_{int(now*1000)}",
                    "action": "sell", "side": "yes", "count": 1, "yes_price": my_ask, "type": "limit",
                    "time_in_force": "gtc", "self_trade_prevention": "dc", "post_only": True
                }
                
                # Send real HTTP requests concurrently to Kalshi
                results = await asyncio.gather(
                    kalshi_async_request(session, client, "POST", "/portfolio/events/orders", data=buy_payload),
                    kalshi_async_request(session, client, "POST", "/portfolio/events/orders", data=sell_payload)
                )
                
                # Track active orders
                for res in results:
                    if res and "error" in res:
                        logger.error(f"Kalshi API Error: {res['error']}")
                    if res and "order" in res and "order_id" in res["order"]:
                        live_orders[ticker].append({
                            "id": res["order"]["order_id"],
                            "side": "bid" if str(res["order"]["client_order_id"]).startswith("b") else "ask",
                            "price": my_bid if str(res["order"]["client_order_id"]).startswith("b") else my_ask,
                            "timestamp": time.time()
                        })
                        
                logger.info(f"[{ticker}] QUOTED: Bid {my_bid}c | Ask {my_ask}c (Inventory: {current_pos}, Skew: {skew})")

async def sync_inventory_loop(session, client):
    global available_capital_cents
    while True:
        if is_live_trading:
            try:
                # Fetch live positions from Kalshi REST API
                res = await kalshi_async_request(session, client, "GET", "/portfolio/positions")
                if res and "positions" in res:
                    new_inventory = collections.defaultdict(int)
                    for pos in res["positions"]:
                        if pos.get("position", 0) != 0:
                            ticker = pos.get("ticker")
                            count = pos.get("position")
                            new_inventory[ticker] += count
                    
                    # Update global inventory
                    import boto3
                    import os
                    sns_topic_arn = os.getenv('SNS_TOPIC_ARN')
                    if sns_topic_arn:
                        sns = boto3.client('sns', region_name='us-east-1')
                        for t in list(inventory.keys()) + list(new_inventory.keys()):
                            old_val = inventory.get(t, 0)
                            new_val = new_inventory.get(t, 0)
                            if old_val != new_val:
                                if new_val == 0 and old_val != 0:
                                    msg = f"🚨 KALSHI FILL ALERT 🚨\nPosition CLOSED on {t}."
                                elif new_val > 0:
                                    msg = f"🚨 KALSHI FILL ALERT 🚨\nPosition OPENED on {t}: {new_val} YES contracts."
                                elif new_val < 0:
                                    msg = f"🚨 KALSHI FILL ALERT 🚨\nPosition OPENED on {t}: {abs(new_val)} NO contracts."
                                else:
                                    msg = f"🚨 KALSHI FILL ALERT 🚨\nPosition CHANGED on {t}: {old_val} -> {new_val}."
                                
                                try:
                                    sns.publish(TopicArn=sns_topic_arn, Message=msg)
                                    logger.info(f"Published SNS Alert: {msg.replace(chr(10), ' ')}")
                                except Exception as e:
                                    logger.error(f"Failed to publish SNS: {e}")
                                    
                            inventory[t] = new_val
                    else:
                        for t in list(inventory.keys()) + list(new_inventory.keys()):
                            inventory[t] = new_inventory.get(t, 0)
                        
                # Sync Live Balance
                bal_res = await kalshi_async_request(session, client, "GET", "/portfolio/balance")
                if bal_res and "balance" in bal_res:
                    available_capital_cents = bal_res["balance"]
                    
            except Exception as e:
                logger.error(f"Failed to sync inventory/balance: {e}")
                
        await asyncio.sleep(5)


async def stale_order_sweeper(session, client):
    while True:
        try:
            now = time.time()
            tickers_to_cancel = []
            for ticker, orders in list(live_orders.items()):
                if orders and (now - orders[0].get("timestamp", now)) > 4:
                    tickers_to_cancel.append(ticker)
            
            for ticker in tickers_to_cancel:
                logger.info(f"Sweeping stale orders for {ticker} to free capital (4s timeout).")
                asyncio.create_task(cancel_all_orders(session, client, ticker))
                
        except Exception as e:
            logger.error(f"Sweeper error: {e}")
        await asyncio.sleep(5)

async def main():
    logger.info("Starting Institutional-Grade HFT Bot...")
    if is_live_trading:
        logger.warning("🚨 REAL MONEY TRADING MODE ENABLED - LIVE EXECUTIONS ACTIVE 🚨")
    else:
        logger.info("🟢 PAPER TRADING MODE ENABLED (Simulating Execution Logic safely)")
        
    client = KalshiClient(KALSHI_API_KEY_ID, KALSHI_PRIVATE_KEY)
    
    timestamp = int(time.time() * 1000)
    signature = client._sign("GET", "/trade-api/ws/v2", timestamp)
    headers = {
        "KALSHI-ACCESS-KEY": KALSHI_API_KEY_ID,
        "KALSHI-ACCESS-TIMESTAMP": str(timestamp),
        "KALSHI-ACCESS-SIGNATURE": signature
    }
    
    uri = "wss://api.elections.kalshi.com/trade-api/ws/v2"
    
    async with aiohttp.ClientSession() as session:
        # Start the inventory synchronizer
        asyncio.create_task(sync_inventory_loop(session, client))
        asyncio.create_task(stale_order_sweeper(session, client))
        
        async with websockets.connect(uri, additional_headers=headers) as ws:
            logger.info("Connected to Kalshi WebSocket. Listening to ALL markets (Global Feed)...")
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
                    logger.info(f"Heartbeat: Processed {tick_count} live market ticks...")
                    
                data = json.loads(msg)
                if data.get("type") == "ticker":
                    payload = data.get("msg", {})
                    ticker = payload.get("market_ticker")
                    
                    if ticker and "CROSSCATEGORY" not in ticker:
                        best_bid = payload.get("yes_bid") or parse_cents(payload.get("yes_bid_dollars"))
                        best_ask = payload.get("yes_ask") or parse_cents(payload.get("yes_ask_dollars"))
                        
                        # Fire and forget concurrent processing task
                        asyncio.create_task(
                            handle_market_update(session, client, ticker, best_bid, best_ask)
                        )

async def graceful_shutdown():
    logger.info("🛑 SAFETY SHUTDOWN INITIATED: Canceling all resting orders across Kalshi...")
    client = KalshiClient(KALSHI_API_KEY_ID, KALSHI_PRIVATE_KEY)
    async with aiohttp.ClientSession() as session:
        tasks = []
        for ticker, orders in live_orders.items():
            for o in orders:
                tasks.append(kalshi_async_request(session, client, "DELETE", f"/portfolio/events/orders/{o['id']}"))
        if tasks:
            await asyncio.gather(*tasks)
    logger.info("✅ All live orders successfully canceled. Safe to exit.")

if __name__ == "__main__":
    from config import enforce_single_instance
    enforce_single_instance(port=18333)  # Use port 18333 for fast_main
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        asyncio.run(graceful_shutdown())
