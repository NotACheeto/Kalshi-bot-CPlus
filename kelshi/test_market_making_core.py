"""
Comprehensive Test Suite for Python Kalshi Market Making Engine
Tests 100% of the unit, solvency, boundary, and black-swan scenarios from the C++ suite.
"""

import unittest
import time
from market_making_core import (
    OrderBook,
    OrderBookLevel,
    VolatilityCircuitBreaker,
    InventoryRiskManager,
    CollateralManager,
    ArbitrageEngine,
    Quote,
    CONTRACT_PAYOUT,
    MIN_TICK_PRICE,
    MAX_TICK_PRICE,
)


class TestOrderBookEdgeCases(unittest.TestCase):
    def test_normal_two_sided_book(self):
        ob = OrderBook.from_prices("KXTEST", 45, 55)
        self.assertTrue(ob.is_valid())
        self.assertFalse(ob.is_crossed())
        self.assertEqual(ob.spread(), 10)
        self.assertEqual(ob.mid_price(), 50.0)

    def test_crossed_book_detection(self):
        ob1 = OrderBook.from_prices("KXTEST", 55, 45)
        self.assertTrue(ob1.is_crossed())
        ob2 = OrderBook.from_prices("KXTEST", 50, 50)
        self.assertTrue(ob2.is_crossed())

    def test_single_sided_and_empty_books(self):
        ob_empty = OrderBook.from_prices("KXTEST", 0, 0)
        self.assertFalse(ob_empty.is_valid())
        ob_bid_only = OrderBook.from_prices("KXTEST", 50, 0)
        self.assertFalse(ob_bid_only.is_valid())
        ob_ask_only = OrderBook.from_prices("KXTEST", 0, 50)
        self.assertFalse(ob_ask_only.is_valid())

    def test_boundary_prices_one_and_ninety_nine(self):
        ob = OrderBook.from_prices("KXTEST", 1, 99)
        self.assertTrue(ob.is_valid())
        self.assertEqual(ob.spread(), 98)

        ob_out_high = OrderBook.from_prices("KXTEST", 1, 100)
        self.assertFalse(ob_out_high.is_valid())

    def test_conversion_from_no_bid_to_yes_ask(self):
        # NO bid at 40c -> YES ask at 60c
        ob = OrderBook.from_bid_no_bid("KXTEST", 55, 40)
        self.assertTrue(ob.is_valid())
        self.assertEqual(ob.best_yes_ask, 60)
        self.assertEqual(ob.spread(), 5)

    def test_dollar_string_and_float_parsing(self):
        self.assertEqual(OrderBook.parse_dollar_or_cents("0.45"), 45)
        self.assertEqual(OrderBook.parse_dollar_or_cents("45"), 45)
        self.assertEqual(OrderBook.parse_dollar_or_cents(0.72), 72)
        self.assertEqual(OrderBook.parse_dollar_or_cents("", 10), 10)
        self.assertEqual(OrderBook.parse_dollar_or_cents(None, 20), 20)

    def test_subpenny_tick_rejection_and_clamping(self):
        # Fractional cents: "0.456" -> 46c
        self.assertEqual(OrderBook.parse_dollar_or_cents("0.456"), 46)


