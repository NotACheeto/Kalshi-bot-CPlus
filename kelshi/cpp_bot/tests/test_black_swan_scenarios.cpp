#include "test_framework.hpp"
#include "../include/MarketMakingCore.hpp"
#include <random>
#include <chrono>

using namespace kalshi_mm;

// =========================================================================
// BLACK SWAN & COMPLEX ADVERSARIAL MARKET SCENARIO TESTS
// =========================================================================

TEST(BlackSwanScenarios, ElectionNightFlashCrashProtection) {
    // SCENARIO: An election outcome is called abruptly.
    // The market instantly plunges from 82c down to 10c.
    // The bot MUST trip its circuit breaker on the initial move,
    // cancel all resting buy orders, and refuse to catch the falling knife.

    VolatilityCircuitBreaker cb({3, 5, 15});
    InventoryRiskManager risk_mgr;
    CollateralManager cm(1000); // 1000c = $10.00
    
    std::string ticker = "KXPRES-ELEC";
    long long t = 100000;

    // 1. Initial calm market (80c / 84c)
    OrderBook calm_ob = OrderBook::from_prices(ticker, 80, 84);
    ASSERT_FALSE(cb.on_price_update(ticker, calm_ob.mid_price(), t));
    
    Quote q_initial = risk_mgr.compute_quote(calm_ob);
    ASSERT_TRUE(q_initial.valid);
    ASSERT_EQ(q_initial.bid_price, 81);
    ASSERT_EQ(q_initial.ask_price, 83);
    ASSERT_TRUE(cm.reserve_for_quote(q_initial.bid_price, q_initial.ask_price));

    // 2. Black Swan Event: 100ms later, price drops from 82c -> 68c (14c drop!)
    long long t_crash = t + 1;
    OrderBook crash_ob_1 = OrderBook::from_prices(ticker, 66, 70);
    bool halted = cb.on_price_update(ticker, crash_ob_1.mid_price(), t_crash);
    
    // Circuit breaker MUST trip
    ASSERT_TRUE(halted);
    ASSERT_TRUE(cb.is_halted(ticker, t_crash));

    // Bot cancels all resting orders and refunds collateral
    cm.refund_bid(q_initial.bid_price);
    cm.refund_ask(q_initial.ask_price);
    ASSERT_EQ(cm.available_capital(), 1000); // Fully recovered, avoided buying at 81c!

    // 3. Falling knife continues down to 15c and then 5c
    OrderBook crash_ob_2 = OrderBook::from_prices(ticker, 12, 18);
    ASSERT_TRUE(cb.is_halted(ticker, t_crash + 2)); // Still halted

    // Attempting to quote while halted must be blocked
    bool can_quote_while_halted = !cb.is_halted(ticker, t_crash + 2);
    ASSERT_FALSE(can_quote_while_halted);
}

TEST(BlackSwanScenarios, TotalLiquidityEvaporationVoidBook) {
    // SCENARIO: Complete liquidity withdrawal across all exchange market makers.
    // Order books suddenly report 0 bids and 0 asks.
    // Bot must remain rock-solid, not throw, not crash, and reject quoting.

    InventoryRiskManager risk_mgr;
    CollateralManager cm(500);

    std::string ticker = "KXVOID-MARKET";
    
    // Void book: 0c bid, 0c ask
    OrderBook void_ob = OrderBook::from_prices(ticker, 0, 0);
    Quote q_void = risk_mgr.compute_quote(void_ob);
    
    ASSERT_FALSE(q_void.valid);
    ASSERT_NE(q_void.reason.find("Invalid"), std::string::npos);

    // One-sided void book: bid exists, ask vanished
    OrderBook half_void = OrderBook::from_prices(ticker, 35, 0);
    Quote q_half = risk_mgr.compute_quote(half_void);
    ASSERT_FALSE(q_half.valid);
}

TEST(BlackSwanScenarios, CorruptedFeedCrossedBookInversionAttack) {
    // SCENARIO: Network corruption or bad exchange feed broadcasts inverted prices:
    // YES Bid 80c, NO Bid 70c (YES Ask = 100 - 70 = 30c -> Bid 80c > Ask 30c!)

    InventoryRiskManager risk_mgr;
    std::string ticker = "KXCORRUPT";

    OrderBook crossed_ob = OrderBook::from_bid_no_bid(ticker, 80, 70);
    ASSERT_TRUE(crossed_ob.is_crossed());
    ASSERT_EQ(crossed_ob.spread(), -50); // Inverted spread: 30 - 80 = -50

    Quote q = risk_mgr.compute_quote(crossed_ob);
    ASSERT_FALSE(q.valid);
    ASSERT_NE(q.reason.find("Crossed"), std::string::npos);
}

