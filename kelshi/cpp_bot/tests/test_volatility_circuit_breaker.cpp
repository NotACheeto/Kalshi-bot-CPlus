#include "test_framework.hpp"
#include "../include/MarketMakingCore.hpp"

using namespace kalshi_mm;

// ==========================================
// VOLATILITY & CIRCUIT BREAKER TESTS
// ==========================================

TEST(VolatilityCircuitBreaker, NormalPriceFluctuationsDoNotHalt) {
    VolatilityConfig cfg;
    cfg.max_volatility_cents = 3;
    cfg.window_sec = 5;
    cfg.halt_duration_sec = 15;

    VolatilityCircuitBreaker cb(cfg);

    long long t = 1000;
    ASSERT_FALSE(cb.on_price_update("KXBTC-1", 50.0, t));
    ASSERT_FALSE(cb.on_price_update("KXBTC-1", 51.0, t + 1));
    ASSERT_FALSE(cb.on_price_update("KXBTC-1", 52.0, t + 2)); // 2c move within window <= 3c
    ASSERT_FALSE(cb.is_halted("KXBTC-1", t + 2));
}

TEST(VolatilityCircuitBreaker, FlashCrashTriggersInstantHalt) {
    VolatilityConfig cfg{3, 5, 15};
    VolatilityCircuitBreaker cb(cfg);

    long long t = 1000;
    cb.on_price_update("KXCRASH", 60.0, t);
    
    // Sudden drop of 10c within 1 second
    bool halted = cb.on_price_update("KXCRASH", 50.0, t + 1);
    ASSERT_TRUE(halted);
    ASSERT_TRUE(cb.is_halted("KXCRASH", t + 1));
    ASSERT_EQ(cb.get_halt_expiry("KXCRASH"), t + 1 + 15);
}

TEST(VolatilityCircuitBreaker, UpwardSpikeTriggersHalt) {
    VolatilityConfig cfg{3, 5, 15};
    VolatilityCircuitBreaker cb(cfg);

    long long t = 2000;
    cb.on_price_update("KXPUMP", 30.0, t);
    
    // Sudden surge by 4c (>= 3c threshold)
    bool halted = cb.on_price_update("KXPUMP", 34.0, t + 2);
    ASSERT_TRUE(halted);
    ASSERT_TRUE(cb.is_halted("KXPUMP", t + 2));
}

TEST(VolatilityCircuitBreaker, SlidingWindowPruningExpiresOldSpikes) {
    VolatilityConfig cfg{3, 5, 15};
    VolatilityCircuitBreaker cb(cfg);

    long long t = 3000;
    cb.on_price_update("KXSLIDE", 50.0, t);
    
    // Price stays flat
    cb.on_price_update("KXSLIDE", 50.0, t + 2);
    
    // Move after window has passed (> 5s from t)
    // At t + 6s, the point at t=3000 is pruned.
    // Price moves from 50.0 at t=3002 to 52.0 at t=3006 (2c change in remaining window)
    bool halted = cb.on_price_update("KXSLIDE", 52.0, t + 6);
    ASSERT_FALSE(halted);
    ASSERT_FALSE(cb.is_halted("KXSLIDE", t + 6));
}

TEST(VolatilityCircuitBreaker, HaltAutoExpiryAfterDuration) {
    VolatilityConfig cfg{3, 5, 15};
    VolatilityCircuitBreaker cb(cfg);

    long long t = 4000;
    cb.on_price_update("KXHALT", 50.0, t);
    cb.on_price_update("KXHALT", 45.0, t + 1); // 5c drop triggers halt until t + 16

    ASSERT_TRUE(cb.is_halted("KXHALT", t + 1));
    ASSERT_TRUE(cb.is_halted("KXHALT", t + 15)); // Still within 15s halt
    
    // After 15s elapsed
    ASSERT_FALSE(cb.is_halted("KXHALT", t + 16));
    ASSERT_FALSE(cb.is_halted("KXHALT", t + 20));
}

TEST(VolatilityCircuitBreaker, OutOfOrderTimestampsHandledSafely) {
    VolatilityConfig cfg{3, 5, 15};
    VolatilityCircuitBreaker cb(cfg);

    long long t = 5000;
    cb.on_price_update("KXTIMETRAVEL", 50.0, t);
    cb.on_price_update("KXTIMETRAVEL", 51.0, t + 2);
    
    // Packet arrives late with timestamp t + 1 (backwards in time)
    // Engine must not crash or corrupt deque order
    ASSERT_NO_THROW(cb.on_price_update("KXTIMETRAVEL", 99.0, t + 1));
    ASSERT_FALSE(cb.is_halted("KXTIMETRAVEL", t + 2));
}

TEST(VolatilityCircuitBreaker, MultiMarketIsolation) {
    VolatilityConfig cfg{3, 5, 15};
    VolatilityCircuitBreaker cb(cfg);

    long long t = 6000;
    cb.on_price_update("MARKET_A", 50.0, t);
    cb.on_price_update("MARKET_B", 50.0, t);

    // Halt MARKET_A
    cb.on_price_update("MARKET_A", 40.0, t + 1);
    ASSERT_TRUE(cb.is_halted("MARKET_A", t + 1));

    // MARKET_B must remain unhalted and tradable
    ASSERT_FALSE(cb.is_halted("MARKET_B", t + 1));
    ASSERT_FALSE(cb.on_price_update("MARKET_B", 51.0, t + 2));
}
