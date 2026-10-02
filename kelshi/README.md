# `kelshi/` — Core Application Architecture & AI Context Guide

> **AI Context Notice**: This document serves as a self-contained specification and reference manual for all modules in `kelshi/`. Consult this file to understand functionality, constraints, interfaces, and limitations without reading the full source files.

---

## 📑 File Directory & Module Map

| File | Type | Primary Role | Key Classes / Entrypoints |
| :--- | :--- | :--- | :--- |
| [`market_making_core.py`](file:///c:/Users/arjun/OneDrive/Desktop/Projects/Kalshi-bot-CPlus/kelshi/market_making_core.py) | Library | Core math, orderbook, risk models, circuit breaker, collateral | `OrderBook`, `VolatilityCircuitBreaker`, `InventoryRiskManager`, `CollateralManager`, `ArbitrageEngine` |
| [`fast_main_remote.py`](file:///c:/Users/arjun/OneDrive/Desktop/Projects/Kalshi-bot-CPlus/kelshi/fast_main_remote.py) | Executable | Async WebSocket HFT Market Maker (Production / Remote) | `main()`, `handle_market_update()`, `cancel_order_async()`, `sync_inventory_loop()` |
| [`fast_main.py`](file:///c:/Users/arjun/OneDrive/Desktop/Projects/Kalshi-bot-CPlus/kelshi/fast_main.py) | Executable | Async WebSocket HFT Market Maker (Local / Dev) | `main()`, `handle_market_update()` |
| [`kalshi_api.py`](file:///c:/Users/arjun/OneDrive/Desktop/Projects/Kalshi-bot-CPlus/kelshi/kalshi_api.py) | Library | Kalshi API v2 REST Client & Auth Signer | `KalshiClient` (`_sign`, `place_order`, `cancel_order`, `get_order_book`) |
| [`strategies.py`](file:///c:/Users/arjun/OneDrive/Desktop/Projects/Kalshi-bot-CPlus/kelshi/strategies.py) | Library | Strategy orchestrators for REST-based execution | `MarketMaker`, `Arbitrageur` |
| [`main.py`](file:///c:/Users/arjun/OneDrive/Desktop/Projects/Kalshi-bot-CPlus/kelshi/main.py) | Executable | Polling loop execution for REST strategies | `main()` |
| [`config.py`](file:///c:/Users/arjun/OneDrive/Desktop/Projects/Kalshi-bot-CPlus/kelshi/config.py) | Utility | Configuration loader & single-instance socket locks | `KALSHI_API_KEY_ID`, `KALSHI_PRIVATE_KEY`, `enforce_single_instance()` |
| [`test_market_making_core.py`](file:///c:/Users/arjun/OneDrive/Desktop/Projects/Kalshi-bot-CPlus/kelshi/test_market_making_core.py) | Test Suite | 37-case unit, black-swan & solvency test suite | `TestOrderBookEdgeCases`, `TestVolatilityCircuitBreaker`, `TestInventoryRiskManager`, `TestCollateralManager`, `TestArbitrageEngine`, `TestBlackSwanScenarios` |

---

## 🔍 Detailed File Breakdown

### 1. `market_making_core.py`
**Purpose**: Pure Python domain engine for pricing, risk boundaries, and market-making calculations with zero third-party dependencies.

#### Key Components:
- **`OrderBook`**:
  - `best_yes_bid`: Highest YES buy price in cents ($1–99$).
  - `best_yes_ask`: Lowest YES sell price in cents ($1–99$). Derived as `100 - best_no_bid` if NO bids are present.
  - `is_valid()`: Ensures non-zero bids/asks strictly $< 100¢$.
  - `is_crossed()`: Returns `True` if `best_yes_bid >= best_yes_ask` (disallows trading on inverted feeds).
  - `parse_dollar_or_cents(val)`: Converts `"0.45"` or `45` to integer cents `45`.
- **`VolatilityCircuitBreaker`**:
  - `max_volatility_cents`: Default `3` cents.
  - `window_sec`: Default `5` seconds.
  - `halt_duration_sec`: Default `15` seconds.
  - `on_price_update(ticker, mid_price, timestamp)`: Pushes to a per-ticker timestamped deque, prunes older entries, and triggers a 15-second halt if `max(price) - min(price) >= 3¢`.
  - Safely ignores out-of-order timestamps and isolates markets independently.
- **`InventoryRiskManager`**:
  - `max_position`: Default `2` contracts per market.
  - `calculate_skew(ticker)`: $+2$ if $\ge +2$, $-2$ if $\le -2$, $+1$ if $> 0$, $-1$ if $< 0$.
  - `compute_quote(ob)`: Applies inventory skew to bid/ask. Clamps bids to $[1, 98]$ and asks to $[2, 99]$.
  - **One-Sided Quoting**:
    - If `pos >= +MAX_POSITION` $\rightarrow$ sets `quote.quote_bid = False` (stops buying; only offloads).
    - If `pos <= -MAX_POSITION` $\rightarrow$ sets `quote.quote_ask = False` (stops selling; only covers).
- **`CollateralManager`**:
  - Formulas: Buy YES costs `bid_price * count` cents. Sell YES costs `(100 - ask_price) * count` cents.
  - `reserve_for_quote(quote)`: Deducts collateral locally; returns `False` if bankroll is insufficient.
  - `refund_bid(price)` / `refund_ask(price)`: Restores exact collateral upon order cancellation.
  - `on_bid_fill(price)` / `on_ask_fill(price)`: Realizes payout ($100¢$ on ask settlement) and tracks PnL.
- **`ArbitrageEngine`**:
  - `evaluate(ob, external_fair_prob, threshold=8.0)`: Finds mispricings where Kalshi Ask is underpriced vs external probability by $\ge 8¢$, or Kalshi Bid is overpriced by $\ge 8¢$.

#### Constraints & Invariants:
- All prices are strictly integer cents ($1$ to $99$).
- Negative available capital is strictly forbidden.

---

### 2. `fast_main_remote.py` / `fast_main.py`
**Purpose**: High-frequency async WebSocket engines for live or simulated market making.

#### Operational Flow:
1. **Authentication**: Computes Kalshi RSA/Ed25519 signature and connects to `wss://api.elections.kalshi.com/trade-api/ws/v2`.
2. **Subscription**: Subscribes to `"ticker"` feed across target contract series (e.g. `KXBTC`, `KXETH`, `KXSP500`, `KXNFL`, `KXWTI`, `KXGOLD`).
3. **Socket Optimization**: Sets `TCP_NODELAY` on `aiohttp.TCPConnector` sockets to eliminate Nagle packet batching delay.
4. **Queue Priority Smart Reconciliation**:
   - Compares active resting order prices for `ticker` against target `quote.bid_price` and `quote.ask_price`.
   - **If price is unchanged $\rightarrow$ DO NOT CANCEL**. Order maintains its FIFO time-priority in Kalshi's matching engine.
   - If price changed or side disabled $\rightarrow$ Cancels and submits new post-only limit order (`/portfolio/orders`).
5. **Circuit Breaker Integration**: On volatility spike, cancels all quotes for that ticker and halts for 15s.
6. **Live Inventory Sync**: Runs periodic background coroutine (`sync_inventory_loop`) to fetch `/portfolio/positions` and `/portfolio/balance`.
7. **Single-Instance Enforcement**: Binds a local TCP port (`18333`) on startup to prevent duplicate competing processes.

#### Constraints & Limitations:
- Single-instance lock on port `18333`.
- Requires `orjson` for fast JSON serialization/deserialization.
- Graceful shutdown handles `SIGINT` (`Ctrl+C`) and flushes all resting orders via `asyncio.gather`.

---

### 3. `kalshi_api.py`
**Purpose**: Synchronous HTTP/REST API wrapper for Kalshi v2 API with cryptographic signing and paper-trading simulation.

#### Key Methods:
- `_sign(method, path, timestamp)`: Computes SHA256-PSS signature for RSA keys or standard Ed25519 signature. Produces Base64 string for `KALSHI-ACCESS-SIGNATURE`.
- `place_order(ticker, action, count, price, side="yes")`:
  - Target endpoint: `POST /trade-api/v2/portfolio/orders`.
  - Format: `{"ticker": ..., "action": "buy"/"sell", "side": "yes"/"no", "count": 1, "yes_price": 45, "type": "limit", "post_only": True}`.
- `cancel_order(order_id)`: Target endpoint: `DELETE /trade-api/v2/portfolio/orders/{order_id}`.
- `get_order_book(ticker)`: Target endpoint: `GET /trade-api/v2/markets/{ticker}/orderbook`.
- `simulate_fills(ticker, best_yes_bid, best_yes_ask)`: Local paper execution hook for testing fills without live capital.

#### Constraints & Limitations:
- Supports both RSA (`.pem`) and Ed25519 keys.
- When `TRADE_ENV="paper_prod"`, orders are logged locally to `paper_trades.log` without placing live exchange orders.

---

### 4. `strategies.py` & `main.py`
**Purpose**: REST polling market-maker and sports arbitrage engine (useful for lower-frequency trading or fallback).

#### Modules:
- **`MarketMaker`** (`strategies.py`):
  - Integrates `market_making_core.py` for risk evaluation.
  - Implements `_reconcile_orders` to preserve FIFO queue priority across polling iterations.
- **`Arbitrageur`** (`strategies.py`):
  - Polls The Odds API (`https://api.the-odds-api.com/v4/sports/...`) for implied probabilities from sportsbooks (e.g. DraftKings).
  - Caches odds for 5 minutes (`CACHE_TTL = 300s`).
  - Executes arb orders if Kalshi price differs from external fair value by $\ge 8¢$.
- **`main.py`**:
  - Polling loop runner using single-instance port `18334`.

---

### 5. `test_market_making_core.py`
**Purpose**: 37 comprehensive unit, stress, boundary, and black-swan test cases.

#### Test Coverage:
1. `TestOrderBookEdgeCases` (7 tests): Normal books, crossed books, single-sided books, boundary 1c/99c ticks, dollar parsing, NO bid to YES ask conversion.
2. `TestVolatilityCircuitBreaker` (7 tests): Flash crash halt, upward spike halt, sliding window pruning, 15s auto-expiry, out-of-order timestamps, multi-market isolation.
3. `TestInventoryRiskManager` (8 tests): Neutral quoting, long skew, short skew, 2c spread quoting, price clamping, fill tracking, one-sided quoting at $\pm 2$ pos.
4. `TestCollateralManager` (5 tests): Collateral formula, reservation, cancellation refund, fill PnL cycle, non-negative solvency guarantee.
5. `TestArbitrageEngine` (4 tests): Buy YES arb, Buy NO arb, sub-threshold edge rejection, crossed book safety.
6. `TestBlackSwanScenarios` (6 tests): Flash crash protection, void book liquidity evaporation, crossed feed attack, toxic taker sweep, micro-bankroll ($0.50) stress, 10,000 packet stress benchmark (~37k–48k updates/sec).

---

## 🔒 System Invariants & Safety Rules

1. **Never Quote Across Own Spread**: `my_bid < my_ask` is strictly validated.
2. **Never Cancel Unchanged Orders**: Preserves Kalshi's Price-Time FIFO order priority.
3. **Never Exceed Collateral**: Collateral must be reserved locally before submitting orders.
4. **Strict Post-Only**: All quotes are submitted with `"post_only": True` to avoid paying taker fees.
5. **Always Halt on Volatility**: If price moves $\ge 3¢$ in $5$s, all quotes on that market are immediately canceled and halted for $15$s.
