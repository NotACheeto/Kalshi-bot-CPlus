import sys

with open('fast_main.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

out = []
skip = False
for line in lines:
    if 'buy_payload = {' in line:
        skip = True
        out.append('                buy_payload = {\n')
        out.append('                    "ticker": ticker, "client_order_id": f"b_{int(now*1000)}",\n')
        out.append('                    "action": "buy", "side": "yes", "count": 1, "yes_price": my_bid, "type": "limit",\n')
        out.append('                    "time_in_force": "gtc", "self_trade_prevention": "dc", "post_only": True\n')
        out.append('                }\n')
        continue
    elif 'sell_payload = {' in line:
        skip = True
        out.append('                sell_payload = {\n')
        out.append('                    "ticker": ticker, "client_order_id": f"s_{int(now*1000)}",\n')
        out.append('                    "action": "sell", "side": "yes", "count": 1, "yes_price": my_ask, "type": "limit",\n')
        out.append('                    "time_in_force": "gtc", "self_trade_prevention": "dc", "post_only": True\n')
        out.append('                }\n')
        continue
    
    if skip:
        if '}' in line and 'sell_payload' not in line and 'buy_payload' not in line:
            skip = False
        continue
    
    out.append(line)

with open('fast_main.py', 'w', encoding='utf-8') as f:
    f.writelines(out)
print("Cleanly rebuilt payload")
