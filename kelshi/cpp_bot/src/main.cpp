#include <iostream>
#include <string>
#include <unordered_map>
#include <vector>
#include <chrono>
#include <mutex>
#include <thread>
#include <asio/ssl.hpp>
#include <websocketpp/config/asio_client.hpp>
#include <websocketpp/client.hpp>
#include <nlohmann/json.hpp>
#include "KalshiClient.hpp"
#include "Config.hpp"

using json = nlohmann::json;
using client = websocketpp::client<websocketpp::config::asio_tls_client>;
using websocketpp::lib::placeholders::_1;
using websocketpp::lib::placeholders::_2;
using websocketpp::lib::bind;

// Shared Application State
std::unordered_map<std::string, int> inventory;
std::unordered_map<std::string, std::vector<std::pair<long long, double>>> price_history;
std::unordered_map<std::string, long long> trading_halts;
std::unordered_map<std::string, std::vector<json>> live_orders;
long long available_capital_cents = 300; // $3 account start
double paper_balance = 10000.0;
std::mutex state_mutex;

// Risk Parameters
const int MAX_POSITION_PER_MARKET = 2;
const int MAX_VOLATILITY_CENTS = 3;
const int VOLATILITY_WINDOW_SEC = 5;
const int HALT_DURATION_SEC = 15;

std::unique_ptr<KalshiClient> kalshi;

long long current_time_sec() {
    return std::chrono::duration_cast<std::chrono::seconds>(std::chrono::system_clock::now().time_since_epoch()).count();
}

long long current_time_ms() {
    return std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::system_clock::now().time_since_epoch()).count();
}

void cancel_all_orders(const std::string& ticker) {
    auto& orders = live_orders[ticker];
    if (orders.empty()) return;
    
    // Process local capital refund
    for (const auto& o : orders) {
        if (o["side"] == "bid") {
            available_capital_cents += o["price"].get<int>();
        } else {
            available_capital_cents += (100 - o["price"].get<int>());
        }
    }
    
    // Issue cancellation API calls
    for (const auto& o : orders) {
        kalshi->cancelOrder(o["id"].get<std::string>());
    }
    
    orders.clear();
}

bool check_volatility(const std::string& ticker, double mid_price, long long now) {
    auto& history = price_history[ticker];
    history.push_back({now, mid_price});
    
    // Prune old data
    while (!history.empty() && now - history.front().first > VOLATILITY_WINDOW_SEC) {
        history.erase(history.begin());
    }
    
    if (history.size() >= 2) {
        double price_change = std::abs(history.back().second - history.front().second);
        if (price_change >= MAX_VOLATILITY_CENTS) {
            std::cout << "🚨 VOLATILITY SPIKE ON " << ticker << "! Halting for " << HALT_DURATION_SEC << "s.\n";
            return true;
        }
    }
    return false;
}

void handle_market_update(const std::string& ticker, int best_yes_bid, int best_yes_ask) {
    std::lock_guard<std::mutex> lock(state_mutex);
    long long now = current_time_sec();
    
    double mid_price = (best_yes_bid + best_yes_ask) / 2.0;
    int spread = best_yes_ask - best_yes_bid;
    
    // PAPER MATCHING ENGINE
    if (!config::isLiveTrading() && !live_orders[ticker].empty()) {
        std::vector<json> unfilled;
        for (const auto& order : live_orders[ticker]) {
            bool filled = false;
            std::string side = order["side"].get<std::string>();
            int price = order["price"].get<int>();
            
            if (side == "bid" && best_yes_ask <= price) {
                filled = true;
                paper_balance -= price / 100.0;
                inventory[ticker]++;
            } else if (side == "ask" && best_yes_bid >= price) {
                filled = true;
                paper_balance += price / 100.0;
                inventory[ticker]--;
                available_capital_cents += 100;
            }
            
            if (filled) {
                std::cout << "$$$ FILL ALERT: " << side << " " << ticker << " at " << price << "c! Inventory: " << inventory[ticker] << "\n";
            } else {
                unfilled.push_back(order);
            }
        }
        live_orders[ticker] = unfilled;
    }
    
    // CIRCUIT BREAKER
    if (check_volatility(ticker, mid_price, now)) {
        trading_halts[ticker] = now + HALT_DURATION_SEC;
        cancel_all_orders(ticker);
        return;
    }
    
    if (now < trading_halts[ticker]) return;
    
    // INVENTORY SKEWING
    int current_pos = inventory[ticker];
    int skew = 0;
    if (current_pos >= MAX_POSITION_PER_MARKET) skew = 2;
    else if (current_pos <= -MAX_POSITION_PER_MARKET) skew = -2;
    else if (current_pos > 0) skew = 1;
    else if (current_pos < 0) skew = -1;
    
    if (spread >= 2) {
        int my_bid = (spread == 2) ? best_yes_bid - skew : (best_yes_bid + 1) - skew;
        int my_ask = (spread == 2) ? best_yes_ask - skew : (best_yes_ask - 1) - skew;
        
        my_bid = std::max(1, std::min(98, my_bid));
        my_ask = std::max(2, std::min(99, my_ask));
        
        if (my_bid < my_ask) {
            int total_collateral_needed = my_bid + (100 - my_ask);
            
            int capital_freed = 0;
            for (const auto& o : live_orders[ticker]) {
                if (o["side"] == "bid") capital_freed += o["price"].get<int>();
                else capital_freed += (100 - o["price"].get<int>());
            }
            
            if ((available_capital_cents + capital_freed) < total_collateral_needed) return;
            
            cancel_all_orders(ticker);
            available_capital_cents -= total_collateral_needed;
            
            std::string bid_id = "b_" + std::to_string(current_time_ms());
            std::string ask_id = "s_" + std::to_string(current_time_ms());
            
            // Execute HTTP Calls (In a real HFT system, these should be dispatched asynchronously to a thread pool)
            json bid_res = kalshi->placeOrder(ticker, "buy", "yes", 1, my_bid, bid_id);
            json ask_res = kalshi->placeOrder(ticker, "sell", "yes", 1, my_ask, ask_id);
            
            if (bid_res.contains("order")) {
                live_orders[ticker].push_back({{"id", bid_res["order"]["order_id"]}, {"side", "bid"}, {"price", my_bid}, {"timestamp", current_time_ms()}});
            }
            if (ask_res.contains("order")) {
                live_orders[ticker].push_back({{"id", ask_res["order"]["order_id"]}, {"side", "ask"}, {"price", my_ask}, {"timestamp", current_time_ms()}});
            }
            
            std::cout << "[" << ticker << "] QUOTED: Bid " << my_bid << "c | Ask " << my_ask << "c\n";
        }
    }
}

