"""
Kalshi Market Making Core Engine (Python Port)

Provides institutional-grade components for:
- OrderBook parsing, validation, and crossed-book detection
- Volatility Circuit Breaker (sliding window flash-crash protection)
- Inventory Risk Manager (inventory skewing, one-sided quoting, position limits)
- Collateral & Solvency Manager (margin requirements, payout modeling)
- Arbitrage & Mispricing Engine (external fair value edge detection)
"""

from typing import Dict, List, Optional, Tuple, Deque
from collections import defaultdict, deque
import math
import time

MIN_TICK_PRICE = 1     # 1 cent ($0.01)
MAX_TICK_PRICE = 99    # 99 cents ($0.99)
CONTRACT_PAYOUT = 100  # 100 cents ($1.00)


class OrderBookLevel:
    def __init__(self, price: int, quantity: int):
        self.price = price
        self.quantity = quantity


class OrderBook:
    def __init__(self, ticker: str = "", best_yes_bid: int = 0, best_yes_ask: int = 0):
        self.ticker = ticker
        self.best_yes_bid = best_yes_bid
        self.best_yes_ask = best_yes_ask
        self.yes_bids: List[OrderBookLevel] = []
        self.no_bids: List[OrderBookLevel] = []

    def is_valid(self) -> bool:
        return (
            self.best_yes_bid > 0
            and self.best_yes_ask > 0
            and self.best_yes_bid < CONTRACT_PAYOUT
            and self.best_yes_ask < CONTRACT_PAYOUT
        )

    def is_crossed(self) -> bool:
        return self.best_yes_bid >= self.best_yes_ask

    def spread(self) -> int:
        return self.best_yes_ask - self.best_yes_bid

    def mid_price(self) -> float:
        return (self.best_yes_bid + self.best_yes_ask) / 2.0

    @staticmethod
    def from_prices(ticker: str, yes_bid: int, yes_ask: int) -> 'OrderBook':
        return OrderBook(ticker=ticker, best_yes_bid=yes_bid, best_yes_ask=yes_ask)

    @staticmethod
    def from_bid_no_bid(ticker: str, yes_bid: int, no_bid: int) -> 'OrderBook':
        yes_ask = (CONTRACT_PAYOUT - no_bid) if (0 < no_bid < CONTRACT_PAYOUT) else 0
        return OrderBook(ticker=ticker, best_yes_bid=yes_bid, best_yes_ask=yes_ask)

    @staticmethod
    def parse_dollar_or_cents(dollar_val, default_val: int = 0) -> int:
        if dollar_val is None or dollar_val == "":
            return default_val
        try:
            val = float(dollar_val)
            if 0.0 < val <= 1.0:
                return int(round(val * 100.0))
            return int(round(val))
        except (ValueError, TypeError):
            return default_val


class VolatilityCircuitBreaker:
    def __init__(self, max_volatility_cents: int = 3, window_sec: int = 5, halt_duration_sec: int = 15):
        self.max_volatility_cents = max_volatility_cents
        self.window_sec = window_sec
        self.halt_duration_sec = halt_duration_sec
        self.price_histories: Dict[str, Deque[Tuple[float, float]]] = defaultdict(deque)
        self.trading_halts: Dict[str, float] = {}

    def on_price_update(self, ticker: str, mid_price: float, timestamp_sec: float) -> bool:
        # Check if already halted
        if ticker in self.trading_halts and timestamp_sec < self.trading_halts[ticker]:
            return True

        history = self.price_histories[ticker]

        # Ignore out-of-order timestamps
        if history and timestamp_sec < history[-1][0]:
            return self.is_halted(ticker, timestamp_sec)

        history.append((timestamp_sec, mid_price))

        # Prune old data outside window
        while history and (timestamp_sec - history[0][0]) > self.window_sec:
            history.popleft()

        if len(history) >= 2:
            prices = [entry[1] for entry in history]
            min_p = min(prices)
            max_p = max(prices)
            price_change = max_p - min_p

            if price_change >= self.max_volatility_cents:
                self.trading_halts[ticker] = timestamp_sec + self.halt_duration_sec
                return True

        return False

    def is_halted(self, ticker: str, current_timestamp_sec: float) -> bool:
        if ticker not in self.trading_halts:
            return False
        return current_timestamp_sec < self.trading_halts[ticker]

    def get_halt_expiry(self, ticker: str) -> float:
        return self.trading_halts.get(ticker, 0.0)

    def reset_halt(self, ticker: str):
        self.trading_halts.pop(ticker, None)
        self.price_histories.pop(ticker, None)

    def clear(self):
        self.trading_halts.clear()
        self.price_histories.clear()


class Quote:
    def __init__(self, ticker: str = ""):
        self.ticker = ticker
        self.bid_price: int = 0
        self.ask_price: int = 0
        self.quantity: int = 1
        self.quote_bid: bool = True
        self.quote_ask: bool = True
        self.valid: bool = False
        self.reason: str = ""