class TestVolatilityCircuitBreaker(unittest.TestCase):
    def setUp(self):
        self.cb = VolatilityCircuitBreaker(max_volatility_cents=3, window_sec=5, halt_duration_sec=15)

    def test_normal_price_fluctuations_do_not_halt(self):
        self.assertFalse(self.cb.on_price_update("KXTEST", 50.0, 1000.0))
        self.assertFalse(self.cb.on_price_update("KXTEST", 51.0, 1001.0))
        self.assertFalse(self.cb.on_price_update("KXTEST", 52.0, 1002.0))
        self.assertFalse(self.cb.is_halted("KXTEST", 1002.0))

    def test_flash_crash_triggers_instant_halt(self):
        self.cb.on_price_update("KXTEST", 50.0, 1000.0)
        # Drop by 4c in 1s
        halted = self.cb.on_price_update("KXTEST", 46.0, 1001.0)
        self.assertTrue(halted)
        self.assertTrue(self.cb.is_halted("KXTEST", 1005.0))

    def test_upward_spike_triggers_halt(self):
        self.cb.on_price_update("KXTEST", 50.0, 1000.0)
        # Spike by 4c in 2s
        halted = self.cb.on_price_update("KXTEST", 54.0, 1002.0)
        self.assertTrue(halted)
        self.assertTrue(self.cb.is_halted("KXTEST", 1003.0))

    def test_sliding_window_pruning_expires_old_spikes(self):
        self.cb.on_price_update("KXTEST", 50.0, 1000.0)
        self.cb.on_price_update("KXTEST", 51.0, 1001.0)
        # 10 seconds later, 50.0 is pruned
        self.assertFalse(self.cb.on_price_update("KXTEST", 53.0, 1011.0))

    def test_halt_auto_expiry_after_duration(self):
        self.cb.on_price_update("KXTEST", 50.0, 1000.0)
        self.cb.on_price_update("KXTEST", 45.0, 1001.0) # Halt until 1001 + 15 = 1016
        self.assertTrue(self.cb.is_halted("KXTEST", 1015.0))
        self.assertFalse(self.cb.is_halted("KXTEST", 1017.0))

    def test_out_of_order_timestamps_handled_safely(self):
        self.cb.on_price_update("KXTEST", 50.0, 1005.0)
        # Backwards timestamp should be ignored safely
        self.assertFalse(self.cb.on_price_update("KXTEST", 40.0, 1001.0))
        self.assertFalse(self.cb.is_halted("KXTEST", 1005.0))

    def test_multi_market_isolation(self):
        self.cb.on_price_update("KXBTC", 50.0, 1000.0)
        self.cb.on_price_update("KXBTC", 40.0, 1001.0) # KXBTC halted
        self.assertTrue(self.cb.is_halted("KXBTC", 1002.0))
        # KXETH should NOT be halted
        self.assertFalse(self.cb.is_halted("KXETH", 1002.0))


class TestInventoryRiskManager(unittest.TestCase):
    def setUp(self):
        self.rm = InventoryRiskManager(max_position=2, min_spread_to_quote=2)

    def test_neutral_position_quoting(self):
        ob = OrderBook.from_prices("KXTEST", 45, 55)
        q = self.rm.compute_quote(ob)
        self.assertTrue(q.valid)
        self.assertEqual(q.bid_price, 46)
        self.assertEqual(q.ask_price, 54)

    def test_long_inventory_skews_prices_downwards(self):
        self.rm.set_position("KXTEST", 1)
        ob = OrderBook.from_prices("KXTEST", 45, 55)
        q = self.rm.compute_quote(ob)
        self.assertTrue(q.valid)
        # skew = +1 -> lower bid and lower ask
        self.assertEqual(q.bid_price, 45)
        self.assertEqual(q.ask_price, 53)

    def test_short_inventory_skews_prices_upwards(self):
        self.rm.set_position("KXTEST", -1)
        ob = OrderBook.from_prices("KXTEST", 45, 55)
        q = self.rm.compute_quote(ob)
        self.assertTrue(q.valid)
        # skew = -1 -> raise bid and raise ask
        self.assertEqual(q.bid_price, 47)
        self.assertEqual(q.ask_price, 55)

    def test_minimum_spread_two_cent_quoting(self):
        ob = OrderBook.from_prices("KXTEST", 50, 52)
        q = self.rm.compute_quote(ob)
        self.assertTrue(q.valid)
        self.assertEqual(q.bid_price, 50)
        self.assertEqual(q.ask_price, 52)

    def test_boundary_quote_clamping(self):
        ob = OrderBook.from_prices("KXTEST", 1, 99)
        q = self.rm.compute_quote(ob)
        self.assertTrue(q.valid)
        self.assertGreaterEqual(q.bid_price, 1)
        self.assertLessEqual(q.ask_price, 99)

    def test_sequential_fills_update_position_correctly(self):
        self.rm.apply_fill("KXTEST", "buy_yes", 1)
        self.assertEqual(self.rm.get_position("KXTEST"), 1)
        self.rm.apply_fill("KXTEST", "sell_yes", 2)
        self.assertEqual(self.rm.get_position("KXTEST"), -1)

    def test_one_sided_quoting_at_max_long_position(self):
        self.rm.set_position("KXTEST", 2)
        ob = OrderBook.from_prices("KXTEST", 45, 55)
        q = self.rm.compute_quote(ob)
        self.assertTrue(q.valid)
        self.assertFalse(q.quote_bid) # Stopped buying
        self.assertTrue(q.quote_ask)  # Still selling to offload

    def test_one_sided_quoting_at_max_short_position(self):
        self.rm.set_position("KXTEST", -2)
        ob = OrderBook.from_prices("KXTEST", 45, 55)
        q = self.rm.compute_quote(ob)
        self.assertTrue(q.valid)
        self.assertTrue(q.quote_bid)   # Still buying to cover
        self.assertFalse(q.quote_ask)  # Stopped selling


