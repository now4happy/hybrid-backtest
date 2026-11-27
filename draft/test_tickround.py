"""호가 단위(tick_mode) 합성시험. 실행: python3 test_tickround.py"""
import datetime as dt
import math
import random
import unittest
from dataclasses import replace

from hbt_engine import (Params, Symbol, F, Sim, prepare, simulate, buy_and_hold, fits_one_share,
                        round_tick, tick_size)
from hbt_rules import Box

ETF = Symbol("E", "E", "etf_equity")
STOCK = Symbol("S", "S", "stock")


def rows_walk(n, seed, start):
    r = random.Random(seed)
    out, px = [], start
    for i in range(n):
        o = px * (1 + r.gauss(0, 0.01))
        c = o * (1 + r.gauss(0, 0.025))
        out.append(dict(date=(dt.date(2024, 1, 1) + dt.timedelta(days=i)).isoformat(), open=o + 0.37,
                        high=max(o, c) * 1.02 + 0.37, low=min(o, c) * 0.98 + 0.37, close=c + 0.37, volume=1000))
        px = c
    return out


def on_grid(px, kind):
    t = tick_size(px, kind)
    return abs(px / t - round(px / t)) < 1e-6


class Table(unittest.TestCase):
    def check(self, kind, cases):
        for price, up, down, near in cases:
            with self.subTest(kind=kind, price=price):
                self.assertEqual(round_tick(price, "buy", "primary", kind), up)
                self.assertEqual(round_tick(price, "sell", "primary", kind), down)
                for side in ("buy", "sell"):
                    self.assertEqual(round_tick(price, side, "nearest", kind), near)

    def test_stock_boundaries(self):  # (가격, 올림, 내림, 가까운 값)
        self.check("stock", [
            (1999, 1999, 1999, 1999), (1999.4, 2000, 1999, 1999), (2000, 2000, 2000, 2000), (2001, 2005, 2000, 2000),
            (4999, 5000, 4995, 5000), (5000, 5000, 5000, 5000), (5001, 5010, 5000, 5000),
            (19999, 20000, 19990, 20000), (20000, 20000, 20000, 20000), (20001, 20050, 20000, 20000),
            (49999, 50000, 49950, 50000), (50000, 50000, 50000, 50000), (50001, 50100, 50000, 50000),
            (199999, 200000, 199900, 200000), (200000, 200000, 200000, 200000), (200001, 200500, 200000, 200000),
            (499999, 500000, 499500, 500000), (500000, 500000, 500000, 500000), (500001, 501000, 500000, 500000)])

    def test_etf_boundaries(self):
        self.check("etf_equity", [
            (1999, 1999, 1999, 1999), (1999.4, 2000, 1999, 1999), (2000, 2000, 2000, 2000), (2001, 2005, 2000, 2000),
            (4999, 5000, 4995, 5000), (5001, 5005, 5000, 5000), (19999, 20000, 19995, 20000),
            (49999, 50000, 49995, 50000), (199999, 200000, 199995, 200000), (499999, 500000, 499995, 500000),
            (500001, 500005, 500000, 500000)])
        self.assertEqual(tick_size(1999, "etf_other"), 1)
        self.assertEqual(tick_size(2000, "etf_other"), 5)

    def test_tick_sizes(self):
        for price, t in ((1999, 1), (2000, 5), (4999, 5), (5000, 10), (19999, 10), (20000, 50), (49999, 50),
                         (50000, 100), (199999, 100), (200000, 500), (499999, 500), (500000, 1000)):
            self.assertEqual(tick_size(price, "stock"), t, price)

    def test_half_and_float_noise(self):
        self.assertEqual(round_tick(2002.5, "sell", "nearest", "etf_equity"), 2005)     # 정확히 가운데 → 올림
        for side in ("buy", "sell"):                                                    # 호가 위 값의 부동소수 오차
            self.assertEqual(round_tick(15000 * 1.01, side, "primary", "etf_equity"), 15150)

    def test_none_is_identity(self):
        for kind in ("stock", "etf_equity", "etf_other"):
            for x in (0.37, 1999.4, 16254.03, 500000.7):
                for side in ("buy", "sell"):
                    self.assertEqual(round_tick(x, side, "none", kind), x)

    def test_default_and_check(self):
        self.assertEqual(Params().tick_mode, "primary")
        with self.assertRaises(ValueError):
            Params(tick_mode="floor").check()


