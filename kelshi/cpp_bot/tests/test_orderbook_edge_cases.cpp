#include "test_framework.hpp"
#include "../include/MarketMakingCore.hpp"

using namespace kalshi_mm;

// ==========================================
// ORDER BOOK EDGE CASES & PROTOCOL BOUNDARIES
// ==========================================

TEST(OrderBookEdgeCases, NormalTwoSidedBook) {
    OrderBook ob = OrderBook::from_prices("KXHIGH-26JAN", 48, 52);
    ASSERT_TRUE(ob.is_valid());
    ASSERT_FALSE(ob.is_crossed());
    ASSERT_EQ(ob.spread(), 4);
    ASSERT_NEAR(ob.mid_price(), 50.0, 0.001);
}

TEST(OrderBookEdgeCases, CrossedBookDetection) {
    // Bid >= Ask is invalid/crossed
    OrderBook ob1 = OrderBook::from_prices("KXTEST-1", 55, 50);
    ASSERT_TRUE(ob1.is_crossed());

    OrderBook ob2 = OrderBook::from_prices("KXTEST-2", 50, 50);
    ASSERT_TRUE(ob2.is_crossed()); // Equal bid and ask is crossed on prediction markets
}

TEST(OrderBookEdgeCases, SingleSidedAndEmptyBooks) {
    OrderBook ob_no_bid = OrderBook::from_prices("KXEMPTY-1", 0, 50);
    ASSERT_FALSE(ob_no_bid.is_valid());

    OrderBook ob_no_ask = OrderBook::from_prices("KXEMPTY-2", 50, 0);
    ASSERT_FALSE(ob_no_ask.is_valid());

    OrderBook ob_empty = OrderBook::from_prices("KXEMPTY-3", 0, 0);
    ASSERT_FALSE(ob_empty.is_valid());
}

TEST(OrderBookEdgeCases, BoundaryPricesOneAndNinetyNine) {
    // 1c and 99c are extreme prediction market boundaries
    OrderBook ob_extreme_low = OrderBook::from_prices("KXEXTREME-1", 1, 3);
    ASSERT_TRUE(ob_extreme_low.is_valid());
    ASSERT_EQ(ob_extreme_low.spread(), 2);

    OrderBook ob_extreme_high = OrderBook::from_prices("KXEXTREME-2", 97, 99);
    ASSERT_TRUE(ob_extreme_high.is_valid());
    ASSERT_EQ(ob_extreme_high.spread(), 2);

    // 0c or 100c are invalid active trading quotes
    OrderBook ob_zero = OrderBook::from_prices("KXEXTREME-3", 0, 99);
    ASSERT_FALSE(ob_zero.is_valid());

    OrderBook ob_hundred = OrderBook::from_prices("KXEXTREME-4", 1, 100);
    ASSERT_FALSE(ob_hundred.is_valid());
}

TEST(OrderBookEdgeCases, ConversionFromNoBidToYesAsk) {
    // If NO Bid is 60c, YES Ask is 100 - 60 = 40c
    OrderBook ob = OrderBook::from_bid_no_bid("KXCONVERT-1", 35, 60);
    ASSERT_EQ(ob.best_yes_bid, 35);
    ASSERT_EQ(ob.best_yes_ask, 40);
    ASSERT_EQ(ob.spread(), 5);

    // If NO Bid + YES Bid > 100, market is crossed (e.g. YES Bid 55c, NO Bid 55c -> YES Ask 45c)
    OrderBook ob_crossed = OrderBook::from_bid_no_bid("KXCONVERT-2", 55, 55);
    ASSERT_EQ(ob_crossed.best_yes_bid, 55);
    ASSERT_EQ(ob_crossed.best_yes_ask, 45);
    ASSERT_TRUE(ob_crossed.is_crossed());
}

TEST(OrderBookEdgeCases, DollarStringAndFloatParsing) {
    // Dollars to cents parsing
    ASSERT_EQ(OrderBook::parse_dollar_or_cents("0.45"), 45);
    ASSERT_EQ(OrderBook::parse_dollar_or_cents("0.01"), 1);
    ASSERT_EQ(OrderBook::parse_dollar_or_cents("0.99"), 99);
    ASSERT_EQ(OrderBook::parse_dollar_or_cents("0.505"), 51); // Rounding check
    ASSERT_EQ(OrderBook::parse_dollar_or_cents("50"), 50);    // Cents integer string

    // Malformed / NaN inputs
    ASSERT_EQ(OrderBook::parse_dollar_or_cents(""), 0);
    ASSERT_EQ(OrderBook::parse_dollar_or_cents("invalid_text", 0), 0);
    ASSERT_EQ(OrderBook::parse_dollar_or_cents("null", -1), -1);
}

TEST(OrderBookEdgeCases, SubPennyTickRejectionAndClamping) {
    InventoryRiskManager risk_mgr;
    
    // OrderBook with 1c spread (49c / 50c) - too tight to quote safely for maker
    OrderBook tight_ob = OrderBook::from_prices("KXTIGHT", 49, 50);
    Quote q = risk_mgr.compute_quote(tight_ob);
    ASSERT_FALSE(q.valid);
    ASSERT_NE(q.reason.find("too tight"), std::string::npos);
}
