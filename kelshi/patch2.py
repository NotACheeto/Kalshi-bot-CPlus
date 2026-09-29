import sys

with open('fast_main.py', 'r', encoding='utf-8') as f:
    code = f.read()

old_block = '''                # Track active orders
                for res in results:
                    if res and "order" in res and "order_id" in res["order"]:'''

new_block = '''                # Track active orders
                for res in results:
                    if res and "error" in res:
                        logger.error(f"Kalshi API Error: {res['error']}")
                    if res and "order" in res and "order_id" in res["order"]:'''

if old_block in code:
    code = code.replace(old_block, new_block)
else:
    print("Block not found!")

with open('fast_main.py', 'w', encoding='utf-8') as f:
    f.write(code)
print("Patched logging")
