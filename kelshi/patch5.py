import sys

with open('fast_main.py', 'r', encoding='utf-8') as f:
    code = f.read()

old_1 = '''                buy_payload = {
                    "ticker": ticker, "client_order_id": f"b_{int(now*1000)}",
                    "side": "bid", "count": "1.00", "price": f"0.{my_bid:02d}00",
                    "self_trade_prevention_type": "dc",
                    "time_in_force": "good_till_canceled", 
                    "post_only": True # CRITICAL: Guarantees Maker Fees
                }'''

old_2 = '''                sell_payload = {
                    "ticker": ticker, "client_order_id": f"s_{int(now*1000)}",
                    "side": "ask", "count": "1.00", "price": f"0.{my_ask:02d}00",
                    "self_trade_prevention_type": "dc",
                    "time_in_force": "good_till_canceled", 
                    "post_only": True # CRITICAL: Guarantees Maker Fees
                }'''

new_1 = '''                buy_payload = {
                    "ticker": ticker, "client_order_id": f"b_{int(now*1000)}",
                    "action": "buy", "side": "yes", "count": 1, "yes_price": my_bid, "type": "limit",
                    "self_trade_prevention": "dc",
                    "time_in_force": "gtc", 
                    "post_only": True # CRITICAL: Guarantees Maker Fees
                }'''

new_2 = '''                sell_payload = {
                    "ticker": ticker, "client_order_id": f"s_{int(now*1000)}",
                    "action": "sell", "side": "yes", "count": 1, "yes_price": my_ask, "type": "limit",
                    "self_trade_prevention": "dc",
                    "time_in_force": "gtc", 
                    "post_only": True # CRITICAL: Guarantees Maker Fees
                }'''

code = code.replace(old_1, new_1)
code = code.replace(old_2, new_2)

# Fix the side logic
old_append = '''                            "side": res["order"]["side"],
                            "price": my_bid if res["order"]["side"] == "bid" else my_ask,
                            "timestamp": time.time()
                        })'''

# In V2, the response 'side' is usually 'yes' or 'no'. The action is 'buy' or 'sell'.
# But wait, we can just use the order_id. We know buy_payload is bid, sell_payload is ask.
# Actually, the parser says: res["order"]["side"] == "bid"
# Let's see what Kalshi V2 returns for side when we place an order.
# Just to be safe, I'll let it use what it has, but I need to make sure the side matches what we expect in the Paper loop.
# The Paper Loop expects: order["side"] == "bid" or "ask".
# Let's fix the appending logic to hardcode "bid" for buy, "ask" for sell by checking the client_order_id!
new_append = '''                            "side": "bid" if str(res["order"]["client_order_id"]).startswith("b") else "ask",
                            "price": my_bid if str(res["order"]["client_order_id"]).startswith("b") else my_ask,
                            "timestamp": time.time()
                        })'''

code = code.replace(old_append, new_append)

with open('fast_main.py', 'w', encoding='utf-8') as f:
    f.write(code)
print("Patched payload 5")
