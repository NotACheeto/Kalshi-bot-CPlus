import logging
import requests
import time
from typing import Optional, Dict
from config import ODDS_API_KEY
from market_making_core import (
    OrderBook,
    InventoryRiskManager,
    VolatilityCircuitBreaker,
    ArbitrageEngine,
    Quote,
    CONTRACT_PAYOUT,
)

logger = logging.getLogger(__name__)


class MarketMaker:
    def __init__(self, kalshi_client, spread_threshold: int = 2, max_position: int = 2):
        self.kalshi = kalshi_client
        self.spread_threshold = spread_threshold
        self.risk_manager = InventoryRiskManager(max_position=max_position, min_spread_to_quote=spread_threshold)
        self.circuit_breaker = VolatilityCircuitBreaker(max_volatility_cents=3, window_sec=5, halt_duration_sec=15)
        # Track resting orders: ticker -> {"bid": {"id": ..., "price": ...}, "ask": {"id": ..., "price": ...}}
        self.resting_quotes: Dict[str, Dict[str, dict]] = {}

    def execute(self, ticker: str):
        """
        Market Making Execution with Queue Priority Retention:
        1. Fetch current orderbook.
        2. Check circuit breaker.
        3. Compute target quote with inventory skewing & one-sided limits.
        4. If resting quotes already match target price, RETAIN them (preserve FIFO queue priority).
        5. If target price changed or halted, cancel & update.
        """
        logger.info(f"Analyzing {ticker} for Market Making opportunities...")
        try:
            # 1. Get orderbook
            orderbook_data = self.kalshi.get_order_book(ticker)
            ob_dict = orderbook_data.get("orderbook", {}) or orderbook_data.get("orderbook_fp", {})
            
            yes_raw = ob_dict.get("yes", []) or ob_dict.get("yes_dollars", [])
            no_raw = ob_dict.get("no", []) or ob_dict.get("no_dollars", [])

            yes_bids = [OrderBook.parse_dollar_or_cents(level[0]) for level in yes_raw if level]
            no_bids = [OrderBook.parse_dollar_or_cents(level[0]) for level in no_raw if level]

            best_yes_bid = max(yes_bids) if yes_bids else 0
            best_no_bid = max(no_bids) if no_bids else 0
            best_yes_ask = (CONTRACT_PAYOUT - best_no_bid) if best_no_bid > 0 else 0

            # Simulation hook
            if hasattr(self.kalshi, 'simulate_fills'):
                self.kalshi.simulate_fills(ticker, best_yes_bid, best_yes_ask)

            ob = OrderBook.from_prices(ticker, best_yes_bid, best_yes_ask)
            if not ob.is_valid():
                logger.debug(f"Only one-sided or empty book for {ticker}, skipping.")
                return

            if ob.is_crossed():
                logger.warning(f"Crossed orderbook detected on {ticker} ({best_yes_bid}c >= {best_yes_ask}c), skipping.")
                return

            now = time.time()
            mid_price = ob.mid_price()

            # 2. Circuit Breaker Check
            if self.circuit_breaker.on_price_update(ticker, mid_price, now):
                logger.warning(f"CIRCUIT BREAKER TRIGGERED on {ticker}! Halting quotes.")
                self._cancel_all_ticker_orders(ticker)
                return

            if self.circuit_breaker.is_halted(ticker, now):
                return

            # 3. Compute Quote
            quote = self.risk_manager.compute_quote(ob)
            if not quote.valid:
                logger.info(f"[{ticker}] Quote suppressed: {quote.reason}")
                return

            # 4. Smart Order Management (Preserves FIFO queue priority)
            self._reconcile_orders(ticker, quote)

        except Exception as e:
            logger.error(f"Error in MarketMaker for {ticker}: {e}")

    def _cancel_all_ticker_orders(self, ticker: str):
        ticker_quotes = self.resting_quotes.get(ticker, {})
        for side in ["bid", "ask"]:
            if side in ticker_quotes and ticker_quotes[side]:
                try:
                    self.kalshi.cancel_order(ticker_quotes[side]["id"])
                except Exception as e:
                    logger.debug(f"Error canceling {side} order: {e}")
        self.resting_quotes[ticker] = {}

    def _reconcile_orders(self, ticker: str, quote: Quote):
        if ticker not in self.resting_quotes:
            self.resting_quotes[ticker] = {}

        current_bid = self.resting_quotes[ticker].get("bid")
        current_ask = self.resting_quotes[ticker].get("ask")

        # Handle Bid
        if quote.quote_bid:
            if current_bid and current_bid.get("price") == quote.bid_price:
                # Price is unchanged -> RETAIN order to keep queue priority!
                logger.debug(f"[{ticker}] Keeping resting bid at {quote.bid_price}c (Queue Priority Retained)")
            else:
                if current_bid:
                    self.kalshi.cancel_order(current_bid["id"])
                res = self.kalshi.place_order(ticker, action="buy", side="yes", count=1, price=quote.bid_price)
                order_id = res.get("order", {}).get("order_id") if res else None
                self.resting_quotes[ticker]["bid"] = {"id": order_id, "price": quote.bid_price}
        else:
            if current_bid:
                self.kalshi.cancel_order(current_bid["id"])
                self.resting_quotes[ticker]["bid"] = None

        # Handle Ask
        if quote.quote_ask:
            if current_ask and current_ask.get("price") == quote.ask_price:
                # Price is unchanged -> RETAIN order to keep queue priority!
                logger.debug(f"[{ticker}] Keeping resting ask at {quote.ask_price}c (Queue Priority Retained)")
            else:
                if current_ask:
                    self.kalshi.cancel_order(current_ask["id"])
                res = self.kalshi.place_order(ticker, action="sell", side="yes", count=1, price=quote.ask_price)
                order_id = res.get("order", {}).get("order_id") if res else None
                self.resting_quotes[ticker]["ask"] = {"id": order_id, "price": quote.ask_price}
        else:
            if current_ask:
                self.kalshi.cancel_order(current_ask["id"])
                self.resting_quotes[ticker]["ask"] = None


