"""합성 데이터 테스트 (네트워크·실데이터 없음)."""
import csv, json, math, os, random, tempfile, unittest, datetime as dt
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import replace
from io import StringIO

import hbt_minute, run_backtest
from hbt_rules import Box, Trail, tag_on_buy, tiers
from hbt_engine import Params, Symbol, F, Sim, prepare, simulate, metrics, buy_and_hold, required_share

ETF = Symbol("T", "T", "etf_equity")
FREE = dict(buy_fee=0.0, sell_fee=0.0)
STEPS = Params().trail_steps


def day(i):
    return (dt.date(2025, 1, 2) + dt.timedelta(days=i)).isoformat()


def bar(d, o, h=None, l=None, c=None, v=1000):
    c = o if c is None else c
    return dict(date=d, open=o, high=max(o, c) if h is None else h, low=min(o, c) if l is None else l, close=c, volume=v)


def P(**kw):
    # 기존 시험은 보호 끔·sniper 경로를 명시해 유지 (새 기본값은 test_hbt_fix2 에서 시험)
    return Params(share_amount=1_000_000, **dict(FREE, **dict(dict(protect_ratio=0.0, remain="sniper"), **kw)))


def sim(p, log=True):
    s = Sim(p, ETF, log)
    s.day_start("2025-01-02")
    s.first_ok = True
    s.minute = None
    return s


def hold_f(s, fid, qty, price, done=True, **kw):
    f = F(fid, s.p.share_amount, qty, qty * price, done=done, **kw)
    if not done:
        f.boxes = [Box(100 + fid, qty, qty * price)]
    s.fs.append(f)
    s.next_f = fid + 1
    return f


def walk(n, seed, start=10000.0, swing=0.0):
    r = random.Random(seed)
    rows, px = [], start
    for i in range(n):
        o = round(px * (1 + r.gauss(0, 0.01)))
        c = round(o * (1 + r.gauss(-0.001 + swing * math.cos(i / 25 + 1.6), 0.02)))
        rows.append(bar(day(i), o, round(max(o, c) * 1.015), round(min(o, c) * 0.985), c))
        px = c
    return rows


class Tags(unittest.TestCase):
    def tags(self, prices):
        boxes = []
        for i, px in enumerate(prices, 1):
            b = Box(i, 1, px)
            tag_on_buy(boxes, b)
            boxes.append(b)
        return [b.tag for b in boxes]

    def test_cheaper_buy_becomes_l1(self):
        self.assertEqual(self.tags([100, 99, 98]), [3, 2, 1])

    def test_overflow_merges_to_seven(self):
        t = self.tags([100 - i for i in range(10)])
        self.assertEqual(max(t), 7)
        self.assertEqual(t[-1], 1)
        self.assertEqual(sorted(set(t)), [1, 2, 3, 4, 5, 6, 7])

    def test_vacant_l1_filled_by_cheapest(self):
        old = [Box(1, 1, 100, 2)]
        new = Box(2, 1, 95)
        tag_on_buy(old, new)
        self.assertEqual((new.tag, old[0].tag), (1, 2))

    def test_fallback_without_tags(self):
        gs, has = tiers([Box(1, 1, 100), Box(2, 1, 90)])
        self.assertTrue(has)
        self.assertEqual([g.price for g in gs], [90, 100])


