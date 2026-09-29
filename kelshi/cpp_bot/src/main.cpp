#include <iostream>
#include <string>
#include <unordered_map>
#include <vector>
#include <deque>
#include <chrono>
#include <mutex>
#include <thread>
#include <condition_variable>
#include <atomic>
#include <asio/ssl.hpp>
#include <websocketpp/config/asio_client.hpp>
#include <websocketpp/client.hpp>
#include <nlohmann/json.hpp>
#include "KalshiClient.hpp"
#include "Config.hpp"
#include "MarketMakingCore.hpp"

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
long long available_capital_cents = 300; // default fallback ($3)
double paper_balance = 10000.0;
std::mutex state_mutex;
std::atomic<bool> is_running{true};

// Risk Parameters
const int MAX_POSITION_PER_MARKET = 2;
const int MAX_VOLATILITY_CENTS = 3;
const int VOLATILITY_WINDOW_SEC = 5;
const int HALT_DURATION_SEC = 15;

std::unique_ptr<KalshiClient> kalshi;

// Asynchronous Order Dispatch Queue for non-blocking execution
enum class OrderActionType { PLACE_ORDER, CANCEL_ORDER };

struct OrderTask {
    OrderActionType type;
    std::string ticker;
    std::string action; // "buy" / "sell"
    std::string side;   // "yes" / "no"
    int count;
    int price;
    std::string client_order_id;
    std::string order_id_to_cancel;
};

std::deque<OrderTask> order_queue;
std::mutex queue_mutex;
std::condition_variable queue_cv;

long long current_time_sec() {
    return std::chrono::duration_cast<std::chrono::seconds>(std::chrono::system_clock::now().time_since_epoch()).count();
}

long long current_time_ms() {
    return std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::system_clock::now().time_since_epoch()).count();
}

void enqueue_order_task(const OrderTask& task) {
    {
        std::lock_guard<std::mutex> lock(queue_mutex);
        order_queue.push_back(task);
    }
    queue_cv.notify_one();
}

