import websocket
import json
import time
from config import KALSHI_API_KEY_ID, KALSHI_PRIVATE_KEY
from kalshi_api import KalshiClient

client = KalshiClient(KALSHI_API_KEY_ID, KALSHI_PRIVATE_KEY, env_mode="paper_prod")

timestamp = int(time.time() * 1000)
# WebSocket authentication requires signing GET /trade-api/ws/v2
signature = client._sign("GET", "/trade-api/ws/v2", timestamp)

headers = [
    f"KALSHI-ACCESS-KEY: {KALSHI_API_KEY_ID}",
    f"KALSHI-ACCESS-TIMESTAMP: {timestamp}",
    f"KALSHI-ACCESS-SIGNATURE: {signature}"
]

def on_message(ws, message):
    print("Received:", message)
    data = json.loads(message)
    if data.get("type") == "ticker":
        ws.close()

def on_error(ws, error):
    print("Error:", error)

def on_close(ws, close_status_code, close_msg):
    print("Closed")

def on_open(ws):
    print("Opened Connection")
    sub_msg = {
        "id": 1,
        "cmd": "subscribe",
        "params": {
            "channels": ["ticker"],
            "market_tickers": ["KXNFLGAME-26OCT04DALHOU-DAL"]
        }
    }
    ws.send(json.dumps(sub_msg))

ws_url = "wss://api.elections.kalshi.com/trade-api/ws/v2"
ws = websocket.WebSocketApp(ws_url, header=headers, on_open=on_open, on_message=on_message, on_error=on_error, on_close=on_close)
ws.run_forever()