class TrailRule(unittest.TestCase):
    def t(self):
        t = Trail(1.0, STEPS, 6.0)
        t.sync(1, 100.0)
        return t

    def test_ignite_only_arms(self):
        t = self.t()
        self.assertIsNone(t.fire(101.0))
        self.assertTrue(t.armed)
        self.assertIsNone(t.fire(101.2))

    def test_break_above_floor_not_triggered(self):
        t = self.t()
        t.fire(101.0); t.fire(101.5)
        self.assertAlmostEqual(t.lock, 101.15)
        self.assertIsNone(t.fire(101.16))

    def test_stop_break_rests_at_lock(self):
        t = self.t()
        t.fire(101.0); t.fire(102.0)
        self.assertAlmostEqual(t.fire(101.4), 101.65)

    def test_cap(self):
        t = self.t()
        t.fire(101.0)
        self.assertAlmostEqual(t.fire(106.0), 106.0)

    def test_gap_below_ignite_is_forgiven(self):
        t = self.t()
        t.fire(101.0); t.fire(101.5)
        self.assertIsNone(t.fire(99.0))
        self.assertFalse(t.armed)

    def test_reset_when_l1_changes(self):
        t = self.t()
        t.fire(101.0)
        t.sync(2, 99.0)
        self.assertFalse(t.armed)


class Sells(unittest.TestCase):
    def two(self, p, prices=(100.0, 99.0)):
        s = sim(p)
        f = hold_f(s, 1, 0, 1, done=False)
        f.boxes, f.qty, f.cost = [], 0, 0.0
        for i, px in enumerate(prices, 1):
            b = Box(i, 10, 10 * px)
            tag_on_buy(list(f.boxes), b)
            f.boxes.append(b)
            f.qty += 10
            f.cost += 10 * px
        return s, f

    def test_l2_solo_difference(self):
        for solo, n in ((False, 0), (True, 1)):
            s = sim(P(l2_solo=solo))
            f = hold_f(s, 1, 10, 100.0, done=False)
            f.boxes[0].tag = 2                      # 1층 공석
            self.assertEqual(len(s.sniper_sell_signals(101.0)), n)

    def test_l2_companion_with_l1_fire(self):
        s, f = self.two(P())
        s.sniper_sell_signals(99.99)                # 점화
        out = s.sniper_sell_signals(105.0)          # 하드캡
        self.assertEqual(sorted(b.avg for _, b, _ in out), [99.0, 100.0])

    def test_graduation_sells_all(self):
        s, f = self.two(P(tick_mode="none"), (103.0, 102.0, 100.0))   # 반올림 전 지정가를 보는 시험
        out = s.sniper_sell_signals(104.1)          # 3층(103) +1% = 104.03
        self.assertEqual(len(out), 3)
        self.assertTrue(all(abs(l - 104.03) < 1e-6 for _, _, l in out))

    def test_fixed_rule(self):
        s, f = self.two(P(sell_rule="fixed", fixed_tp=2.0))
        self.assertEqual([b.avg for _, b, _ in s.sniper_sell_signals(101.0)], [99.0])

    def test_resting_fills_only_on_return(self):
        p = P()
        rows = [bar(day(0), 10000, 10000, 10000, 10000),
                bar(day(1), 10000, 10200, 10110, 10110)]
        r = simulate(prepare(rows), replace(p, path="high_first", first_buy_filter=False), ETF, log=True)
        self.assertEqual(r["rest_orders"], 1)
        self.assertEqual(r["rest_filled"], 0)
        self.assertEqual(r["sells"], 0)


