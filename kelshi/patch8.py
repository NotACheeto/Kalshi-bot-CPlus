import sys

with open('fast_main.py', 'r', encoding='utf-8') as f:
    code = f.read()

start_str = '# SAFETY LAYER 3: POST-ONLY GUARANTEE'
end_str = '# Send real HTTP requests concurrently to Kalshi'

start_idx = code.find(start_str)
end_idx = code.find(end_str)

if start_idx != -1 and end_idx != -1:
    new_block = '''# SAFETY LAYER 3: POST-ONLY GUARANTEE
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
                
                '''
    code = code[:start_idx] + new_block + code[end_idx:]
    with open('fast_main.py', 'w', encoding='utf-8') as f:
        f.write(code)
    print("Cleaned!")
else:
    print("Not found")
