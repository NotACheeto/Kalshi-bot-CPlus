import sys

with open('fast_main.py', 'r', encoding='utf-8') as f:
    code = f.read()

old_payload = '''                buy_payload = {
                    "ticker": ticker, "client_order_id": f"b_{int(now*1000)}",
                    "side": "bid", "count": "1.00", "price": f"0.{my_bid:02d}00",
                    "time_in_force": "good_till_canceled", 
                    "post_only": True # CRITICAL: Guarantees Maker Fees
                }
                sell_payload = {
                    "ticker": ticker, "client_order_id": f"s_{int(now*1000)}",
                    "side": "ask", "count": "1.00", "price": f"0.{my_ask:02d}00",
                    "time_in_force": "good_till_canceled", 
                    "post_only": True # CRITICAL: Guarantees Maker Fees
                }'''

new_payload = '''                buy_payload = {
                    "ticker": ticker, "client_order_id": f"b_{int(now*1000)}",
                    "side": "bid", "count": "1", "price": f"{my_bid}", "type": "limit", "action": "buy",
                    "self_trade_prevention_type": "dc",
                    "time_in_force": "good_till_canceled", 
                    "post_only": True # CRITICAL: Guarantees Maker Fees
                }
                sell_payload = {
                    "ticker": ticker, "client_order_id": f"s_{int(now*1000)}",
                    "side": "ask", "count": "1", "price": f"{my_ask}", "type": "limit", "action": "buy",
                    "self_trade_prevention_type": "dc",
                    "time_in_force": "good_till_canceled", 
                    "post_only": True # CRITICAL: Guarantees Maker Fees
                }'''

if old_payload in code:
    code = code.replace(old_payload, new_payload)
else:
    print("Block not found!")

# Wait, let me just add self_trade_prevention_type: "dc" and keep the old format just in case
old_payload_safe = '''                buy_payload = {
                    "ticker": ticker, "client_order_id": f"b_{int(now*1000)}",
                    "side": "bid", "count": "1.00", "price": f"0.{my_bid:02d}00",
                    "time_in_force": "good_till_canceled", 
                    "post_only": True # CRITICAL: Guarantees Maker Fees
                }
                sell_payload = {
                    "ticker": ticker, "client_order_id": f"s_{int(now*1000)}",
                    "side": "ask", "count": "1.00", "price": f"0.{my_ask:02d}00",
                    "time_in_force": "good_till_canceled", 
                    "post_only": True # CRITICAL: Guarantees Maker Fees
                }'''

new_payload_safe = '''                buy_payload = {
                    "ticker": ticker, "client_order_id": f"b_{int(now*1000)}",
                    "side": "bid", "count": 1, "price": my_bid, "type": "limit", "action": "buy",
                    "time_in_force": "good_till_canceled", 
                    "self_trade_prevention": "dc",
                    "buy_to_close": False,
                    "post_only": True # CRITICAL: Guarantees Maker Fees
                }
                sell_payload = {
                    "ticker": ticker, "client_order_id": f"s_{int(now*1000)}",
                    "side": "ask", "count": 1, "price": my_ask, "type": "limit", "action": "sell",
                    "time_in_force": "good_till_canceled", 
                    "self_trade_prevention": "dc",
                    "buy_to_close": False,
                    "post_only": True # CRITICAL: Guarantees Maker Fees
                }'''

with open('fast_main.py', 'w', encoding='utf-8') as f:
    f.write(code.replace(old_payload, '''                buy_payload = {
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
                }'''))
print("Patched payload")