class Tower(unittest.TestCase):
    def test_half_and_remainder_back_to_sniper(self):
        s = sim(P())
        f = hold_f(s, 1, 100, 10000.0)
        self.assertTrue(s.tower_sell(f, 11000.0))
        self.assertEqual((f.qty, f.done, len(f.boxes)), (50, False, 1))
        self.assertEqual(s.anchor, 10000.0)
        self.assertIn(1, s.blocked)

    def test_hold_variant_locks(self):
        s = sim(P(remain="hold"))
        f = hold_f(s, 1, 100, 10000.0)
        s.tower_sell(f, 11000.0)
        self.assertTrue(f.locked and f.tower)
        self.assertEqual(s.sniper_boxes(), [])

    def test_one_share_never_sold_and_keeps_one(self):
        s = sim(P(tower_sell=100.0))
        self.assertFalse(s.tower_sell(hold_f(s, 1, 1, 100.0), 120.0))
        f = hold_f(s, 2, 3, 100.0)
        s.tower_sell(f, 120.0)
        self.assertEqual(f.qty, 1)

    def test_merge_and_split(self):
        s = sim(P())
        low = hold_f(s, 1, 100, 10000.0)
        old = hold_f(s, 2, 70, 9000.0, done=False)
        s.tower_sell(low, 11000.0)
        self.assertEqual(len(s.fs), 2)
        full = next(f for f in s.fs if f.done)
        rest = next(f for f in s.fs if not f.done)
        self.assertIs(full, old)
        self.assertEqual(full.qty + rest.qty, 120)
        self.assertAlmostEqual(full.cost + rest.cost, 70 * 9000 + 50 * 10000)
        self.assertLessEqual(full.cost, full.target + 1e-6)
        self.assertTrue(rest.indep)
        s.day_end()
        self.assertEqual(len(s.fs), 2)

    def test_day_end_merges_inverted(self):
        s = sim(P())
        hold_f(s, 1, 10, 100.0)
        hold_f(s, 2, 10, 110.0)
        s.day_end()
        self.assertEqual([(f.qty, f.avg) for f in s.fs], [(20, 105.0)])


class Buys(unittest.TestCase):
    def test_f_created_then_next_share(self):
        s = sim(P(portion_pct=50.0, max_shares=2))
        for _ in range(6):
            s.buy(10000.0)
        self.assertEqual([f.done for f in s.fs], [True, True])
        self.assertEqual(s.st["f_created"], 2)
        self.assertEqual(s.st["shares_blocked"], 1)     # 같은 날·같은 조건의 재평가 2번은 신호 1개
        self.assertEqual((s.st["no_cash"], s.st["depletion"]), (1, 1))   # 몫 소진과 현금 부족을 함께 기록
        self.assertAlmostEqual(s.cash, 0.0)

    def test_after_shares_add(self):
        s = sim(P(portion_pct=50.0, max_shares=1, after_shares="add"))
        for _ in range(3):
            s.buy(10000.0)
        self.assertEqual(len(s.fs), 2)
        self.assertAlmostEqual(s.st["added"], 1_000_000)
        self.assertAlmostEqual(s.flow, 1_000_000)

    def test_unaffordable(self):
        s = sim(P(portion_pct=10.0))
        self.assertFalse(s.buy(200_000.0))
        self.assertEqual(s.st["unaffordable"], 1)

    def test_portion_must_be_below_share(self):
        with self.assertRaises(ValueError):
            Params(portion_pct=100.0).check()
        with self.assertRaises(ValueError):
            Params(trail_steps=((1.0, 1.5),)).check()

    def test_pullback_depth_persists(self):
        s = sim(P())
        s.peak_layers = 9
        self.assertEqual(s.drop(), -3.5)

    def test_gate_once_per_10_minutes(self):
        rows = [bar(f"2025-01-02 09:{m:02d}", 10000 - (m - 10) * 200) for m in range(10, 22)]
        base = P(first_buy_filter=False)
        r = simulate(prepare(rows), base, ETF, log=True)
        self.assertEqual([t[1] for t in r["trades"]], [550, 551, 560])
        r0 = simulate(prepare(rows), replace(base, gate_min=0), ETF)
        self.assertGreater(r0["buys"], 5)          # 층이 깊어지면 눌림 조건 때문에 매 분 사지는 못함

    def test_open_shield_and_late_block(self):
        base = P(first_buy_filter=False)
        r = simulate(prepare([bar("2025-01-02 09:03", 10000), bar("2025-01-02 09:07", 10000)]), base, ETF, log=True)
        self.assertEqual(r["trades"][0][1], 547)
        r = simulate(prepare([bar("2025-01-02 14:30", 10000)]), base, ETF)
        self.assertEqual(r["buys"], 0)

    def test_limit_lock_and_zero_volume(self):
        rows = [dict(bar(day(0), 13000), prev_close=10000), bar(day(1), 9000, v=0)]
        r = simulate(prepare(rows), P(), ETF)
        self.assertEqual((r["buys"], r["limit_blocked"]), (0, 1))

    def test_halt(self):
        rows = [bar(day(i), 10000 - 300 * i) for i in range(5)]
        r = simulate(prepare(rows), P(halts=((day(0), day(2), "stop_buy"),)), ETF, log=True)
        self.assertEqual(r["trades"][0][0], day(3))


