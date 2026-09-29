import sys

with open('fast_main.py', 'r', encoding='utf-8') as f:
    code = f.read()

# 1. Hyper Sweeper (10s to 4s)
code = code.replace('> 10:', '> 4:')
code = code.replace('(10s timeout)', '(4s timeout)')

# 2. Volume Filter
filter_code = '''    if not best_yes_bid or not best_yes_ask:
        return
        
    valid_prefixes = ["KXBTC", "KXETH", "KXSOLD", "KXDOGE", "KXSP500", "KXNASDAQ", "KXNFL", "KXWTI", "KXGOLD", "KXSILVER"]
    if not any(ticker.startswith(p) for p in valid_prefixes):
        return'''

code = code.replace('''    if not best_yes_bid or not best_yes_ask:
        return''', filter_code)

# 3. Spread Squeeze
old_spread_logic = '''        if spread >= SPREAD_THRESHOLD:
            # Apply the inventory skew to our resting limit prices
            my_bid = (best_yes_bid + 1) - skew
            my_ask = (best_yes_ask - 1) - skew
            
            # SAFETY: Clamp prices to strictly valid ranges (1c to 98c for bids, 2c to 99c for asks)
            my_bid = max(1, min(98, my_bid))
            my_ask = max(2, min(99, my_ask))'''

new_spread_logic = '''        if spread >= 2:
            if spread == 2:
                my_bid = best_yes_bid - skew
                my_ask = best_yes_ask - skew
            else:
                my_bid = (best_yes_bid + 1) - skew
                my_ask = (best_yes_ask - 1) - skew
            
            # SAFETY: Clamp prices to strictly valid ranges (1c to 98c for bids, 2c to 99c for asks)
            my_bid = max(1, min(98, my_bid))
            my_ask = max(2, min(99, my_ask))'''

if old_spread_logic in code:
    code = code.replace(old_spread_logic, new_spread_logic)
else:
    print("Spread logic not found!")

with open('fast_main.py', 'w', encoding='utf-8') as f:
    f.write(code)
print("Patched all 3 upgrades!")