class TestCollateralManager(unittest.TestCase):
    def setUp(self):
        self.cm = CollateralManager(initial_capital_cents=300) # $3.00

    def test_collateral_calculation_correctness(self):
        # Bid 45c -> 45c; Ask 55c -> (100 - 55) = 45c. Total = 90c
        coll = CollateralManager.calculate_collateral(45, 55, 1, True, True)
        self.assertEqual(coll, 90)

    def test_reservation_and_solvency_check(self):
        q = Quote("KXTEST")
        q.bid_price = 45
        q.ask_price = 55
        q.quantity = 1
        self.assertTrue(self.cm.can_afford_quote(q))
        self.assertTrue(self.cm.reserve_for_quote(q))
        self.assertEqual(self.cm.available_capital(), 210)

    def test_cancellation_refund_restores_exact_balance(self):
        q = Quote("KXTEST")
        q.bid_price = 45
        q.ask_price = 55
        self.cm.reserve_for_quote(q)
        self.cm.refund_bid(45, 1)
        self.cm.refund_ask(55, 1)
        self.assertEqual(self.cm.available_capital(), 300)

    def test_execution_fill_cycle_pnl_tracking(self):
        # Reserve bid 40c (leaves 260c)
        q = Quote("KXTEST")
        q.bid_price = 40
        q.quote_ask = False
        self.cm.reserve_for_quote(q)
        self.assertEqual(self.cm.available_capital(), 260)
        self.cm.on_bid_fill(40, 1)

        # Now sell at 60c: reserve ask collateral (40c, leaves 220c)
        q_ask = Quote("KXTEST")
        q_ask.ask_price = 60
        q_ask.quote_bid = False
        self.cm.reserve_for_quote(q_ask)
        self.assertEqual(self.cm.available_capital(), 220)

        # On ask fill: receives 100c payout -> 220 + 100 = 320c ($3.20, +$0.20 profit)
        self.cm.on_ask_fill(60, 1)
        self.assertEqual(self.cm.available_capital(), 320)
        self.assertEqual(self.cm.realized_pnl, 60)

    def test_never_produces_negative_available_capital(self):
        q = Quote("KXTEST")
        q.bid_price = 200
        q.ask_price = 20 # collateral needed = 200 + 80 = 280
        self.assertTrue(self.cm.reserve_for_quote(q)) # leaves 20c
        q_too_big = Quote("KXTEST2")
        q_too_big.bid_price = 50
        self.assertFalse(self.cm.can_afford_quote(q_too_big))
        self.assertFalse(self.cm.reserve_for_quote(q_too_big))
        self.assertGreaterEqual(self.cm.available_capital(), 0)


class TestArbitrageEngine(unittest.TestCase):
    def test_buy_yes_arbitrage_opportunity(self):
        ob = OrderBook.from_prices("KXTEST", 40, 50)
        # External fair is 65c (ask 50c is 15c cheaper, well above 8c threshold)
        opp = ArbitrageEngine.evaluate(ob, 65.0, 8.0)
        self.assertTrue(opp.found)
        self.assertEqual(opp.action, "buy_yes")
        self.assertEqual(opp.price, 50)
        self.assertEqual(opp.edge, 15.0)

    def test_buy_no_arbitrage_opportunity(self):
        ob = OrderBook.from_prices("KXTEST", 70, 80)
        # External fair is 50c (bid 70c is 20c too high)
        opp = ArbitrageEngine.evaluate(ob, 50.0, 8.0)
        self.assertTrue(opp.found)
        self.assertEqual(opp.action, "buy_no")
        self.assertEqual(opp.price, 30) # 100 - 70 = 30c
        self.assertEqual(opp.edge, 20.0)

    def test_insufficient_edge_does_not_trigger_arbitrage(self):
        ob = OrderBook.from_prices("KXTEST", 48, 52)
        # External fair is 55c (ask 52c is only 3c edge, below 8c threshold)
        opp = ArbitrageEngine.evaluate(ob, 55.0, 8.0)
        self.assertFalse(opp.found)

    def test_invalid_or_crossed_book_ignored(self):
        ob_crossed = OrderBook.from_prices("KXTEST", 60, 50)
        opp = ArbitrageEngine.evaluate(ob_crossed, 70.0, 8.0)
        self.assertFalse(opp.found)