void on_message(client* c, websocketpp::connection_hdl hdl, client::message_ptr msg) {
    try {
        json data = json::parse(msg->get_payload());
        if (data.value("type", "") == "ticker") {
            json payload = data["msg"];
            std::string ticker = payload.value("market_ticker", "");
            
            if (!ticker.empty() && ticker.find("CROSSCATEGORY") == std::string::npos) {
                // Parsing dollars to cents logic as per original Python code
                int bid = 0;
                int ask = 0;
                
                if (payload.contains("yes_bid")) bid = payload["yes_bid"].get<int>();
                else if (payload.contains("yes_bid_dollars")) bid = static_cast<int>(std::stod(payload["yes_bid_dollars"].get<std::string>()) * 100);
                
                if (payload.contains("yes_ask")) ask = payload["yes_ask"].get<int>();
                else if (payload.contains("yes_ask_dollars")) ask = static_cast<int>(std::stod(payload["yes_ask_dollars"].get<std::string>()) * 100);
                
                if (bid > 0 && ask > 0) {
                    // Start worker thread or handle synchronously for now
                    handle_market_update(ticker, bid, ask);
                }
            }
        }
    } catch (const std::exception& e) {
        std::cerr << "Message Parse Error: " << e.what() << "\n";
    }
}

int main() {
    try {
        std::cout << "Starting C++ Kalshi HFT Bot...\n";
        
        if (config::KALSHI_API_KEY_ID.empty() || config::KALSHI_PRIVATE_KEY.empty()) {
            std::cerr << "Error: KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY must be set in environment.\n";
            return 1;
        }
        
        kalshi = std::make_unique<KalshiClient>(config::KALSHI_API_KEY_ID, config::KALSHI_PRIVATE_KEY);
        
        // WebSocket Setup
        client c;
        c.set_access_channels(websocketpp::log::alevel::all);
        c.set_error_channels(websocketpp::log::elevel::all);
        c.init_asio();
        
        c.set_tls_init_handler([](websocketpp::connection_hdl) {
            return websocketpp::lib::make_shared<asio::ssl::context>(asio::ssl::context::tlsv12_client);
        });
        
        c.set_message_handler(bind(&on_message, &c, ::_1, ::_2));
        
        c.set_fail_handler([](websocketpp::connection_hdl) {
            std::cout << "WebSocket connection failed!\n";
        });
        
        c.set_close_handler([](websocketpp::connection_hdl) {
            std::cout << "WebSocket connection closed!\n";
        });
        
        std::string uri = "wss://api.elections.kalshi.com/trade-api/ws/v2";
        websocketpp::lib::error_code ec;
        client::connection_ptr con = c.get_connection(uri, ec);
        if (ec) {
            std::cout << "Connect init error: " << ec.message() << "\n";
            return 1;
        }
        
        long long timestamp = current_time_ms();
        std::string sig = kalshi->sign("GET", "/trade-api/ws/v2", timestamp);
        
        con->append_header("KALSHI-ACCESS-KEY", config::KALSHI_API_KEY_ID);
        con->append_header("KALSHI-ACCESS-TIMESTAMP", std::to_string(timestamp));
        con->append_header("KALSHI-ACCESS-SIGNATURE", sig);
        
        c.connect(con);
        
        // When connected, send subscription message
        con->set_open_handler([&c](websocketpp::connection_hdl hdl) {
            std::cout << "WebSocket connected. Subscribing to ticker channel...\n";
            json sub = {
                {"id", 1},
                {"cmd", "subscribe"},
                {"params", {{"channels", {"ticker"}}}}
            };
            c.send(hdl, sub.dump(), websocketpp::frame::opcode::text);
        });
        
        // Stale Order Sweeper Thread
        std::thread sweeper([](){
            while(true) {
                std::this_thread::sleep_for(std::chrono::seconds(5));
                std::lock_guard<std::mutex> lock(state_mutex);
                long long now = current_time_ms();
                std::vector<std::string> to_cancel;
                
                for (const auto& [ticker, orders] : live_orders) {
                    if (!orders.empty() && (now - orders[0]["timestamp"].get<long long>()) > 4000) {
                        to_cancel.push_back(ticker);
                    }
                }
                
                for (const auto& t : to_cancel) {
                    cancel_all_orders(t);
                }
            }
        });
        sweeper.detach();
        
        c.run();
    } catch (const std::exception& e) {
        std::cerr << "FATAL ERROR: " << e.what() << "\n";
        return 1;
    }
    return 0;
}
