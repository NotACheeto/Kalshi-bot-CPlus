import sys

with open('fast_main.py', 'r', encoding='utf-8') as f:
    code = f.read()

old = '''                buy_payload = {
                    "ticker": ticker, "client_order_id": f"b_{int(now*1000)}",
                    "action": "buy",
                    "side": "yes",
                    "count": 1, "yes_price": my_bid, "type": "limit",
                    "time_in_force": "gtc", 
                    "self_trade_prevention": "dc",
                    "buy_to_close": False,
                    "post_only": True # CRITICAL: Guarantees Maker Fees
                }
                sell_payload = {
                    "ticker": ticker, "client_order_id": f"s_{int(now*1000)}",
                    "action": "sell",
                    "side": "yes",
                    "count": 1, "yes_price": my_ask, "type": "limit",
                    "time_in_force": "gtc", 
                    "self_trade_prevention": "dc",
                    "buy_to_close": False,
                    "post_only": True # CRITICAL: Guarantees Maker Fees
                }'''

new = '''                buy_payload = {
                    "ticker": ticker, "client_order_id": f"b_{int(now*1000)}",
                    "action": "buy",
                    "side": "yes",
                    "count": 1, "yes_price": my_bid, "type": "limit",
                    "time_in_force": "gtc", 
                    "self_trade_prevention_type": "dc",
                    "buy_to_close": False,
                    "post_only": True # CRITICAL: Guarantees Maker Fees
                }
                sell_payload = {
                    "ticker": ticker, "client_order_id": f"s_{int(now*1000)}",
                    "action": "sell",
                    "side": "yes",
                    "count": 1, "yes_price": my_ask, "type": "limit",
                    "time_in_force": "gtc", 
                    "self_trade_prevention_type": "dc",
                    "buy_to_close": False,
                    "post_only": True # CRITICAL: Guarantees Maker Fees
                }'''

with open('fast_main.py', 'w', encoding='utf-8') as f:
    f.write(code.replace(old, new))
print("Patched payload 4")
