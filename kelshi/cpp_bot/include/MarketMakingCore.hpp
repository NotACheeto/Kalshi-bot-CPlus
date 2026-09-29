#pragma once

#include <string>
#include <vector>
#include <deque>
#include <map>
#include <unordered_map>
#include <cmath>
#include <algorithm>
#include <chrono>
#include <optional>
#include <stdexcept>
#include <sstream>

namespace kalshi_mm {

    // Constants
    constexpr int MIN_TICK_PRICE = 1;     // 1 cent ($0.01)
    constexpr int MAX_TICK_PRICE = 99;    // 99 cents ($0.99)
    constexpr int CONTRACT_PAYOUT = 100;  // 100 cents ($1.00)

    struct OrderBookLevel {
        int price; // cents (1-99)
        int quantity;
    };

    struct OrderBook {
        std::string ticker;
        int best_yes_bid = 0;
        int best_yes_ask = 0;
        std::vector<OrderBookLevel> yes_bids;
        std::vector<OrderBookLevel> no_bids;

        bool is_valid() const {
            return best_yes_bid > 0 && best_yes_ask > 0 && best_yes_bid < CONTRACT_PAYOUT && best_yes_ask < CONTRACT_PAYOUT;
        }

        bool is_crossed() const {
            return best_yes_bid >= best_yes_ask;
        }

        int spread() const {
            return best_yes_ask - best_yes_bid;
        }

        double mid_price() const {
            return (best_yes_bid + best_yes_ask) / 2.0;
        }

        static OrderBook from_prices(const std::string& t, int yes_bid, int yes_ask) {
            OrderBook ob;
            ob.ticker = t;
            ob.best_yes_bid = yes_bid;
            ob.best_yes_ask = yes_ask;
            return ob;
        }

        // Convert NO bid to YES ask: YES_Ask = 100 - NO_Bid
        static OrderBook from_bid_no_bid(const std::string& t, int yes_bid, int no_bid) {
            OrderBook ob;
            ob.ticker = t;
            ob.best_yes_bid = yes_bid;
            ob.best_yes_ask = (no_bid > 0 && no_bid < CONTRACT_PAYOUT) ? (CONTRACT_PAYOUT - no_bid) : 0;
            return ob;
        }

        // Parse dollar string or integer
        static int parse_dollar_or_cents(const std::string& dollar_str, int default_val = 0) {
            if (dollar_str.empty()) return default_val;
            try {
                double val = std::stod(dollar_str);
                // If value is <= 1.0, it's dollars ($0.45 -> 45c)
                if (val <= 1.0 && val > 0.0) {
                    return static_cast<int>(std::round(val * 100.0));
                }
                return static_cast<int>(std::round(val));
            } catch (...) {
                return default_val;
            }
        }
    };

    struct VolatilityConfig {
        int max_volatility_cents = 3;
        int window_sec = 5;
        int halt_duration_sec = 15;
    };

    class VolatilityCircuitBreaker {
    public:
        explicit VolatilityCircuitBreaker(VolatilityConfig cfg = {})
            : config_(cfg) {}

        // Returns true if circuit breaker triggered (halt initiated)
        bool on_price_update(const std::string& ticker, double mid_price, long long timestamp_sec) {
            // Check if already halted
            auto halt_it = trading_halts_.find(ticker);
            if (halt_it != trading_halts_.end() && timestamp_sec < halt_it->second) {
                return true; // Still in halt
            }

            auto& history = price_histories_[ticker];
            
            // Handle out-of-order timestamps or clock skew gracefully
            if (!history.empty() && timestamp_sec < history.back().first) {
                // Ignore backwards tick or reset if severely skewed
                return is_halted(ticker, timestamp_sec);
            }

            history.push_back({timestamp_sec, mid_price});

            // Prune old data outside sliding window
            while (!history.empty() && (timestamp_sec - history.front().first) > config_.window_sec) {
                history.pop_front();
            }

            if (history.size() >= 2) {
                // Find min and max in window to catch intraday whipsaws and one-directional spikes
                double min_p = history.front().second;
                double max_p = history.front().second;
                for (const auto& entry : history) {
                    min_p = std::min(min_p, entry.second);
                    max_p = std::max(max_p, entry.second);
                }

                double price_change = max_p - min_p;
                if (price_change >= config_.max_volatility_cents) {
                    trading_halts_[ticker] = timestamp_sec + config_.halt_duration_sec;
                    return true;
                }
            }

            return false;
        }

