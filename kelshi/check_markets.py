import requests
res = requests.get("https://api.elections.kalshi.com/trade-api/v2/markets?limit=5")
print(res.json())
