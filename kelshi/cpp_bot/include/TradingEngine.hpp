#pragma once

#include "KalshiClient.hpp"
#include <string>
#include <memory>
#include <map>
#include <chrono>

class MarketMaker {
public:
    MarketMaker(std::shared_ptr<KalshiClient> kalshi, int spread_threshold = 4, int position_limit = 50);
    
    // Pure market making execution
    void execute(const std::string& ticker);

private:
    std::shared_ptr<KalshiClient> kalshi_;
    int spread_threshold_;
    int position_limit_;
};

class Arbitrageur {
public:
    Arbitrageur(std::shared_ptr<KalshiClient> kalshi);
    
    void execute(const std::string& ticker, const std::string& event_title);

private:
    std::shared_ptr<KalshiClient> kalshi_;
    std::map<std::string, double> odds_cache_;
    long long last_fetch_ = 0;
    const int CACHE_TTL = 300; // 5 minutes
    
    std::map<std::string, double> fetch_nfl_odds();
    double get_fair_value(const std::string& event_title);
};
