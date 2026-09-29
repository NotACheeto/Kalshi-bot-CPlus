import re

with open('fast_main.py', 'r', encoding='utf-8') as f:
    code = f.read()

# Use regex to replace the entire buy_payload block
code = re.sub(r'buy_payload = \{[^}]+\}', '''buy_payload = {
                    "ticker": ticker, "client_order_id": f"b_{int(now*1000)}",
                    "action": "buy", "side": "yes", "count": 1, "yes_price": my_bid, "type": "limit",
                    "time_in_force": "gtc", "self_trade_prevention": "dc", "post_only": True
                }''', code)

code = re.sub(r'sell_payload = \{[^}]+\}', '''sell_payload = {
                    "ticker": ticker, "client_order_id": f"s_{int(now*1000)}",
                    "action": "sell", "side": "yes", "count": 1, "yes_price": my_ask, "type": "limit",
                    "time_in_force": "gtc", "self_trade_prevention": "dc", "post_only": True
                }''', code)

with open('fast_main.py', 'w', encoding='utf-8') as f:
    f.write(code)
print("Regex replace done")