void cancel_all_orders_async(const std::string& ticker) {
    auto& orders = live_orders[ticker];
    if (orders.empty()) return;
    
    // Refund local capital
    for (const auto& o : orders) {
        if (o["side"] == "bid") {
            available_capital_cents += o["price"].get<int>();
        } else {
            available_capital_cents += (100 - o["price"].get<int>());
        }
        
        // Enqueue cancel task asynchronously
        OrderTask task;
        task.type = OrderActionType::CANCEL_ORDER;
        task.ticker = ticker;
        task.order_id_to_cancel = o["id"].get<std::string>();
        enqueue_order_task(task);
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
        double min_p = history.front().second;
        double max_p = history.front().second;
        for (const auto& p : history) {
            min_p = std::min(min_p, p.second);
            max_p = std::max(max_p, p.second);
        }
        if ((max_p - min_p) >= MAX_VOLATILITY_CENTS) {
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
    
    // 1. PAPER MATCHING ENGINE
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
    
    // 2. CIRCUIT BREAKER CHECK
    if (check_volatility(ticker, mid_price, now)) {
        trading_halts[ticker] = now + HALT_DURATION_SEC;
        cancel_all_orders_async(ticker);
        return;
    }
    
    if (now < trading_halts[ticker]) return;
    
    // 3. INVENTORY SKEWING & RISK CONTROLS
    int current_pos = inventory[ticker];
    int skew = 0;
    if (current_pos >= MAX_POSITION_PER_MARKET) skew = 2;
    else if (current_pos <= -MAX_POSITION_PER_MARKET) skew = -2;
    else if (current_pos > 0) skew = 1;
    else if (current_pos < 0) skew = -1;
    
    // One-sided quoting flags:
    // If long limit reached, only quote ASKS to offload. Do NOT buy more.
    // If short limit reached, only quote BIDS to cover. Do NOT sell more.
    bool allow_bid = (current_pos < MAX_POSITION_PER_MARKET);
    bool allow_ask = (current_pos > -MAX_POSITION_PER_MARKET);
    
    if (spread >= 2) {
        int my_bid = (spread == 2) ? best_yes_bid - skew : (best_yes_bid + 1) - skew;
        int my_ask = (spread == 2) ? best_yes_ask - skew : (best_yes_ask - 1) - skew;
        
        my_bid = std::max(1, std::min(98, my_bid));
        my_ask = std::max(2, std::min(99, my_ask));
        
        if (my_bid < my_ask || (!allow_bid || !allow_ask)) {
            int total_collateral_needed = 0;
            if (allow_bid) total_collateral_needed += my_bid;
            if (allow_ask) total_collateral_needed += (100 - my_ask);
            
            int capital_freed = 0;
            for (const auto& o : live_orders[ticker]) {
                if (o["side"] == "bid") capital_freed += o["price"].get<int>();
                else capital_freed += (100 - o["price"].get<int>());
            }
            
            if ((available_capital_cents + capital_freed) < total_collateral_needed) return;
            
            cancel_all_orders_async(ticker);
            available_capital_cents -= total_collateral_needed;
            
            long long cur_ms = current_time_ms();
            
            if (allow_bid) {
                std::string bid_id = "b_" + std::to_string(cur_ms);
                live_orders[ticker].push_back({{"id", bid_id}, {"side", "bid"}, {"price", my_bid}, {"timestamp", cur_ms}});
                
                OrderTask task;
                task.type = OrderActionType::PLACE_ORDER;
                task.ticker = ticker;
                task.action = "buy";
                task.side = "yes";
                task.count = 1;
                task.price = my_bid;
                task.client_order_id = bid_id;
                enqueue_order_task(task);
            }
            
            if (allow_ask) {
                std::string ask_id = "s_" + std::to_string(cur_ms);
                live_orders[ticker].push_back({{"id", ask_id}, {"side", "ask"}, {"price", my_ask}, {"timestamp", cur_ms}});
                
                OrderTask task;
                task.type = OrderActionType::PLACE_ORDER;
                task.ticker = ticker;
                task.action = "sell";
                task.side = "yes";
                task.count = 1;
                task.price = my_ask;
                task.client_order_id = ask_id;
                enqueue_order_task(task);
            }
            
            std::cout << "[" << ticker << "] QUOTE: " 
                      << (allow_bid ? ("Bid " + std::to_string(my_bid) + "c") : "Bid [OFF]") << " | "
                      << (allow_ask ? ("Ask " + std::to_string(my_ask) + "c") : "Ask [OFF]") 
                      << " (Pos: " << current_pos << ")\n";
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
                int bid = 0;
                int ask = 0;
                
                if (payload.contains("yes_bid")) bid = payload["yes_bid"].get<int>();
                else if (payload.contains("yes_bid_dollars")) bid = static_cast<int>(std::stod(payload["yes_bid_dollars"].get<std::string>()) * 100);
                
                if (payload.contains("yes_ask")) ask = payload["yes_ask"].get<int>();
                else if (payload.contains("yes_ask_dollars")) ask = static_cast<int>(std::stod(payload["yes_ask_dollars"].get<std::string>()) * 100);
                
                if (bid > 0 && ask > 0) {
                    handle_market_update(ticker, bid, ask);
                }
            }
        }
    } catch (const std::exception& e) {
        std::cerr << "Message Parse Error: " << e.what() << "\n";
    }
}

// Background Worker Thread for non-blocking HTTP order placement & cancellations
void order_execution_worker() {
    while (is_running) {
        OrderTask task;
        {
            std::unique_lock<std::mutex> lock(queue_mutex);
            queue_cv.wait(lock, [] { return !order_queue.empty() || !is_running; });
            if (!is_running && order_queue.empty()) break;
            task = order_queue.front();
            order_queue.pop_front();
        }
        
        try {
            if (task.type == OrderActionType::PLACE_ORDER) {
                kalshi->placeOrder(task.ticker, task.action, task.side, task.count, task.price, task.client_order_id);
            } else if (task.type == OrderActionType::CANCEL_ORDER) {
                kalshi->cancelOrder(task.order_id_to_cancel);
            }
        } catch (const std::exception& e) {
            std::cerr << "Order worker error: " << e.what() << "\n";
        }
    }
}

int main() {
    try {
        std::cout << "Starting C++ Kalshi HFT Bot with Async Non-Blocking Execution Engine...\n";
        
        if (config::KALSHI_API_KEY_ID.empty() || config::KALSHI_PRIVATE_KEY.empty()) {
            std::cerr << "Error: KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY must be set in environment.\n";
            return 1;
        }
        
        kalshi = std::make_unique<KalshiClient>(config::KALSHI_API_KEY_ID, config::KALSHI_PRIVATE_KEY);
        
        // Initialize balance dynamically if possible
        if (config::isLiveTrading()) {
            try {
                json bal = kalshi->getBalance();
                if (bal.contains("balance")) {
                    available_capital_cents = bal["balance"].get<long long>();
                    std::cout << "Loaded live account balance: $" << (available_capital_cents / 100.0) << "\n";
                }
            } catch (...) {
                std::cout << "Could not fetch remote balance, defaulting to config.\n";
            }
        }
        
        // Start background order execution worker
        std::thread worker(order_execution_worker);
        
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
            std::cout << "WebSocket connection closed! Flushing resting orders...\n";
            std::lock_guard<std::mutex> lock(state_mutex);
            for (auto& [ticker, orders] : live_orders) {
                for (const auto& o : orders) {
                    kalshi->cancelOrder(o["id"].get<std::string>());
                }
                orders.clear();
            }
        });
        
        std::string uri = "wss://api.elections.kalshi.com/trade-api/ws/v2";
        websocketpp::lib::error_code ec;
        client::connection_ptr con = c.get_connection(uri, ec);
        if (ec) {
            std::cout << "Connect init error: " << ec.message() << "\n";
            is_running = false;
            queue_cv.notify_all();
            if (worker.joinable()) worker.join();
            return 1;
        }
        
        long long timestamp = current_time_ms();
        std::string sig = kalshi->sign("GET", "/trade-api/ws/v2", timestamp);
        
        con->append_header("KALSHI-ACCESS-KEY", config::KALSHI_API_KEY_ID);
        con->append_header("KALSHI-ACCESS-TIMESTAMP", std::to_string(timestamp));
        con->append_header("KALSHI-ACCESS-SIGNATURE", sig);
        
        c.connect(con);
        
        // Subscription on connection
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
            while(is_running) {
                std::this_thread::sleep_for(std::chrono::seconds(4));
                std::lock_guard<std::mutex> lock(state_mutex);
                long long now = current_time_ms();
                std::vector<std::string> to_cancel;
                
                for (const auto& [ticker, orders] : live_orders) {
                    if (!orders.empty() && (now - orders[0]["timestamp"].get<long long>()) > 3500) {
                        to_cancel.push_back(ticker);
                    }
                }
                
                for (const auto& t : to_cancel) {
                    cancel_all_orders_async(t);
                }
            }
        });
        sweeper.detach();
        
        c.run();
        
        // Cleanup on exit
        is_running = false;
        queue_cv.notify_all();
        if (worker.joinable()) worker.join();
        
    } catch (const std::exception& e) {
        std::cerr << "FATAL ERROR: " << e.what() << "\n";
        return 1;
    }
    return 0;
}
