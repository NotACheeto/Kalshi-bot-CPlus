#include "test_framework.hpp"
#include "../include/MarketMakingCore.hpp"

using namespace kalshi_mm;

// ==========================================
// ARBITRAGE & MISPRICING TESTS
// ==========================================

TEST(ArbitrageEngine, BuyYesArbitrageOpportunity) {
    // External fair value: 65% (65c)
    // Kalshi Ask: 55c (edge = 65 - 55 = 10c >= 8c threshold)
    OrderBook ob = OrderBook::from_prices("KXNFL-CHIEFS", 50, 55);
    
    ArbitrageOpportunity opp = ArbitrageEngine::evaluate(ob, 65.0, 8.0);
    ASSERT_TRUE(opp.found);
    ASSERT_EQ(opp.action, "buy_yes");
    ASSERT_EQ(opp.price, 55);
    ASSERT_NEAR(opp.edge, 10.0, 0.001);
}

TEST(ArbitrageEngine, BuyNoArbitrageOpportunity) {
    // External fair value: 40% (40c)
    // Kalshi Bid: 52c (YES is overpriced by 12c >= 8c threshold)
    // Bot should buy NO (sell YES) at (100 - 52) = 48c
    OrderBook ob = OrderBook::from_prices("KXNFL-EAGLES", 52, 58);

    ArbitrageOpportunity opp = ArbitrageEngine::evaluate(ob, 40.0, 8.0);
    ASSERT_TRUE(opp.found);
    ASSERT_EQ(opp.action, "buy_no");
    ASSERT_EQ(opp.price, 48); // 100 - 52
    ASSERT_NEAR(opp.edge, 12.0, 0.001);
}

TEST(ArbitrageEngine, InsufficientEdgeDoesNotTriggerArbitrage) {
    // External fair value: 50%
    // Kalshi Ask: 45c (edge = 5c < 8c threshold)
    OrderBook ob = OrderBook::from_prices("KXNFL-BILLS", 40, 45);

    ArbitrageOpportunity opp = ArbitrageEngine::evaluate(ob, 50.0, 8.0);
    ASSERT_FALSE(opp.found);
}

TEST(ArbitrageEngine, InvalidOrCrossedBookIgnored) {
    OrderBook crossed_ob = OrderBook::from_prices("KXNFL-CROSSED", 60, 50);
    ArbitrageOpportunity opp1 = ArbitrageEngine::evaluate(crossed_ob, 70.0, 8.0);
    ASSERT_FALSE(opp1.found);

    OrderBook empty_ob = OrderBook::from_prices("KXNFL-EMPTY", 0, 0);
    ArbitrageOpportunity opp2 = ArbitrageEngine::evaluate(empty_ob, 70.0, 8.0);
    ASSERT_FALSE(opp2.found);

    // External probability out of range
    OrderBook valid_ob = OrderBook::from_prices("KXNFL-VALID", 40, 60);
    ArbitrageOpportunity opp3 = ArbitrageEngine::evaluate(valid_ob, 120.0, 8.0);
    ASSERT_FALSE(opp3.found);
}
