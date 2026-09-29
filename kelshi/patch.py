import sys

with open('fast_main.py', 'r', encoding='utf-8') as f:
    code = f.read()

old_append = '''                        live_orders[ticker].append({
                            "id": res["order"]["order_id"],
                            "side": res["order"]["side"],
                            "price": my_bid if res["order"]["side"] == "bid" else my_ask
                        })'''
new_append = '''                        live_orders[ticker].append({
                            "id": res["order"]["order_id"],
                            "side": res["order"]["side"],
                            "price": my_bid if res["order"]["side"] == "bid" else my_ask,
                            "timestamp": time.time()
                        })'''

if old_append in code:
    code = code.replace(old_append, new_append)
else:
    print('Failed to find append block')

sweeper_code = '''
async def stale_order_sweeper(session, client):
    while True:
        try:
            now = time.time()
            tickers_to_cancel = []
            for ticker, orders in list(live_orders.items()):
                if orders and (now - orders[0].get("timestamp", now)) > 15:
                    tickers_to_cancel.append(ticker)
            
            for ticker in tickers_to_cancel:
                logger.info(f"Sweeping stale orders for {ticker} to free capital (15s timeout).")
                asyncio.create_task(cancel_all_orders(session, client, ticker))
                
        except Exception as e:
            logger.error(f"Sweeper error: {e}")
        await asyncio.sleep(5)

async def main():'''

code = code.replace('async def main():', sweeper_code)

sync_start = 'asyncio.create_task(sync_inventory_loop(session, client))'
if sync_start in code:
    code = code.replace(sync_start, sync_start + '\n        asyncio.create_task(stale_order_sweeper(session, client))')
else:
    print('Failed to find sync loop start')

with open('fast_main.py', 'w', encoding='utf-8') as f:
    f.write(code)
print('Patched successfully')