class Invariants(unittest.TestCase):
    def test_random_paths(self):
        variants = [dict(), dict(sell_rule="fixed"), dict(remain="hold"), dict(l2_solo=True),
                    dict(after_shares="add", max_shares=1), dict(tower_tp=3.0, tower_sell=30.0, tower_per_day=3)]
        sold_tower = 0
        for seed in range(8):
            bars = prepare(walk(250, seed, swing=0.008))
            for kw in variants:
                p = P(**kw)
                r = simulate(bars, p, ETF, log=True)
                qty, cash = 0, r["start_cash"] + r["added"]
                for _, _, kind, _, q, px in r["trades"]:
                    qty += q if kind == "매수" else -q
                    cash += -q * px if kind == "매수" else q * px
                    self.assertGreaterEqual(qty, 0)
                self.assertEqual(qty, sum(f["qty"] for f in r["f_open"]))
                stock = qty * bars[-1][6]
                self.assertAlmostEqual(r["curve"][-1][1], cash + stock, delta=len(r["trades"]) * 2 + 1)
                invested = sum(f["qty"] * f["avg"] for f in r["f_open"])
                pnl = r["sniper_pnl"] + r["tower_pnl"]
                self.assertAlmostEqual(r["start_cash"] + r["added"] + pnl + stock - invested, r["curve"][-1][1], delta=qty * 0.01 + 1)
                self.assertTrue(all(c[1] - c[3] >= -1e-6 for c in r["curve"]), "현금 음수")
                sold_tower += r["tower_sells"]
        self.assertGreater(sold_tower, 0)

    def test_costs_reduce_result(self):
        bars = prepare(walk(250, 3))
        free = simulate(bars, P(), ETF)
        paid = simulate(bars, replace(P(), buy_fee=0.01, sell_fee=0.01), ETF)
        self.assertLess(paid["final"], free["final"])


class Metrics(unittest.TestCase):
    def test_added_cash_is_not_return(self):
        res = dict(curve=[(day(0), 100.0, 0.0, 50.0, True), (day(1), 200.0, 100.0, 50.0, False)],
                   start_cash=100.0, liquidation_cost=0.0)
        m = metrics(res)
        self.assertAlmostEqual(m["ret"], 0.0)
        self.assertEqual(m["freeze"], 1)

    def test_mdd_and_hold(self):
        bars = prepare([bar(day(0), 100), bar(day(1), 50), bar(day(2), 100)])
        m = metrics(buy_and_hold(bars, P(), ETF, 1000))
        self.assertAlmostEqual(m["mdd"], 0.5)
        self.assertAlmostEqual(m["ret"], 0.0)

    def test_required_share(self):
        self.assertEqual(required_share(20_000_000, 100_000, 10.0), 20_000_000)
        self.assertEqual(required_share(20_000_000, 2_000_000, 8.0), 26_000_000)


def write_minutes(path, ndays=40, seed=1, partial=True):
    r = random.Random(seed)
    px = 10000.0
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow("time,open,high,low,close,volume".split(","))
        d, n = dt.date(2025, 11, 3), 0
        while n < ndays:
            if d.weekday() < 5:
                n += 1
                end = 11 * 60 if partial and n == ndays else 15 * 60 + 30
                for m in [8 * 60 + 30] + list(range(540, end + 1, 3)):
                    o = round(px)
                    c = round(o * (1 + r.gauss(0, 0.004)))
                    w.writerow([f"{d:%Y%m%d}{m // 60:02d}{m % 60:02d}00", o, max(o, c) + 10, min(o, c) - 10, c, 100])
                    px = c
            d += dt.timedelta(days=1)


