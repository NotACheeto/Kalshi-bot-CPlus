#pragma once

#include <string>
#include <map>
#include <vector>
#include <nlohmann/json.hpp>
#include <memory>

class KalshiClient {
public:
    KalshiClient(const std::string& key_id, const std::string& private_key);
    ~KalshiClient() = default;

    // HTTP Methods
    nlohmann::json get(const std::string& path);
    nlohmann::json post(const std::string& path, const nlohmann::json& payload);
    nlohmann::json del(const std::string& path);

    // Signature Generation
    std::string sign(const std::string& method, const std::string& path, long long timestamp);

    // Helpers
    nlohmann::json getPositions();
    nlohmann::json getBalance();
    nlohmann::json placeOrder(const std::string& ticker, const std::string& action, const std::string& side, int count, int price, const std::string& client_order_id);
    nlohmann::json cancelOrder(const std::string& order_id);

private:
    std::string key_id_;
    std::string private_key_;
    std::string base_url_ = "https://api.elections.kalshi.com";
    
    // Internal struct to hold the loaded RSA/ECDSA key to avoid reloading per request
    struct EVP_PKEY_Deleter;
    std::shared_ptr<void> pkey_; 

    void loadPrivateKey();
    nlohmann::json executeRequest(const std::string& method, const std::string& path, const nlohmann::json& payload = nullptr);
};
