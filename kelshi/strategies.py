import logging
import requests
import time
from config import ODDS_API_KEY

logger = logging.getLogger(__name__)

class MarketMaker:
    def __init__(self, kalshi_client, spread_threshold=4, position_limit=50):
        self.kalshi = kalshi_client
        self.spread_threshold = spread_threshold
        self.position_limit = position_limit

    def execute(self, ticker):
        """
        Pure Market Making (No External Data):
        1. Cancels old resting orders for this ticker.
        2. Finds the current orderbook.
        3. If the spread is wider than `spread_threshold`, places a bid and ask just inside.
        """
        logger.info(f"Analyzing {ticker} for Market Making opportunities...")
        try:
            # 1. Cancel existing resting orders to prevent order buildup
            try:
                resting_orders = self.kalshi.get_orders(ticker=ticker, status="resting")
                orders_list = resting_orders.get("orders", [])
                for order in orders_list:
                    logger.info(f"Canceling stale resting order {order.get('order_id')} for {ticker}")
                    self.kalshi.cancel_order(order.get('order_id'))
            except Exception as e:
                logger.error(f"Error canceling orders for {ticker}: {e}")

            # 2. Get orderbook
            orderbook = self.kalshi.get_order_book(ticker)
            ob_fp = orderbook.get("orderbook_fp", {})
            yes_bids_raw = ob_fp.get("yes_dollars", [])
            no_bids_raw = ob_fp.get("no_dollars", [])

            yes_bids = [int(round(float(level[0]) * 100)) for level in yes_bids_raw]
            no_bids = [int(round(float(level[0]) * 100)) for level in no_bids_raw]

            best_yes_bid = max(yes_bids) if yes_bids else None
            best_no_bid = max(no_bids) if no_bids else None
            best_yes_ask = (100 - best_no_bid) if best_no_bid is not None else None

            # PAPER TRADING HOOK: Check if our local resting orders got filled by the live market movement
            if hasattr(self.kalshi, 'simulate_fills'):
                self.kalshi.simulate_fills(ticker, best_yes_bid, best_yes_ask)

            if not best_yes_bid or not best_yes_ask:
                logger.debug(f"Only one-sided or empty book for {ticker}, skipping.")
                return

            spread = best_yes_ask - best_yes_bid
            logger.info(f"{ticker} - Best YES Bid: {best_yes_bid}c, Best YES Ask: {best_yes_ask}c (Spread: {spread}c)")

            # 3. Place new quotes if spread is sufficiently wide
            if spread >= self.spread_threshold:
                if spread <= 2:
                    # For extremely tight spreads, we must join the existing queue.
                    my_bid = best_yes_bid
                    my_ask = best_yes_ask
                    logger.info(f"Spread is tight ({spread}c). Joining the queue at {my_bid}c Bid / {my_ask}c Ask")
                else:
                    # For wider spreads, we step inside to get queue priority
                    my_bid = best_yes_bid + 1
                    my_ask = best_yes_ask - 1
                    logger.info(f"Found {spread}c spread! Stepping inside: Bidding {my_bid}c, Asking {my_ask}c")

                # Safety check
                if my_bid < my_ask:
                    # Cancel existing open orders to prevent position buildup
                    open_orders = self.kalshi.get_orders(ticker=ticker, status="resting")
                    for order in open_orders.get('orders', []):
                        self.kalshi.cancel_order(order['order_id'])

                    self.kalshi.place_order(ticker, action="buy", side="yes", count=1, price=my_bid)
                    self.kalshi.place_order(ticker, action="sell", side="yes", count=1, price=my_ask)
                else:
                    logger.info(f"Calculated crossed or zero spread (Bid: {my_bid}, Ask: {my_ask}), skipping.")
            else:
                logger.info(f"Spread ({spread}c) is too tight to safely market make.")
        except Exception as e:
            logger.error(f"Error in MarketMaker for {ticker}: {e}")