class MinuteAndCli(unittest.TestCase):
    def test_loader_filters(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "m.csv")
            write_minutes(path, 5)
            rows, info, _ = hbt_minute.load(path)
            self.assertEqual(info["days"], 4)
            self.assertEqual(info["outside_session"], {"08시": 5})
            self.assertTrue(info["dropped_last_day"])
            agg = hbt_minute.to_daily(rows)
            self.assertEqual(len(agg), 4)
            self.assertEqual(sum(a["volume"] for a in agg), sum(x["volume"] for x in rows))
            self.assertEqual(max(a["high"] for a in agg), max(x["high"] for x in rows))

    def test_cli_all_commands_offline(self):
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "T.csv"), "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=["date", "open", "high", "low", "close", "volume"])
                w.writeheader()
                rows = []
                for i, r in enumerate(walk(900, 5)):
                    r["date"] = (dt.date(2023, 7, 1) + dt.timedelta(days=i)).isoformat()
                    rows.append(r)
                w.writerows(rows)
            write_minutes(os.path.join(td, "T_1m.csv"))
            cfg = dict(period=dict(start="2023-07-01", end="2025-12-31"),
                       source="csv:" + os.path.join(td, "{ticker}.csv"),
                       symbols=[dict(ticker="T", name="합성", kind="etf_equity", price_limit=0.3)],
                       segments=[dict(name=y, start=f"{y}-01-01", end=f"{y}-12-31", type="auto") for y in ("2023", "2024", "2025")],
                       auto_up_threshold=0.15, params={},
                       sweep=dict(sell_rule=["code", "fixed"], fixed_tp=[1.0, 2.0], remain=["sniper", "hold"]),
                       minute=dict(file="{code}_1m.csv", since="2025-11-03", sessions={}))
            cp = os.path.join(td, "c.json")
            with open(cp, "w", encoding="utf-8") as f:
                json.dump(cfg, f)
            for cmd, name, extra in (("run", "report.md", []), ("sweep", "sweep_report.md", []),
                                     ("compare", "compare_report.md", ["--minute-dir", td, "--compare-daily", "none"])):
                out = os.path.join(td, cmd)
                with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                    run_backtest.main([cmd, "--config", cp, "--out", out, "--jobs", "2"] + extra)
                with open(os.path.join(out, name), encoding="utf-8") as g:
                    text = g.read()
                self.assertIn("분봉이 없는 구간은 일봉만으로 판단한다", text)
                self.assertIn("합성", text)
            self.assertIn("일봉 근사(분봉 합성)", text)
            # 외부 일봉 열 (오프라인: 분봉을 합친 CSV 를 외부 일봉으로 사용)
            rows, _, _ = hbt_minute.load(os.path.join(td, "T_1m.csv"))
            with open(os.path.join(td, "ext_T.csv"), "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=["date", "open", "high", "low", "close", "volume"])
                w.writeheader()
                w.writerows(hbt_minute.to_daily(rows))
            out = os.path.join(td, "compare_ext")
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                run_backtest.main(["compare", "--config", cp, "--out", out, "--minute-dir", td, "--no-grid",
                                   "--compare-daily", "csv:" + os.path.join(td, "ext_{ticker}.csv")])
            with open(os.path.join(out, "compare_report.md"), encoding="utf-8") as g:
                self.assertIn("일봉 근사(csv)", g.read())

    def test_grid_dedup(self):
        _, ps = run_backtest.grid_params(Params(), dict(sell_rule=["code", "fixed"], fixed_tp=[1.0, 2.0], l2_solo=[False, True]))
        self.assertEqual(len(ps), 4)

    def test_spearman(self):
        self.assertAlmostEqual(run_backtest.spearman([1, 2, 3], [10, 20, 30]), 1.0)
        self.assertAlmostEqual(run_backtest.spearman([1, 2, 3], [3, 2, 1]), -1.0)


if __name__ == "__main__":
    unittest.main()
