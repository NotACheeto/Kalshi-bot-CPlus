import time
import logging
from config import KALSHI_API_KEY_ID, KALSHI_PRIVATE_KEY, TRADE_ENV, TICK_INTERVAL_SECONDS
from kalshi_api import KalshiClient
from strategies import MarketMaker, Arbitrageur

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def main():
    logger.info("Starting Kalshi Trading Bot...")
    
    if not KALSHI_API_KEY_ID or not KALSHI_PRIVATE_KEY:
        logger.error("Missing API Credentials. Please update your .env file.")
        return

    env_mode = TRADE_ENV.lower()
    if env_mode == "paper_prod":
        logger.info("Running in PAPER PROD mode: Using live data but logging simulated trades locally.")
    elif env_mode == "demo":
        logger.info("Running in DEMO (Paper Trading) mode on Kalshi Demo servers.")
    else:
        logger.warning("Running in PRODUCTION mode! Real funds are at risk.")

    # Initialize Client
    client = KalshiClient(KALSHI_API_KEY_ID, KALSHI_PRIVATE_KEY, env_mode=env_mode)

    try:
        balance_info = client.get_balance()
        logger.info(f"Current Balance Info: {balance_info}")
    except Exception as e:
        logger.error(f"Failed to connect to Kalshi: {e}")
        return

    # Initialize Strategies (adjusted for lower risk)
    mm_strategy = MarketMaker(client, spread_threshold=1)
    arb_strategy = Arbitrageur(client)

    logger.info(f"Beginning trading loop. Tick interval: {TICK_INTERVAL_SECONDS} seconds.")
    
    while True:
        try:
            # 1. Fetch active markets dynamically
            markets_data = client.get_markets(limit=100, status="open")
            active_markets = [(m['ticker'], m.get('title', m['ticker'])) 
                              for m in markets_data.get('markets', [])]

            # 2. Run strategies on targets
            for ticker, event_title in active_markets:
                mm_strategy.execute(ticker)
                # Removed Arbitrageur execution to run purely on internal Kalshi data

        except KeyboardInterrupt:
            logger.info("Bot stopped by user.")
            break
        except Exception as e:
            logger.error(f"Unexpected error in main loop: {e}")

        logger.info("-" * 40)
        time.sleep(TICK_INTERVAL_SECONDS)

if __name__ == "__main__":
    from config import enforce_single_instance
    enforce_single_instance(port=18334)  # Use port 18334 for main.py
    main()