class InventoryRiskManager:
    def __init__(self, max_position: int = 2, min_spread_to_quote: int = 2,
                 min_bid: int = MIN_TICK_PRICE, max_ask: int = MAX_TICK_PRICE):
        self.max_position = max_position
        self.min_spread_to_quote = min_spread_to_quote
        self.min_bid = min_bid
        self.max_ask = max_ask
        self.positions: Dict[str, int] = defaultdict(int)

    def get_position(self, ticker: str) -> int:
        return self.positions.get(ticker, 0)

    def set_position(self, ticker: str, pos: int):
        self.positions[ticker] = pos

    def apply_fill(self, ticker: str, side: str, count: int = 1):
        if side in ("bid", "buy_yes", "buy"):
            self.positions[ticker] += count
        elif side in ("ask", "sell_yes", "sell"):
            self.positions[ticker] -= count

    def calculate_skew(self, ticker: str) -> int:
        pos = self.get_position(ticker)
        if pos >= self.max_position:
            return 2
        if pos <= -self.max_position:
            return -2
        if pos > 0:
            return 1
        if pos < 0:
            return -1
        return 0

    def compute_quote(self, ob: OrderBook) -> Quote:
        q = Quote(ticker=ob.ticker)
        q.quantity = 1
        q.valid = False
        q.quote_bid = True
        q.quote_ask = True

        if not ob.is_valid():
            q.reason = "Invalid or empty orderbook"
            return q

        if ob.is_crossed():
            q.reason = "Crossed orderbook (bid >= ask)"
            return q

        spread = ob.spread()
        if spread < self.min_spread_to_quote:
            q.reason = f"Spread too tight ({spread}c < {self.min_spread_to_quote}c)"
            return q

        current_pos = self.get_position(ob.ticker)
        skew = self.calculate_skew(ob.ticker)

        # One-sided quoting risk mitigation:
        # Long limit reached -> stop quoting bids, only quote asks to offload.
        if current_pos >= self.max_position:
            q.quote_bid = False
        # Short limit reached -> stop quoting asks, only quote bids to cover.
        if current_pos <= -self.max_position:
            q.quote_ask = False

        if spread == 2:
            my_bid = ob.best_yes_bid - skew
            my_ask = ob.best_yes_ask - skew
        else:
            my_bid = (ob.best_yes_bid + 1) - skew
            my_ask = (ob.best_yes_ask - 1) - skew

        # Clamping to exchange valid boundaries
        my_bid = max(self.min_bid, min(MAX_TICK_PRICE - 1, my_bid))
        my_ask = max(self.min_bid + 1, min(self.max_ask, my_ask))

        if q.quote_bid and q.quote_ask and my_bid >= my_ask:
            q.reason = "Calculated crossed or inverted quote"
            return q

        q.bid_price = my_bid
        q.ask_price = my_ask
        q.valid = True
        q.reason = "OK"
        return q


class CollateralManager:
    def __init__(self, initial_capital_cents: int):
        self.available_capital_cents = initial_capital_cents
        self.total_starting_capital = initial_capital_cents
        self.realized_spent = 0
        self.realized_pnl = 0

    def available_capital(self) -> int:
        return self.available_capital_cents

    @staticmethod
    def calculate_collateral(bid_price: int, ask_price: int, count: int = 1,
                             has_bid: bool = True, has_ask: bool = True) -> int:
        coll = 0
        if has_bid:
            coll += (bid_price * count)
        if has_ask:
            coll += ((CONTRACT_PAYOUT - ask_price) * count)
        return coll

    @classmethod
    def calculate_quote_collateral(cls, q: Quote) -> int:
        return cls.calculate_collateral(q.bid_price, q.ask_price, q.quantity, q.quote_bid, q.quote_ask)

    def can_afford_quote(self, q: Quote) -> bool:
        needed = self.calculate_quote_collateral(q)
        return self.available_capital_cents >= needed

    def reserve_for_quote(self, q: Quote) -> bool:
        needed = self.calculate_quote_collateral(q)
        if self.available_capital_cents < needed:
            return False
        self.available_capital_cents -= needed
        return True

    def refund_bid(self, bid_price: int, count: int = 1):
        self.available_capital_cents += (bid_price * count)

    def refund_ask(self, ask_price: int, count: int = 1):
        self.available_capital_cents += ((CONTRACT_PAYOUT - ask_price) * count)

    def on_bid_fill(self, bid_price: int, count: int = 1):
        self.realized_spent += (bid_price * count)

    def on_ask_fill(self, ask_price: int, count: int = 1):
        self.available_capital_cents += (CONTRACT_PAYOUT * count)
        self.realized_pnl += ((CONTRACT_PAYOUT - (100 - ask_price)) * count)

    def reset(self, capital_cents: int):
        self.available_capital_cents = capital_cents
        self.total_starting_capital = capital_cents
        self.realized_spent = 0
        self.realized_pnl = 0


class ArbitrageOpportunity:
    def __init__(self, found: bool = False, action: str = "", price: int = 0, edge: float = 0.0, explanation: str = ""):
        self.found = found
        self.action = action
        self.price = price
        self.edge = edge
        self.explanation = explanation


class ArbitrageEngine:
    DEFAULT_ARB_THRESHOLD = 8.0

    @classmethod
    def evaluate(cls, ob: OrderBook, external_fair_prob: float, threshold: float = DEFAULT_ARB_THRESHOLD) -> ArbitrageOpportunity:
        opp = ArbitrageOpportunity()
        if not ob.is_valid() or ob.is_crossed() or external_fair_prob <= 0.0 or external_fair_prob >= 100.0:
            return opp

        if ob.best_yes_ask < (external_fair_prob - threshold):
            opp.found = True
            opp.action = "buy_yes"
            opp.price = ob.best_yes_ask
            opp.edge = external_fair_prob - ob.best_yes_ask
            opp.explanation = (
                f"YES is underpriced. Kalshi Ask ({ob.best_yes_ask}c) is < "
                f"External Fair ({external_fair_prob}c) - {threshold}c"
            )
            return opp

        if ob.best_yes_bid > (external_fair_prob + threshold):
            opp.found = True
            opp.action = "buy_no"
            opp.price = CONTRACT_PAYOUT - ob.best_yes_bid
            opp.edge = ob.best_yes_bid - external_fair_prob
            opp.explanation = (
                f"YES is overpriced / NO is underpriced. Kalshi Bid ({ob.best_yes_bid}c) is > "
                f"External Fair ({external_fair_prob}c) + {threshold}c"
            )
            return opp

        return opp
