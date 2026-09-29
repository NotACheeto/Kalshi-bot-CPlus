#include "test_framework.hpp"
#include "../include/MarketMakingCore.hpp"

using namespace kalshi_mm;

// ==========================================
// COLLATERAL & SOLVENCY TESTS
// ==========================================

TEST(CollateralManager, CollateralCalculationCorrectness) {
    // Binary option collateral:
    // Buying YES at 45c requires 45c
    // Selling YES at 55c requires (100 - 55) = 45c
    // Total collateral for 2-sided quote (45c / 55c) = 45 + 45 = 90c
    int collateral = CollateralManager::calculate_collateral(45, 55, 1);
    ASSERT_EQ(collateral, 90);

    // Extreme quote: Bid 10c, Ask 90c
    // Bid needs 10c, Ask needs 100 - 90 = 10c, total 20c
    ASSERT_EQ(CollateralManager::calculate_collateral(10, 90, 1), 20);

    // Tight quote: Bid 49c, Ask 51c
    // Bid needs 49c, Ask needs 100 - 51 = 49c, total 98c
    ASSERT_EQ(CollateralManager::calculate_collateral(49, 51, 1), 98);
}

TEST(CollateralManager, ReservationAndSolvencyCheck) {
    long long initial_cents = 300; // $3.00 initial capital
    CollateralManager cm(initial_cents);

    ASSERT_EQ(cm.available_capital(), 300);

    // Quote 1: Bid 40c, Ask 60c -> needed: 40 + (100-60) = 80c
    ASSERT_TRUE(cm.can_afford_quote(40, 60));
    ASSERT_TRUE(cm.reserve_for_quote(40, 60));
    ASSERT_EQ(cm.available_capital(), 220);

    // Quote 2: Bid 50c, Ask 55c -> needed: 50 + 45 = 95c
    ASSERT_TRUE(cm.reserve_for_quote(50, 55));
    ASSERT_EQ(cm.available_capital(), 125);

    // Quote 3: Bid 70c, Ask 80c -> needed: 70 + 20 = 90c
    ASSERT_TRUE(cm.reserve_for_quote(70, 80));
    ASSERT_EQ(cm.available_capital(), 35);

    // Quote 4: Needs 80c, but only 35c available -> Must be rejected!
    ASSERT_FALSE(cm.can_afford_quote(40, 60));
    ASSERT_FALSE(cm.reserve_for_quote(40, 60));
    ASSERT_EQ(cm.available_capital(), 35); // Capital remained intact
}

TEST(CollateralManager, CancellationRefundRestoresExactBalance) {
    CollateralManager cm(300);

    int my_bid = 42;
    int my_ask = 58;
    int needed = CollateralManager::calculate_collateral(my_bid, my_ask); // 42 + 42 = 84c

    cm.reserve_for_quote(my_bid, my_ask);
    ASSERT_EQ(cm.available_capital(), 300 - 84);

    // Cancel both orders
    cm.refund_bid(my_bid);
    cm.refund_ask(my_ask);

    ASSERT_EQ(cm.available_capital(), 300); // Fully restored
}

TEST(CollateralManager, ExecutionFillCyclePnlTracking) {
    CollateralManager cm(1000); // $10.00 capital

    // Post bid at 45c and ask at 55c (collateral reserved: 45 + 45 = 90c)
    cm.reserve_for_quote(45, 55);
    ASSERT_EQ(cm.available_capital(), 910);

    // Bid fills at 45c
    cm.on_bid_fill(45);
    ASSERT_EQ(cm.available_capital(), 910); // Asset acquired

    // Ask fills at 55c (pair complete: bought at 45c, sold at 55c -> 10c profit)
    // On ask fill, 100c total contract payoff is credited back
    cm.on_ask_fill(55);
    ASSERT_EQ(cm.available_capital(), 1010); // $10.10 (+10c profit!)
}

TEST(CollateralManager, NeverProducesNegativeAvailableCapital) {
    CollateralManager cm(50); // Minimal 50c bankroll

    // Attempt to reserve 90c quote
    bool success = cm.reserve_for_quote(45, 55);
    ASSERT_FALSE(success);
    ASSERT_GE(cm.available_capital(), 0);
}
