#include "test_framework.hpp"
#include "../include/MarketMakingCore.hpp"

using namespace kalshi_mm;

// ==========================================
// INVENTORY SKEW & RISK CONTROLS TESTS
// ==========================================

TEST(InventoryRiskManager, NeutralPositionQuoting) {
    InventoryRiskManager risk_mgr;
    risk_mgr.set_position("KXELEC", 0);

    // Book: 45c bid / 55c ask (10c spread)
    OrderBook ob = OrderBook::from_prices("KXELEC", 45, 55);
    Quote q = risk_mgr.compute_quote(ob);

    ASSERT_TRUE(q.valid);
    // When neutral (pos=0), my_bid = 45 + 1 = 46c, my_ask = 55 - 1 = 54c
    ASSERT_EQ(q.bid_price, 46);
    ASSERT_EQ(q.ask_price, 54);
}

TEST(InventoryRiskManager, LongInventorySkewsPricesDownwards) {
    InventoryRiskManager risk_mgr;
    
    // Holding +1 position -> skew +1
    risk_mgr.set_position("KXELEC", 1);
    ASSERT_EQ(risk_mgr.calculate_skew("KXELEC"), 1);

    OrderBook ob = OrderBook::from_prices("KXELEC", 45, 55);
    Quote q = risk_mgr.compute_quote(ob);

    // my_bid = (45 + 1) - 1 = 45c, my_ask = (55 - 1) - 1 = 53c (encourages selling YES)
    ASSERT_TRUE(q.valid);
    ASSERT_EQ(q.bid_price, 45);
    ASSERT_EQ(q.ask_price, 53);

    // Holding max long position (+2) -> skew +2
    risk_mgr.set_position("KXELEC", 2);
    ASSERT_EQ(risk_mgr.calculate_skew("KXELEC"), 2);

    Quote q2 = risk_mgr.compute_quote(ob);
    // my_bid = (45 + 1) - 2 = 44c, my_ask = (55 - 1) - 2 = 52c
    ASSERT_TRUE(q2.valid);
    ASSERT_EQ(q2.bid_price, 44);
    ASSERT_EQ(q2.ask_price, 52);
}

TEST(InventoryRiskManager, ShortInventorySkewsPricesUpwards) {
    InventoryRiskManager risk_mgr;

    // Holding -2 (short YES / long NO) -> skew -2
    risk_mgr.set_position("KXELEC", -2);
    ASSERT_EQ(risk_mgr.calculate_skew("KXELEC"), -2);

    OrderBook ob = OrderBook::from_prices("KXELEC", 45, 55);
    Quote q = risk_mgr.compute_quote(ob);

    // my_bid = (45 + 1) - (-2) = 48c, my_ask = (55 - 1) - (-2) = 56c (encourages buying YES)
    ASSERT_TRUE(q.valid);
    ASSERT_EQ(q.bid_price, 48);
    ASSERT_EQ(q.ask_price, 56);
}

TEST(InventoryRiskManager, MinimumSpreadTwoCentQuoting) {
    InventoryRiskManager risk_mgr;
    risk_mgr.set_position("KXSPREAD2", 0);

    // Spread is exactly 2c (50c / 52c)
    OrderBook ob = OrderBook::from_prices("KXSPREAD2", 50, 52);
    Quote q = risk_mgr.compute_quote(ob);

    ASSERT_TRUE(q.valid);
    // For spread == 2: my_bid = best_bid - skew = 50, my_ask = best_ask - skew = 52
    ASSERT_EQ(q.bid_price, 50);
    ASSERT_EQ(q.ask_price, 52);
}

TEST(InventoryRiskManager, BoundaryQuoteClamping) {
    InventoryRiskManager risk_mgr;

    // Extremely low prices: Book at 1c / 3c with -2 skew (pos = -2)
    risk_mgr.set_position("KXLOW", -2);
    OrderBook ob_low = OrderBook::from_prices("KXLOW", 1, 3);
    Quote q_low = risk_mgr.compute_quote(ob_low);

    ASSERT_GE(q_low.bid_price, MIN_TICK_PRICE);
    ASSERT_LE(q_low.ask_price, MAX_TICK_PRICE);
    ASSERT_LT(q_low.bid_price, q_low.ask_price);

    // Extremely high prices: Book at 97c / 99c with +2 skew (pos = +2)
    risk_mgr.set_position("KXHIGH", 2);
    OrderBook ob_high = OrderBook::from_prices("KXHIGH", 97, 99);
    Quote q_high = risk_mgr.compute_quote(ob_high);

    ASSERT_GE(q_high.bid_price, MIN_TICK_PRICE);
    ASSERT_LE(q_high.ask_price, MAX_TICK_PRICE);
    ASSERT_LT(q_high.bid_price, q_high.ask_price);
}

TEST(InventoryRiskManager, SequentialFillsUpdatePositionCorrectly) {
    InventoryRiskManager risk_mgr;
    std::string ticker = "KXSEQ";

    ASSERT_EQ(risk_mgr.get_position(ticker), 0);

    // Fill 1: buy YES
    risk_mgr.apply_fill(ticker, "bid", 1);
    ASSERT_EQ(risk_mgr.get_position(ticker), 1);

    // Fill 2: buy YES
    risk_mgr.apply_fill(ticker, "bid", 1);
    ASSERT_EQ(risk_mgr.get_position(ticker), 2);

    // Fill 3: sell YES
    risk_mgr.apply_fill(ticker, "ask", 1);
    ASSERT_EQ(risk_mgr.get_position(ticker), 1);

    // Fill 4 & 5: sell YES
    risk_mgr.apply_fill(ticker, "ask", 2);
    ASSERT_EQ(risk_mgr.get_position(ticker), -1);
}

TEST(InventoryRiskManager, OneSidedQuotingAtMaxLongPosition) {
    InventoryRiskManager risk_mgr;
    std::string ticker = "KXLIMIT-LONG";
    risk_mgr.set_position(ticker, 2); // At max position limit (+2)

    OrderBook ob = OrderBook::from_prices(ticker, 45, 55);
    Quote q = risk_mgr.compute_quote(ob);

    ASSERT_TRUE(q.valid);
    ASSERT_FALSE(q.quote_bid); // MUST NOT place buy order
    ASSERT_TRUE(q.quote_ask);  // MUST place sell order to flatten inventory
    ASSERT_EQ(q.ask_price, 52);
}

TEST(InventoryRiskManager, OneSidedQuotingAtMaxShortPosition) {
    InventoryRiskManager risk_mgr;
    std::string ticker = "KXLIMIT-SHORT";
    risk_mgr.set_position(ticker, -2); // At max short limit (-2)

    OrderBook ob = OrderBook::from_prices(ticker, 45, 55);
    Quote q = risk_mgr.compute_quote(ob);

    ASSERT_TRUE(q.valid);
    ASSERT_TRUE(q.quote_bid);   // MUST place buy order to cover short
    ASSERT_FALSE(q.quote_ask);  // MUST NOT place sell order
    ASSERT_EQ(q.bid_price, 48);
}

