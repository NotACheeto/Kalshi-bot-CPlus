import requests
res = requests.get("https://api.elections.kalshi.com/trade-api/v2/markets/KXNFLGAME-26OCT04DALHOU-DAL/orderbook")
print(res.json())