        bool is_halted(const std::string& ticker, long long current_timestamp_sec) const {
            auto it = trading_halts_.find(ticker);
            if (it == trading_halts_.end()) return false;
            return current_timestamp_sec < it->second;
        }

        long long get_halt_expiry(const std::string& ticker) const {
            auto it = trading_halts_.find(ticker);
            if (it == trading_halts_.end()) return 0;
            return it->second;
        }

        void reset_halt(const std::string& ticker) {
            trading_halts_.erase(ticker);
            price_histories_.erase(ticker);
        }

        void clear() {
            trading_halts_.clear();
            price_histories_.clear();
        }

    private:
        VolatilityConfig config_;
        std::unordered_map<std::string, std::deque<std::pair<long long, double>>> price_histories_;
        std::unordered_map<std::string, long long> trading_halts_;
    };

    struct Quote {
        std::string ticker;
        int bid_price; // YES bid
        int ask_price; // YES ask
        int quantity;
        bool valid;
        std::string reason;
    };

    struct RiskConfig {
        int max_position = 2;
        int min_spread_to_quote = 2;
        int min_bid = MIN_TICK_PRICE;
        int max_ask = MAX_TICK_PRICE;
    };

    class InventoryRiskManager {
    public:
        explicit InventoryRiskManager(RiskConfig config = {})
            : config_(config) {}

        int get_position(const std::string& ticker) const {
            auto it = positions_.find(ticker);
            return (it != positions_.end()) ? it->second : 0;
        }

        void set_position(const std::string& ticker, int pos) {
            positions_[ticker] = pos;
        }

        void apply_fill(const std::string& ticker, const std::string& side, int count = 1) {
            if (side == "bid" || side == "buy_yes") {
                positions_[ticker] += count;
            } else if (side == "ask" || side == "sell_yes") {
                positions_[ticker] -= count;
            }
        }

        int calculate_skew(const std::string& ticker) const {
            int pos = get_position(ticker);
            if (pos >= config_.max_position) return 2;
            if (pos <= -config_.max_position) return -2;
            if (pos > 0) return 1;
            if (pos < 0) return -1;
            return 0;
        }

        Quote compute_quote(const OrderBook& ob) const {
            Quote q;
            q.ticker = ob.ticker;
            q.quantity = 1;
            q.valid = false;

            if (!ob.is_valid()) {
                q.reason = "Invalid or empty orderbook";
                return q;
            }

            if (ob.is_crossed()) {
                q.reason = "Crossed orderbook (bid >= ask)";
                return q;
            }

            int spread = ob.spread();
            if (spread < config_.min_spread_to_quote) {
                q.reason = "Spread too tight (" + std::to_string(spread) + "c < " + std::to_string(config_.min_spread_to_quote) + "c)";
                return q;
            }

            int skew = calculate_skew(ob.ticker);
            int my_bid = 0;
            int my_ask = 0;

            if (spread == 2) {
                my_bid = ob.best_yes_bid - skew;
                my_ask = ob.best_yes_ask - skew;
            } else {
                my_bid = (ob.best_yes_bid + 1) - skew;
                my_ask = (ob.best_yes_ask - 1) - skew;
            }

            // Clamping to exchange valid boundaries
            my_bid = std::clamp(my_bid, config_.min_bid, MAX_TICK_PRICE - 1);
            my_ask = std::clamp(my_ask, config_.min_bid + 1, config_.max_ask);

            if (my_bid >= my_ask) {
                q.reason = "Calculated crossed or inverted quote";
                return q;
            }

            q.bid_price = my_bid;
            q.ask_price = my_ask;
            q.valid = true;
            q.reason = "OK";
            return q;
        }

    private:
        RiskConfig config_;
        std::unordered_map<std::string, int> positions_;
    };

    class CollateralManager {
    public:
        explicit CollateralManager(long long initial_capital_cents)
            : available_capital_cents_(initial_capital_cents), total_starting_capital_(initial_capital_cents) {}

