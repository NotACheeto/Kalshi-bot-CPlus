#include "TradingEngine.hpp"
#include <iostream>
#include <cpr/cpr.h>
#include <nlohmann/json.hpp>
#include "Config.hpp"

using json = nlohmann::json;

long long current_time_seconds() {
    return std::chrono::duration_cast<std::chrono::seconds>(std::chrono::system_clock::now().time_since_epoch()).count();
}

// ==========================
// Market Maker
// ==========================

MarketMaker::MarketMaker(std::shared_ptr<KalshiClient> kalshi, int spread_threshold, int position_limit)
    : kalshi_(kalshi), spread_threshold_(spread_threshold), position_limit_(position_limit) {}

void MarketMaker::execute(const std::string& ticker) {
    std::cout << "Analyzing " << ticker << " for Market Making opportunities...\n";
    try {
        // 1. Cancel resting orders
        // Note: The Kalshi REST API needs GET /portfolio/orders with status=resting
        json resting_res = kalshi_->get("/portfolio/orders?ticker=" + ticker + "&status=resting");
        if (resting_res.contains("orders")) {
            for (const auto& order : resting_res["orders"]) {
                std::cout << "Canceling stale resting order " << order["order_id"].get<std::string>() << " for " << ticker << "\n";
                kalshi_->cancelOrder(order["order_id"].get<std::string>());
            }
        }
        
        // 2. Get orderbook
        json ob_res = kalshi_->get("/markets/" + ticker + "/orderbook");
        if (!ob_res.contains("orderbook")) return;
        
        json ob = ob_res["orderbook"];
        int best_yes_bid = 0;
        int best_no_bid = 0;
        
        if (ob.contains("yes") && !ob["yes"].empty()) {
            best_yes_bid = ob["yes"][0][0].get<int>();
        }
        if (ob.contains("no") && !ob["no"].empty()) {
            best_no_bid = ob["no"][0][0].get<int>();
        }
        
        if (best_yes_bid == 0 || best_no_bid == 0) {
            std::cout << "Only one-sided or empty book for " << ticker << ", skipping.\n";
            return;
        }
        
        int best_yes_ask = 100 - best_no_bid;
        int spread = best_yes_ask - best_yes_bid;
        std::cout << ticker << " - Best YES Bid: " << best_yes_bid << "c, Best YES Ask: " << best_yes_ask << "c (Spread: " << spread << "c)\n";
        
        if (spread >= spread_threshold_) {
            int my_bid = (spread <= 2) ? best_yes_bid : best_yes_bid + 1;
            int my_ask = (spread <= 2) ? best_yes_ask : best_yes_ask - 1;
            
            if (my_bid < my_ask) {
                // Cancel existing open orders to prevent position buildup
                json open_orders = kalshi_->get("/portfolio/orders?ticker=" + ticker + "&status=resting");
                if (open_orders.contains("orders")) {
                    for (const auto& o : open_orders["orders"]) {
                        kalshi_->cancelOrder(o["order_id"].get<std::string>());
                    }
                }
                
                kalshi_->placeOrder(ticker, "buy", "yes", 1, my_bid, "mm_b_" + std::to_string(current_time_seconds()));
                kalshi_->placeOrder(ticker, "sell", "yes", 1, my_ask, "mm_s_" + std::to_string(current_time_seconds()));
            } else {
                std::cout << "Calculated crossed or zero spread, skipping.\n";
            }
        } else {
            std::cout << "Spread (" << spread << "c) is too tight to safely market make.\n";
        }
    } catch (const std::exception& e) {
        std::cerr << "Error in MarketMaker for " << ticker << ": " << e.what() << "\n";
    }
}

// ==========================
// Arbitrageur
// ==========================

Arbitrageur::Arbitrageur(std::shared_ptr<KalshiClient> kalshi) : kalshi_(kalshi) {}

