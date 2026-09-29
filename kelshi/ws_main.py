import time
import logging
import json
import websocket
from config import KALSHI_API_KEY_ID, KALSHI_PRIVATE_KEY, TRADE_ENV
from kalshi_api import KalshiClient
from strategies import MarketMaker

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def main():
    logger.info("Starting Kalshi WebSocket Trading Bot...")
    
    if not KALSHI_API_KEY_ID or not KALSHI_PRIVATE_KEY:
        logger.error("Missing API Credentials. Please update your .env file.")
        return

    env_mode = TRADE_ENV.lower()
    client = KalshiClient(KALSHI_API_KEY_ID, KALSHI_PRIVATE_KEY, env_mode=env_mode)
    
    try:
        balance_info = client.get_balance()
        logger.info(f"Current Balance Info: {balance_info}")
    except Exception as e:
        logger.error(f"Failed to connect to Kalshi: {e}")
        return

    mm_strategy = MarketMaker(client, spread_threshold=1)
    
    # Track the last time we executed a ticker to avoid spamming REST calls on every single tick
    last_exec_time = {}
    COOLDOWN_SECONDS = 1.0

    def on_message(ws, message):
        try:
            data = json.loads(message)
            msg = data.get("msg", {})
            ticker = msg.get("market_ticker")
            
            if data.get("type") == "ticker" and ticker and "CROSSCATEGORY" not in ticker:
                now = time.time()
                # Rate limit REST calls to avoid hitting Kalshi's 10 req/s limit
                if now - last_exec_time.get(ticker, 0) > COOLDOWN_SECONDS:
                    last_exec_time[ticker] = now
                    mm_strategy.execute(ticker)
        except Exception as e:
            logger.error(f"Error parsing WebSocket message: {e}")

    def on_error(ws, error):
        logger.error(f"WebSocket Error: {error}")

    def on_close(ws, close_status_code, close_msg):
        logger.info("WebSocket connection closed.")

    def on_open(ws):
        logger.info("WebSocket connected! Subscribing to real-time market data...")
        sub_msg = {
            "id": 1,
            "cmd": "subscribe",
            "params": {
                "channels": ["ticker"]
            }
        }
        ws.send(json.dumps(sub_msg))

    # WebSocket Authentication
    timestamp = int(time.time() * 1000)
    signature = client._sign("GET", "/trade-api/ws/v2", timestamp)
    headers = [
        f"KALSHI-ACCESS-KEY: {KALSHI_API_KEY_ID}",
        f"KALSHI-ACCESS-TIMESTAMP: {timestamp}",
        f"KALSHI-ACCESS-SIGNATURE: {signature}"
    ]
    
    ws_url = "wss://api.elections.kalshi.com/trade-api/ws/v2" if env_mode in ["prod", "paper_prod"] else "wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2"
    
    ws = websocket.WebSocketApp(
        ws_url,
        header=headers,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )
    
    logger.info("Listening for real-time WebSocket events...")
    ws.run_forever()

if __name__ == "__main__":
    from config import enforce_single_instance
    enforce_single_instance(port=18335)  # Use port 18335 for ws_main.py
    main()