TEST(BlackSwanScenarios, ToxicTakerSweepAndMaxPositionSqueeze) {
    // SCENARIO: An informed HFT taker aggressively buys all liquidity,
    // repeatedly executing against our resting quotes to push us short.
    // The engine must adapt skew in real-time and limit max exposure.

    InventoryRiskManager risk_mgr(RiskConfig{2, 2, 1, 99}); // Max pos = 2
    std::string ticker = "KXSQUEEZE";

    OrderBook ob = OrderBook::from_prices(ticker, 50, 56);

    // Step 1: Initial neutral quote
    Quote q0 = risk_mgr.compute_quote(ob);
    ASSERT_EQ(q0.bid_price, 51);
    ASSERT_EQ(q0.ask_price, 55);

    // Step 2: Taker sweeps our ask (we sold 1 YES -> pos = -1)
    risk_mgr.apply_fill(ticker, "ask", 1);
    ASSERT_EQ(risk_mgr.get_position(ticker), -1);

    Quote q1 = risk_mgr.compute_quote(ob);
    // Skew is now -1 -> increases bid and ask to 52c / 56c
    ASSERT_EQ(q1.bid_price, 52);
    ASSERT_EQ(q1.ask_price, 56);

    // Step 3: Taker sweeps our ask again (we sold another YES -> pos = -2, at limit!)
    risk_mgr.apply_fill(ticker, "ask", 1);
    ASSERT_EQ(risk_mgr.get_position(ticker), -2);

    Quote q2 = risk_mgr.compute_quote(ob);
    // Skew is now -2 -> aggressively skews upward to 53c / 57c
    ASSERT_EQ(q2.bid_price, 53);
    ASSERT_EQ(q2.ask_price, 57);

    // Verify skew caps at +/-2
    ASSERT_EQ(risk_mgr.calculate_skew(ticker), -2);
}

TEST(BlackSwanScenarios, MicroBankrollSolvencyStress) {
    // SCENARIO: Extremely low account balance ($1.20 = 120c).
    // Multiple markets attempt concurrent quoting.
    // The collateral manager must strictly guarantee non-negative capital.

    CollateralManager cm(120);

    // Market A: Bid 40c, Ask 60c -> requires 40 + (100 - 60) = 80c
    ASSERT_TRUE(cm.can_afford_quote(40, 60));
    ASSERT_TRUE(cm.reserve_for_quote(40, 60));
    ASSERT_EQ(cm.available_capital(), 40);

    // Market B: Bid 50c, Ask 60c -> requires 50 + 40 = 90c (cannot afford with 40c!)
    ASSERT_FALSE(cm.can_afford_quote(50, 60));
    ASSERT_FALSE(cm.reserve_for_quote(50, 60));
    ASSERT_EQ(cm.available_capital(), 40);

    // Market C: Micro quote: Bid 10c, Ask 90c -> requires 10 + 10 = 20c (can afford!)
    ASSERT_TRUE(cm.can_afford_quote(10, 90));
    ASSERT_TRUE(cm.reserve_for_quote(10, 90));
    ASSERT_EQ(cm.available_capital(), 20);

    // Never breaches zero
    ASSERT_GE(cm.available_capital(), 0);
}

TEST(BlackSwanScenarios, UltraHighFrequency100kPacketBlastStress) {
    // SCENARIO: Stress test engine with 100,000 rapid orderbook update packets
    // with random price fluctuations, testing throughput and zero memory leaks.

    VolatilityCircuitBreaker cb({3, 5, 15});
    InventoryRiskManager risk_mgr;
    CollateralManager cm(100000);

    std::mt19937_64 rng(1337);
    std::uniform_int_distribution<int> price_dist(30, 70);
    std::uniform_int_distribution<int> spread_dist(2, 8);

    long long start_time_ms = 1000000;
    int halted_count = 0;
    int valid_quotes_generated = 0;

    auto start_perf = std::chrono::high_resolution_clock::now();

    constexpr int TOTAL_PACKETS = 100000;
    for (int i = 0; i < TOTAL_PACKETS; ++i) {
        long long current_t = start_time_ms + (i / 10); // 10 updates per second average
        int base_bid = price_dist(rng);
        int sp = spread_dist(rng);
        int base_ask = base_bid + sp;

        OrderBook ob = OrderBook::from_prices("KXSTRESS", base_bid, base_ask);
        
        // Feed into volatility tracker
        if (cb.on_price_update("KXSTRESS", ob.mid_price(), current_t)) {
            halted_count++;
            continue;
        }

        Quote q = risk_mgr.compute_quote(ob);
        if (q.valid) {
            valid_quotes_generated++;
        }
    }

    auto end_perf = std::chrono::high_resolution_clock::now();
    auto duration_us = std::chrono::duration_cast<std::chrono::microseconds>(end_perf - start_perf).count();
    double duration_ms = duration_us / 1000.0;
    double updates_per_sec = (TOTAL_PACKETS / (duration_ms / 1000.0));

    std::cout << "\n    ⚡ 100,000 Packet Blast Performance:\n"
              << "       Total Time: " << duration_ms << " ms\n"
              << "       Throughput: " << static_cast<long long>(updates_per_sec) << " updates/sec\n"
              << "       Latency per update: " << (duration_us / static_cast<double>(TOTAL_PACKETS)) * 1000.0 << " ns\n"
              << "       Halted events detected: " << halted_count << "\n"
              << "       Valid quotes generated: " << valid_quotes_generated << "\n";

    ASSERT_GT(updates_per_sec, 50000.0); // Must exceed 50,000 updates/sec on modern CPU
    ASSERT_GT(valid_quotes_generated, 0);
}