        long long available_capital() const {
            return available_capital_cents_;
        }

        // Collateral needed for a 2-sided quote: buying YES at bid_price costs bid_price;
        // selling YES at ask_price (or buying NO) requires (100 - ask_price) collateral
        static int calculate_collateral(int bid_price, int ask_price, int count = 1) {
            return (bid_price + (CONTRACT_PAYOUT - ask_price)) * count;
        }

        bool can_afford_quote(int bid_price, int ask_price, int count = 1) const {
            int needed = calculate_collateral(bid_price, ask_price, count);
            return available_capital_cents_ >= needed;
        }

        bool reserve_for_quote(int bid_price, int ask_price, int count = 1) {
            int needed = calculate_collateral(bid_price, ask_price, count);
            if (available_capital_cents_ < needed) {
                return false;
            }
            available_capital_cents_ -= needed;
            return true;
        }

        void refund_bid(int bid_price, int count = 1) {
            available_capital_cents_ += (bid_price * count);
        }

        void refund_ask(int ask_price, int count = 1) {
            available_capital_cents_ += ((CONTRACT_PAYOUT - ask_price) * count);
        }

        void on_bid_fill(int bid_price, int count = 1) {
            // Bid collateral was already deducted upon placement
            // Balance in cents is updated with asset acquired
            realized_spent_ += (bid_price * count);
        }

        void on_ask_fill(int ask_price, int count = 1) {
            // Ask collateral was already deducted ((100 - ask_price))
            // On fill, we receive ask_price and release collateral, net total payout = 100 cents
            available_capital_cents_ += (CONTRACT_PAYOUT * count);
            realized_pnl_ += ((CONTRACT_PAYOUT - (100 - ask_price)) * count); // or ask_price
        }

        void reset(long long capital_cents) {
            available_capital_cents_ = capital_cents;
            total_starting_capital_ = capital_cents;
            realized_spent_ = 0;
            realized_pnl_ = 0;
        }

    private:
        long long available_capital_cents_;
        long long total_starting_capital_;
        long long realized_spent_ = 0;
        long long realized_pnl_ = 0;
    };

    struct ArbitrageOpportunity {
        bool found = false;
        std::string action; // "buy_yes" or "buy_no"
        int price = 0;
        double edge = 0.0;
        std::string explanation;
    };

    class ArbitrageEngine {
    public:
        static constexpr double DEFAULT_ARB_THRESHOLD = 8.0; // 8 cents edge required

        static ArbitrageOpportunity evaluate(const OrderBook& ob, double external_fair_prob, double threshold = DEFAULT_ARB_THRESHOLD) {
            ArbitrageOpportunity opp;
            if (!ob.is_valid() || ob.is_crossed() || external_fair_prob <= 0.0 || external_fair_prob >= 100.0) {
                return opp;
            }

            // Kalshi Ask is lower than external probability by threshold -> Buy undervalued YES
            if (ob.best_yes_ask < (external_fair_prob - threshold)) {
                opp.found = true;
                opp.action = "buy_yes";
                opp.price = ob.best_yes_ask;
                opp.edge = external_fair_prob - ob.best_yes_ask;
                opp.explanation = "YES is underpriced. Kalshi Ask (" + std::to_string(ob.best_yes_ask) +
                                  "c) is < External Fair (" + std::to_string(external_fair_prob) + "c) - " +
                                  std::to_string(threshold) + "c";
                return opp;
            }

            // Kalshi Bid is higher than external probability by threshold -> Buy undervalued NO (or sell YES)
            if (ob.best_yes_bid > (external_fair_prob + threshold)) {
                opp.found = true;
                opp.action = "buy_no";
                opp.price = CONTRACT_PAYOUT - ob.best_yes_bid;
                opp.edge = ob.best_yes_bid - external_fair_prob;
                opp.explanation = "YES is overpriced / NO is underpriced. Kalshi Bid (" + std::to_string(ob.best_yes_bid) +
                                  "c) is > External Fair (" + std::to_string(external_fair_prob) + "c) + " +
                                  std::to_string(threshold) + "c";
                return opp;
            }

            return opp;
        }
    };

} // namespace kalshi_mm
