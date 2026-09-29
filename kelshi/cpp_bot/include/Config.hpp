#pragma once

#include <string>
#include <stdexcept>
#include <cstdlib>

namespace config {

    inline std::string getEnv(const std::string& key, const std::string& default_val = "") {
        const char* val = std::getenv(key.c_str());
        if (val == nullptr) {
            if (default_val.empty()) {
                // In production, we might want to throw if a critical key is missing
                // throw std::runtime_error("Environment variable " + key + " is required.");
                return "";
            }
            return default_val;
        }
        return std::string(val);
    }

    // Load from env variables (like Python's os.getenv)
    // You could also implement a .env parser here, but env vars are faster
    inline std::string KALSHI_API_KEY_ID = getEnv("KALSHI_API_KEY_ID");
    inline std::string KALSHI_PRIVATE_KEY = getEnv("KALSHI_PRIVATE_KEY"); 
    inline std::string TRADE_ENV = getEnv("TRADE_ENV", "paper");

    inline bool isLiveTrading() {
        return TRADE_ENV == "prod" || TRADE_ENV == "PROD";
    }
}