class Arbitrageur:
    def __init__(self, kalshi_client, threshold: float = 8.0):
        self.kalshi = kalshi_client
        self.threshold = threshold
        self.odds_cache: Dict[str, float] = {}
        self.last_fetch = 0.0
        self.CACHE_TTL = 300.0  # 5 minutes

    def fetch_nfl_odds(self) -> Dict[str, float]:
        now = time.time()
        if (now - self.last_fetch) < self.CACHE_TTL and self.odds_cache:
            return self.odds_cache

        if not ODDS_API_KEY or ODDS_API_KEY == "your_odds_api_key_here":
            logger.warning("No valid ODDS_API_KEY found.")
            return {}

        logger.info("Fetching fresh NFL odds from The Odds API...")
        try:
            url = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds"
            params = {
                "apiKey": ODDS_API_KEY,
                "regions": "us",
                "markets": "h2h",
                "oddsFormat": "decimal"
            }
            response = requests.get(url, params=params, timeout=5)
            response.raise_for_status()
            data = response.json()

            new_cache = {}
            for event in data:
                bookmakers = event.get("bookmakers", [])
                if not bookmakers:
                    continue

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
                    if decimal_odds and decimal_odds > 1.0:
                        implied_prob = (1.0 / decimal_odds) * 100.0
                        new_cache[team_name] = round(implied_prob, 2)

            self.odds_cache = new_cache
            self.last_fetch = now
            logger.info(f"Cached odds for {len(new_cache)} NFL teams.")
            return self.odds_cache

        except Exception as e:
            logger.error(f"Error fetching from The Odds API: {e}")
            return self.odds_cache

    def get_fair_value(self, event_title: str) -> Optional[float]:
        odds = self.fetch_nfl_odds()
        if not odds:
            return None

        for team, prob in odds.items():
            if team.lower() in event_title.lower():
                return prob
        return None

    def execute(self, ticker: str, event_title: str):
        logger.info(f"Analyzing {ticker} ({event_title}) for Arbitrage opportunities...")
        try:
            external_prob = self.get_fair_value(event_title)
            if external_prob is None:
                logger.info(f"No external odds found for {event_title}. Skipping Arbitrage.")
                return

            orderbook_data = self.kalshi.get_order_book(ticker)
            ob_dict = orderbook_data.get("orderbook", {}) or orderbook_data.get("orderbook_fp", {})
            yes_bids_raw = ob_dict.get("yes", []) or ob_dict.get("yes_dollars", [])
            no_bids_raw = ob_dict.get("no", []) or ob_dict.get("no_dollars", [])

            if not yes_bids_raw or not no_bids_raw:
                logger.info(f"Empty order book for {ticker}, skipping Arbitrage.")
                return

            best_yes_bid = OrderBook.parse_dollar_or_cents(yes_bids_raw[0][0])
            best_no_bid = OrderBook.parse_dollar_or_cents(no_bids_raw[0][0])
            best_yes_ask = (CONTRACT_PAYOUT - best_no_bid) if best_no_bid > 0 else 0

            ob = OrderBook.from_prices(ticker, best_yes_bid, best_yes_ask)
            opp = ArbitrageEngine.evaluate(ob, external_prob, self.threshold)

            if opp.found:
                logger.info(f"Arbitrage Found! {opp.explanation}")
                if opp.action == "buy_yes":
                    self.kalshi.place_order(ticker, action="buy", side="yes", count=1, price=opp.price)
                elif opp.action == "buy_no":
                    self.kalshi.place_order(ticker, action="buy", side="no", count=1, price=opp.price)

        except Exception as e:
            logger.error(f"Error in Arbitrageur for {ticker}: {e}")