class Engine(unittest.TestCase):
    def test_all_fills_on_grid_and_bars_unchanged(self):
        total = dict(매수=0, 매도=0, 탑매도=0)
        for sym, start in ((ETF, 16000.0), (STOCK, 70000.0), (STOCK, 4800.0)):
            for mode in ("primary", "nearest"):
                for rule in ("code", "fixed"):
                    bars = prepare(rows_walk(400, 7, start))
                    before = list(bars)
                    p = Params(tick_mode=mode, sell_rule=rule, slippage=0.0007, protect_ratio=0.0,
                               share_amount=2_000_000, tower_tp=5.0)
                    r = simulate(bars, p, sym, log=True)
                    self.assertEqual(bars, before)
                    self.assertTrue(r["trades"])
                    for t in r["trades"]:
                        total[t[2]] += 1
                        self.assertTrue(on_grid(t[5], sym.kind), (sym.kind, mode, rule, t))
        self.assertTrue(all(total.values()), total)

    def test_none_keeps_off_grid_fills(self):
        r = simulate(prepare(rows_walk(100, 7, 16000.0)), Params(tick_mode="none", protect_ratio=0.0), ETF, log=True)
        self.assertTrue(any(not on_grid(t[5], ETF.kind) for t in r["trades"]))

    def fixed_sim(self, mode):
        s = Sim(Params(sell_rule="fixed", fixed_tp=1.0, tick_mode=mode, protect_ratio=0.0), ETF, True)
        s.day_start("2025-01-02")
        s.fs.append(F(1, 20_000_000, 10, 160950.0, boxes=[Box(1, 10, 160950.0)]))   # 평단 16,095 → +1% = 16,255.95
        return s

    def test_fixed_trigger_rounding(self):
        for mode, trig in (("primary", 16255), ("nearest", 16255), ("none", 16095 * 1.01)):
            s = self.fixed_sim(mode)
            self.assertAlmostEqual(s.sniper_sell_signals(16260)[0][2], trig)
            self.assertAlmostEqual(s.events(16200, 16300, False)[0], trig)
        self.assertTrue(self.fixed_sim("primary").sniper_sell_signals(16255))
        self.assertFalse(self.fixed_sim("none").sniper_sell_signals(16255))

    def test_tower_trigger_rounding(self):
        for mode, sold in (("primary", True), ("none", False)):
            s = Sim(Params(tower_tp=10.0, tick_mode=mode, protect_ratio=0.0), ETF, True)
            s.day_start("2025-01-02")
            s.fs.append(F(1, 100.0, 10, 150030.0, done=True))          # 평단 15,003 → +10% = 16,503.3 → 16,500
            self.assertEqual(16500 in s.events(16400, 16600, False), sold)
            s.evaluate(16500, False, True, None)
            self.assertEqual(s.st["tower_sells"], int(sold))

    def test_trail_triggers_rounded(self):
        s = Sim(Params(tick_mode="primary"), ETF)
        s.trail.sync(10, 16095.0)
        up, _ = s.trail.levels()
        self.assertTrue(all(on_grid(x, ETF.kind) for x in up), up)
        ignite = s.trail.price(s.p.trail_ignite)                          # 16,255.95 → 16,255
        self.assertEqual(ignite, 16255)
        s.trail.fire(ignite)
        self.assertTrue(s.trail.armed)
        step, lock = s.p.trail_steps[0]
        s.trail.fire(s.trail.price(step))                                 # 반올림한 단계 가격에 닿으면 보존선이 올라감
        self.assertEqual(s.trail.lock, s.trail.price(lock))
        limit = s.trail.fire(s.trail.lock - 1)
        self.assertTrue(on_grid(limit, ETF.kind), limit)

    def test_pull_target_not_rounded(self):
        s = self.fixed_sim("primary")
        s.anchor = 16093.0
        raw = s.buy_ref() * (1 + s.drop() / 100) * (1 - 1e-12)
        self.assertEqual(s.events(16100, 15000, True), [raw])

    def test_buy_and_hold_and_fit_use_fill_price(self):
        bars = prepare([dict(date="2025-01-02", open=16001, high=16001, low=16001, close=16001, volume=1)])
        p = Params(buy_fee=0.0, sell_fee=0.0)
        r = buy_and_hold(bars, p, ETF, 160055)                          # 매수 체결 16,005 × 10주, 잔액 5
        self.assertEqual(r["curve"][0][1], 5 + 10 * 16001)
        self.assertEqual(r["liquidation_cost"], 10)                     # 청산 체결 16,001 → 16,000
        q = replace(p, share_amount=160_010, portion_pct=10.0)          # 1회 16,001원
        self.assertTrue(fits_one_share(replace(q, tick_mode="none"), 16001, ETF))
        self.assertFalse(fits_one_share(q, 16001, ETF))                  # 올림 16,005원

    def test_liquidation_uses_rounded_price(self):
        bars = prepare([dict(date="2025-01-02", open=16000, high=16003, low=16000, close=16003, volume=1)])
        p = Params(first_buy_filter=False, buy_fee=0.0, sell_fee=0.0, protect_ratio=0.0)
        r = simulate(bars, p, ETF)
        qty = sum(f["qty"] for f in r["f_open"])
        self.assertGreater(qty, 0)
        self.assertAlmostEqual(r["liquidation_cost"], qty * 3)          # 16,003 → 내림 16,000


if __name__ == "__main__":
    unittest.main()