class Arbitrageur:
    def __init__(self, kalshi_client):
        self.kalshi = kalshi_client
        self.odds_cache = {}
        self.last_fetch = 0
        self.CACHE_TTL = 300 # 5 minutes

    def fetch_nfl_odds(self):
        """
        Fetches NFL odds from The Odds API and caches them.
        """
        now = time.time()
        if now - self.last_fetch < self.CACHE_TTL and self.odds_cache:
            return self.odds_cache
            
        if not ODDS_API_KEY or ODDS_API_KEY == "your_odds_api_key_here":
            logger.warning("No valid ODDS_API_KEY found.")
            return {}
            
        logger.info("Fetching fresh NFL odds from The Odds API...")
        try:
            url = f"https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds"
            params = {
                "apiKey": ODDS_API_KEY,
                "regions": "us",
                "markets": "h2h",
                "oddsFormat": "decimal"
            }
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            
            new_cache = {}
            for event in data:
                bookmakers = event.get("bookmakers", [])
                if not bookmakers: continue
                
                # We'll just take DraftKings or the first available bookmaker for simplicity
                target_book = None
                for book in bookmakers:
                    if book.get("key") == "draftkings":
                        target_book = book
                        break
                if not target_book:
                    target_book = bookmakers[0]
                    
                market = target_book.get("markets", [])[0]
                outcomes = market.get("outcomes", [])
                
                for outcome in outcomes:
                    team_name = outcome.get("name")
                    decimal_odds = outcome.get("price")
                    if decimal_odds:
                        implied_prob = (1 / decimal_odds) * 100
                        new_cache[team_name] = round(implied_prob, 2)
                        
            self.odds_cache = new_cache
            self.last_fetch = now
            logger.info(f"Cached odds for {len(new_cache)} NFL teams.")
            return self.odds_cache
            
        except Exception as e:
            logger.error(f"Error fetching from The Odds API: {e}")
            return self.odds_cache

    def get_fair_value(self, event_title):
        """
        Tries to match the kalshi event_title to a team in the cache.
        """
        odds = self.fetch_nfl_odds()
        if not odds: return None
        
        # See if any team name from the Odds API matches the Kalshi market title
        for team, prob in odds.items():
            # Example Kalshi title: "Kansas City Chiefs to win"
            # Example Odds API team: "Kansas City Chiefs"
            if team.lower() in event_title.lower():
                return prob
                
        return None

    def execute(self, ticker, event_title):
        """
        Arbitrage Strategy:
        Compares the price on Kalshi to external books.
        """
        logger.info(f"Analyzing {ticker} ({event_title}) for Arbitrage opportunities...")
        try:
            external_implied_prob = self.get_fair_value(event_title)
            if not external_implied_prob:
                logger.info(f"No external odds found for {event_title}. Skipping Arbitrage.")
                return

            orderbook = self.kalshi.get_order_book(ticker)
            yes_bids = orderbook.get("yes", [])
            yes_asks = orderbook.get("no", []) 

            if not yes_bids or not yes_asks:
                logger.info(f"Empty order book for {ticker}, skipping Arbitrage.")
                return

            best_yes_bid = max([level[0] for level in yes_bids]) if yes_bids else None
            best_yes_ask = min([100 - level[0] for level in yes_asks]) if yes_asks else None

            logger.info(f"{ticker} - Kalshi Ask: {best_yes_ask}c, External Fair Value: {external_implied_prob}c")

            # Increased threshold to 8 cents for lower risk
            if best_yes_ask and best_yes_ask < (external_implied_prob - 8):
                logger.info(f"Arbitrage Found! Kalshi Price ({best_yes_ask}) is 8c+ lower than external ({external_implied_prob}).")
                self.kalshi.place_order(ticker, action="buy", side="yes", count=1, price=best_yes_ask)

            elif best_yes_bid and best_yes_bid > (external_implied_prob + 8):
                logger.info(f"Arbitrage Found! Kalshi Price ({best_yes_bid}) is 8c+ higher than external ({external_implied_prob}).")
                self.kalshi.place_order(ticker, action="buy", side="no", count=1, price=100 - best_yes_bid)

        except Exception as e:
            logger.error(f"Error in Arbitrageur for {ticker}: {e}")