std::map<std::string, double> Arbitrageur::fetch_nfl_odds() {
    long long now = current_time_seconds();
    if (now - last_fetch_ < CACHE_TTL && !odds_cache_.empty()) {
        return odds_cache_;
    }
    
    std::string odds_api_key = config::getEnv("ODDS_API_KEY");
    if (odds_api_key.empty() || odds_api_key == "your_odds_api_key_here") {
        std::cout << "No valid ODDS_API_KEY found.\n";
        return {};
    }
    
    std::cout << "Fetching fresh NFL odds from The Odds API...\n";
    std::string url = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds?apiKey=" + odds_api_key + "&regions=us&markets=h2h&oddsFormat=decimal";
    cpr::Response r = cpr::Get(cpr::Url{url});
    
    if (r.status_code != 200) {
        std::cerr << "Error fetching from Odds API: " << r.status_code << "\n";
        return odds_cache_;
    }
    
    try {
        json data = json::parse(r.text);
        std::map<std::string, double> new_cache;
        
        for (const auto& event : data) {
            if (!event.contains("bookmakers") || event["bookmakers"].empty()) continue;
            
            json target_book = event["bookmakers"][0];
            for (const auto& book : event["bookmakers"]) {
                if (book["key"] == "draftkings") {
                    target_book = book;
                    break;
                }
            }
            
            if (!target_book.contains("markets") || target_book["markets"].empty()) continue;
            json market = target_book["markets"][0];
            
            if (market.contains("outcomes")) {
                for (const auto& outcome : market["outcomes"]) {
                    std::string team_name = outcome["name"].get<std::string>();
                    double decimal_odds = outcome["price"].get<double>();
                    double implied_prob = (1.0 / decimal_odds) * 100.0;
                    new_cache[team_name] = implied_prob;
                }
            }
        }
        odds_cache_ = new_cache;
        last_fetch_ = now;
        std::cout << "Cached odds for " << odds_cache_.size() << " NFL teams.\n";
    } catch (const std::exception& e) {
        std::cerr << "JSON parse error from Odds API: " << e.what() << "\n";
    }
    
    return odds_cache_;
}

double Arbitrageur::get_fair_value(const std::string& event_title) {
    auto odds = fetch_nfl_odds();
    if (odds.empty()) return 0.0;
    
    for (const auto& [team, prob] : odds) {
        // Simple case-insensitive substring match
        std::string team_lower = team;
        std::string title_lower = event_title;
        std::transform(team_lower.begin(), team_lower.end(), team_lower.begin(), ::tolower);
        std::transform(title_lower.begin(), title_lower.end(), title_lower.begin(), ::tolower);
        
        if (title_lower.find(team_lower) != std::string::npos) {
            return prob;
        }
    }
    return 0.0;
}

void Arbitrageur::execute(const std::string& ticker, const std::string& event_title) {
    std::cout << "Analyzing " << ticker << " (" << event_title << ") for Arbitrage opportunities...\n";
    try {
        double external_prob = get_fair_value(event_title);
        if (external_prob == 0.0) {
            std::cout << "No external odds found for " << event_title << ". Skipping Arbitrage.\n";
            return;
        }
        
        json ob_res = kalshi_->get("/markets/" + ticker + "/orderbook");
        if (!ob_res.contains("orderbook")) return;
        json ob = ob_res["orderbook"];
        
        if (!ob.contains("yes") || ob["yes"].empty() || !ob.contains("no") || ob["no"].empty()) {
            std::cout << "Empty order book for " << ticker << ", skipping Arbitrage.\n";
            return;
        }
        
        int best_yes_bid = ob["yes"][0][0].get<int>();
        int best_yes_ask = 100 - ob["no"][0][0].get<int>();
        
        std::cout << ticker << " - Kalshi Ask: " << best_yes_ask << "c, External Fair Value: " << external_prob << "c\n";
        
        if (best_yes_ask < (external_prob - 8)) {
            std::cout << "Arbitrage Found! Kalshi Price (" << best_yes_ask << ") is 8c+ lower than external.\n";
            kalshi_->placeOrder(ticker, "buy", "yes", 1, best_yes_ask, "arb_b_" + std::to_string(current_time_seconds()));
        } else if (best_yes_bid > (external_prob + 8)) {
            std::cout << "Arbitrage Found! Kalshi Price (" << best_yes_bid << ") is 8c+ higher than external.\n";
            kalshi_->placeOrder(ticker, "buy", "no", 1, 100 - best_yes_bid, "arb_s_" + std::to_string(current_time_seconds()));
        }
    } catch (const std::exception& e) {
        std::cerr << "Error in Arbitrageur for " << ticker << ": " << e.what() << "\n";
    }
}