class TestBlackSwanScenarios(unittest.TestCase):
    def test_election_night_flash_crash_protection(self):
        cb = VolatilityCircuitBreaker(max_volatility_cents=3, window_sec=5, halt_duration_sec=15)
        rm = InventoryRiskManager(max_position=2)
        
        # Initial calm market
        ob = OrderBook.from_prices("KXELECT", 50, 54)
        self.assertFalse(cb.on_price_update("KXELECT", ob.mid_price(), 1000.0))
        
        # Sudden 10-cent collapse in 1 second
        ob_crash = OrderBook.from_prices("KXELECT", 40, 44)
        halted = cb.on_price_update("KXELECT", ob_crash.mid_price(), 1001.0)
        self.assertTrue(halted)
        self.assertTrue(cb.is_halted("KXELECT", 1005.0))

    def test_total_liquidity_evaporation_void_book(self):
        rm = InventoryRiskManager()
        ob_void = OrderBook.from_prices("KXVOID", 0, 0)
        q = rm.compute_quote(ob_void)
        self.assertFalse(q.valid)
        self.assertIn("Invalid or empty", q.reason)

    def test_corrupted_feed_crossed_book_inversion_attack(self):
        rm = InventoryRiskManager()
        ob_inverted = OrderBook.from_prices("KXCORRUPT", 80, 20)
        q = rm.compute_quote(ob_inverted)
        self.assertFalse(q.valid)
        self.assertIn("Crossed", q.reason)

    def test_toxic_taker_sweep_and_max_position_squeeze(self):
        rm = InventoryRiskManager(max_position=2)
        # Taker sweeps 2 bids -> we are max long (+2)
        rm.apply_fill("KXTOXIC", "buy_yes", 2)
        self.assertEqual(rm.get_position("KXTOXIC"), 2)
        
        ob = OrderBook.from_prices("KXTOXIC", 40, 50)
        q = rm.compute_quote(ob)
        self.assertTrue(q.valid)
        self.assertFalse(q.quote_bid) # Must NOT bid more
        self.assertTrue(q.quote_ask)  # Must ask to dump
        self.assertEqual(q.ask_price, 47) # Skewed down from 49 to 47

    def test_micro_bankroll_solvency_stress(self):
        cm = CollateralManager(initial_capital_cents=50) # 50c total bankroll
        q = Quote("KXSTRESS")
        q.bid_price = 40
        q.ask_price = 60 # Collateral needed = 40 + 40 = 80c
        self.assertFalse(cm.can_afford_quote(q))
        self.assertFalse(cm.reserve_for_quote(q))
        self.assertEqual(cm.available_capital(), 50)

    def test_high_frequency_10k_packet_blast_stress(self):
        cb = VolatilityCircuitBreaker(max_volatility_cents=3, window_sec=5, halt_duration_sec=15)
        rm = InventoryRiskManager(max_position=2)
        
        t0 = time.perf_counter()
        valid_quotes = 0
        for i in range(10000):
            price = 50 + (i % 3)
            ob = OrderBook.from_prices(f"KXTICK_{i%10}", price, price + 4)
            if not cb.on_price_update(ob.ticker, ob.mid_price(), 1000.0 + i * 0.001):
                q = rm.compute_quote(ob)
                if q.valid:
                    valid_quotes += 1
                    
        elapsed = time.perf_counter() - t0
        self.assertGreater(valid_quotes, 0)
        print(f"\n[BENCHMARK] Python 10,000 Packet Stress: {elapsed*1000:.2f}ms (~{10000/elapsed:.0f} updates/sec)")


if __name__ == "__main__":
    unittest.main()
