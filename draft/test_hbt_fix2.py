"""초안 수정 2차(11가지) 합성시험. 네트워크·실데이터 없음."""
import csv, datetime as dt, json, os, tempfile, unittest, urllib.request
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import replace
from io import StringIO

import kiwoom_minute_probe as K
import run_backtest as R
from hbt_rules import Box
from hbt_engine import Params, Sim, F, prepare, simulate, metrics, buy_and_hold, fits_one_share
from test_hbt import ETF, FREE, P, bar, day, sim, hold_f, walk


class Fix01Auction(unittest.TestCase):
    def go(self, second):
        p = P(sell_rule="fixed", fixed_tp=1.0, first_buy_filter=False)
        rows = [bar("2026-11-25 09:10", 100), second]
        return simulate(prepare(rows), p, ETF, log=True)

    def spike(self, when, v):
        return bar(when, 100, 102, 100, 101.5, v=v)

    def sold(self, r):
        return [t for t in r["trades"] if t[2] == "매도"]

    def test_A_1520_with_volume_not_sold_but_settled(self):
        r = self.go(self.spike("2026-11-26 15:20", 200))
        self.assertEqual(self.sold(r), [])
        self.assertEqual([c[0] for c in r["curve"]], ["2026-11-25", "2026-11-26"])
        c = self.go(self.spike("2026-11-26 15:20", 0))
        self.assertEqual(r["curve"][-1][1], c["curve"][-1][1])

    def test_B_1519_sold(self):
        r = self.go(self.spike("2026-11-26 15:19", 200))
        self.assertEqual([(t[1], t[4], t[5]) for t in self.sold(r)], [(919, 1000, 101.0)])

    def test_C_1520_zero_volume(self):
        r = self.go(self.spike("2026-11-26 15:20", 0))
        self.assertEqual(self.sold(r), [])
        self.assertEqual(len(r["curve"]), 2)

    def test_D_daily_unchanged(self):
        p = P(sell_rule="fixed", fixed_tp=1.0)
        r = simulate(prepare([bar("2026-11-25", 100), self.spike("2026-11-26", 200)]), p, ETF, log=True)
        self.assertEqual([(t[0], t[4], t[5]) for t in self.sold(r)][0], ("2026-11-26", 1000, 101.0))


def row(cagr, mdd, cal, **kw):
    base = dict(feasible=True, depletion=0, late_depletion=0, tower_ties=0, cagr=cagr, mdd=mdd, calmar=cal)
    return dict(base, **kw)


class Fix02And06Ranking(unittest.TestCase):
    def test_muse_ABC_only_B_ranked(self):
        rows = [row(.20, .10, 2.0, depletion=1), row(.10, .10, 1.0), row(.05, 0.0, None)]
        order, v = R.rank(rows, "")
        self.assertEqual(order, [1])
        self.assertEqual(v, ["제외(고갈)", "순위", "판정 보류(칼마 미산출)"])

    def test_score_none_is_not_infinite(self):
        self.assertIsNone(R.score(row(.05, 0.0, None), ""))
        self.assertIsNone(R.score(row(-.05, 0.0, None), ""))

    def test_no_calmar_held_for_any_cagr_sign(self):
        for cagr in (.05, 0.0, -.05):
            with self.subTest(cagr=cagr):
                r = row(cagr, 0.0, None)
                self.assertIsNone(R.score(r, ""))
                self.assertEqual(R.verdict(r, ""), "판정 보류(칼마 미산출)")
                self.assertEqual(R.rank([r], "")[0], [])

    def test_negative_AB_order_kept(self):
        rows = [row(-.10, .50, -.20), row(-.08, .10, -.80)]   # A, B
        self.assertEqual(R.rank(rows, "")[0], [1, 0])

    def test_positive_calmar_order_kept_and_before_negative(self):
        rows = [row(-.08, .10, -.80), row(.10, .10, 1.0), row(.10, .05, 2.0), row(0.0, .10, 0.0)]
        self.assertEqual(R.rank(rows, "")[0], [2, 1, 3, 0])

    def test_unaffordable_and_shares_blocked_are_not_depletion(self):
        order, _ = R.rank([row(.1, .1, 1.0, unaffordable=9, shares_blocked=9)], "")
        self.assertEqual(order, [0])

    def test_one_row_has_counters(self):
        R._CTX.clear()
        R._init(dict(bars=prepare(walk(120, 1)), sym=ETF))
        r = R._one(P(protect_ratio=0.2, remain="hold"))
        for k in ("no_cash", "cash_protection", "unaffordable", "shares_blocked", "depletion"):
            self.assertIn(k, r)


def broke(minute=600, cash=5.0, **kw):
    """현금이 1주에도 모자란 상태 (첫 매수 신호)"""
    s = sim(P(portion_pct=10.0, **kw))
    s.minute, s.open_min, s.gate_bucket, s.last_buy_minute = minute, 540, None, None
    s.cash = cash
    return s


class Fix03Signals(unittest.TestCase):
    def ev(self, s, px=10000.0):
        s.evaluate(px, True, True, None)

    def test_four_evaluations_in_one_bar_count_once(self):
        s = broke()
        for px in (10000, 9900, 10100, 10000):      # 분 안의 회복·재하락도 중복
            self.ev(s, px)
        self.assertEqual((s.st["no_cash"], s.st["depletion"]), (1, 1))

    def test_next_minute_is_new_signal(self):
        s = broke()
        self.ev(s)
        s.minute = 601
        self.ev(s)
        self.assertEqual(s.st["depletion"], 2)

    def test_daily_once_per_day(self):
        s = broke(minute=None)
        self.ev(s); self.ev(s)
        s.day_start("2025-01-03"); s.first_ok = True
        self.ev(s)
        self.assertEqual(s.st["depletion"], 2)

    def test_changed_reference_or_f_is_new_condition(self):
        s = broke()
        f = hold_f(s, 1, 10, 10000.0, done=False)
        self.ev(s, 9800); self.ev(s, 9790)
        self.assertEqual(s.st["depletion"], 1)
        s.anchor = 9950.0                             # 기준가 변경
        self.ev(s, 9800)
        self.assertEqual(s.st["depletion"], 2)
        f.id = 2                                       # 받을 F 변경
        self.ev(s, 9800)
        self.assertEqual(s.st["depletion"], 3)

    def test_causes_recorded_together_depletion_once(self):
        s = broke(cash=5.0)
        self.assertFalse(s.buy(200_000.0))             # 1회 매수액도 현금도 1주 미달
        self.assertEqual((s.st["unaffordable"], s.st["no_cash"], s.st["cash_protection"], s.st["depletion"]), (1, 1, 0, 1))

    def test_late_depletion_is_flagged_not_counted(self):
        s = broke(cash=5.0)
        s.cash = 250_000.0
        s.buy(200_000.0)                               # 첫 실패: 1주 미달만 (고갈 아님)
        s.cash = 5.0
        s.buy(50_000.0)                                # 같은 창·같은 조건에서 현금 부족
        s.buy(50_000.0)
        self.assertEqual((s.st["depletion"], s.st["late_depletion"], s.st["no_cash"]), (0, 1, 0))
        self.assertEqual(R.verdict(row(.1, .1, 1.0, late_depletion=1), ""), "판정 보류(창 안 뒤늦은 고갈)")

    def test_failed_buy_in_simulate_minute_bars(self):
        rows = [bar("2025-01-02 09:10", 10000, 10010, 9990, 10000), bar("2025-01-02 09:11", 10000, 10010, 9990, 10000)]
        p = P(first_buy_filter=False, protect_ratio=0.95, max_shares=1)   # 첫 매수가 보호선에 걸림
        r = simulate(prepare(rows), p, ETF)
        self.assertEqual((r["buys"], r["cash_protection"], r["depletion"]), (0, 2, 2))


class Fix04Protect(unittest.TestCase):
    def go(self, cash, amount, ratio=0.2, fee=0.0):
        s = sim(Params(share_amount=amount * 10, portion_pct=10.0, protect_ratio=ratio, buy_fee=fee, sell_fee=0.0))
        s.start_cash, s.cash = 100.0, cash
        s.buy(10.0)
        q = sum(f.qty for f in s.fs)
        return q, tuple(s.st[k] for k in ("no_cash", "unaffordable", "cash_protection", "shares_blocked", "depletion"))

    def test_cases(self):
        table = [((5, 5), 0, (1, 1, 0, 0, 1)),        # 동시 부족: 보호는 원인 아님
                 ((25, 5), 0, (0, 1, 0, 0, 0)),       # 비중만 부족
                 ((25, 10), 0, (0, 0, 1, 0, 1)),      # 보호 차단
                 ((30, 10), 1, (0, 0, 0, 0, 0)),      # 보호선과 같음: 허용
                 ((29.99, 10), 0, (0, 0, 1, 0, 1)),   # 보호선 바로 아래
                 ((5, 10), 0, (1, 0, 0, 0, 1))]       # 예정 0주: 보호 기록 안 함
        for args, q, counts in table:
            with self.subTest(args=args):
                self.assertEqual(self.go(*args), (q, counts))

    def test_all_or_nothing_vs_partial(self):
        self.assertEqual(self.go(35, 20)[0], 0)             # 2주 사면 15 < 20 → 1주도 안 삼
        self.assertEqual(self.go(15, 20, ratio=0.0)[0], 1)  # 보호 끔: 부분 매수

    def test_fee_partial_when_off(self):
        self.assertEqual(self.go(50, 100, ratio=0.0, fee=0.01), (4, (0, 0, 0, 0, 0)))

    def test_line_is_ratio_of_initial_cash(self):
        self.assertEqual(self.go(45, 10, ratio=0.4)[0], 0)
        self.assertEqual(self.go(45, 10, ratio=0.3)[0], 1)

    def test_default_and_add_mode(self):
        self.assertEqual(Params().protect_ratio, 0.2)
        with self.assertRaises(ValueError):
            Params(after_shares="add").check()
        Params(after_shares="add", protect_ratio=0.0).check()

    def test_shares_blocked_and_no_cash_together(self):
        s = sim(P(max_shares=1))
        hold_f(s, 1, 100, 10000.0)
        s.cash = 0.0
        s.buy(9000.0)
        self.assertEqual((s.st["shares_blocked"], s.st["no_cash"], s.st["depletion"]), (1, 1, 1))

    def test_f_limit_with_protection_breach_records_both(self):
        s = sim(P(max_shares=1, protect_ratio=0.5))        # 해석: 원인을 각각 진단 (VERIFY.md 해석 2)
        hold_f(s, 1, 100, 10000.0)
        s.cash = 550_000.0
        s.buy(10000.0)
        self.assertEqual((s.st["shares_blocked"], s.st["cash_protection"], s.st["no_cash"], s.st["depletion"]), (1, 1, 0, 1))


class Fix05Capital(unittest.TestCase):
    def bars(self, first, later):
        return prepare([bar(day(0), first), bar(day(1), later)])

    def plist(self):
        return [Params(portion_pct=8.0), Params(portion_pct=10.0)]

    def test_future_high_does_not_change_capital(self):
        for basis in ("fixed", "first_bar"):
            a, _ = R.sized(dict(share_basis=basis), ETF, self.bars(100_000, 100_000), self.plist())
            b, _ = R.sized(dict(share_basis=basis), ETF, self.bars(100_000, 3_000_000), self.plist())
            self.assertEqual(a, b)
            self.assertEqual({p.share_amount for p in b}, {20_000_000})

    def test_infeasible_marked_without_raising_capital(self):
        out, note = R.sized({}, ETF, self.bars(1_800_000, 1_800_000), self.plist())
        self.assertEqual({p.share_amount for p in out}, {20_000_000})
        self.assertEqual([fits_one_share(p, 1_800_000) for p in out], [False, True])
        self.assertIn("실행 불가 설정 1/2개", note)
        R._CTX.clear(); R._init(dict(bars=self.bars(1_800_000, 1_000_000), sym=ETF))
        r = R._one(out[0])
        self.assertFalse(r["feasible"])
        self.assertEqual(R.verdict(r, "liq_"), "실행 불가")

    def test_first_bar_option_is_explicit_and_same_for_all(self):
        out, note = R.sized(dict(share_basis="first_bar"), ETF, self.bars(2_000_000, 9_000_000), self.plist())
        self.assertEqual({p.share_amount for p in out}, {26_000_000})
        self.assertIn("늘림", note)


class Fix07And08Metrics(unittest.TestCase):
    def res(self):
        days = [(dt.date(2023, 1, 1) + dt.timedelta(days=i * 2)).isoformat() for i in range(261)]
        curve = [(d, 1000.0 + i, 0.0, 500.0, True) for i, d in enumerate(days)]
        return dict(curve=curve, start_cash=1000.0, liquidation_cost=10.0), days

    def test_cagr_same_basis(self):
        res, days = self.res()
        full, seg = metrics(res), metrics(res, set(days))
        self.assertEqual(full["cagr"], seg["cagr"])
        self.assertAlmostEqual(full["cagr"], (1 + full["ret"]) ** (245 / 261) - 1)

    def test_segments_never_carry_liquidation(self):
        res, days = self.res()
        free = dict(res, liquidation_cost=0.0)
        for part in (set(days[-50:]), set(days[:-1]), set(days)):
            self.assertEqual(metrics(res, part), metrics(free, part))
        with self.assertRaises(ValueError):
            metrics(res, set(days), liquidate=True)

    def test_full_shows_both(self):
        res, days = self.res()
        self.assertAlmostEqual(metrics(res)["ret"] - metrics(res, liquidate=True)["ret"], 0.01)
        rows = R.summarize(res, res, [("2023", "상승", set(days[:100]))])
        self.assertEqual([(r[0], r[1]) for r in rows],
                         [("2023", "평가(청산 전)"), ("상승 합산", "평가(청산 전)"),
                          ("전체", "평가(청산 전)"), ("전체", "청산차감")])


class Fix09Tower(unittest.TestCase):
    def sold(self, order, price, a1=110.0, a2=100.0, **kw):
        s = sim(P(tower_order=order, **kw))
        hold_f(s, 1, 10, a1); hold_f(s, 2, 10, a2)
        s.evaluate(price, False, True, None)
        return [t[3] for t in s.log if t[2] == "탑매도"], s

    def test_two_rules(self):
        self.assertEqual(self.sold("id_desc", 112.0)[0], [2])
        self.assertEqual(self.sold("avg_desc", 112.0)[0], [2])
        self.assertEqual(self.sold("id_desc", 122.0)[0], [2])
        self.assertEqual(self.sold("avg_desc", 122.0)[0], [1])

    def test_tie_is_held_for_judgement(self):
        ids, s = self.sold("avg_desc", 112.0, 100.0, 100.0)
        self.assertEqual((ids, s.st["tower_ties"]), ([2], 1))
        self.assertEqual(self.sold("id_desc", 112.0, 100.0, 100.0)[1].st["tower_ties"], 0)
        self.assertEqual(self.sold("avg_desc", 112.0, 100.0, 100.0, tower_per_day=2)[1].st["tower_ties"], 0)
        self.assertEqual(R.verdict(row(.1, .1, 1.0, tower_ties=1), ""), "판정 보류(평단 동점)")

    def test_hold_is_default_and_locks(self):
        self.assertEqual(Params().remain, "hold")
        s = sim(Params(share_amount=1_000_000, protect_ratio=0.0, **FREE))
        f = hold_f(s, 1, 100, 10000.0)
        s.tower_sell(f, 11000.0)
        self.assertEqual((f.qty, f.locked, f.boxes), (50, True, []))

    def test_variants_and_tracks_are_separate(self):
        v = R.variants(Params())
        self.assertEqual(len({R.track(p) for p in v}), 4)
        self.assertEqual(len(R.variants(Params(protect_ratio=0.0))), 2)


class Fix10BucketsAndNumbers(unittest.TestCase):
    def dup(self, issuer):
        s = sim(P())
        a = hold_f(s, 1, 2, 100.0, done=False)
        b = hold_f(s, 2, 3, 100.0, done=False)
        a.boxes[0].id = b.boxes[0].id = 1
        s.next_box = issuer
        return s, a, b

    def test_merge_into_reissues_duplicate(self):
        for issuer, want in ((1, [1, 2]), (10, [1, 10])):   # Muse 원본 실행 A1·A2 와 같은 번호
            s, a, b = self.dup(issuer)
            s.merge_into(a, b)
            self.assertEqual([x.id for x in a.boxes], want)
            self.assertEqual((a.qty, a.cost), (5, 500.0))

    def test_day_end_merge_reissues_duplicate(self):
        s, a, b = self.dup(1)
        s.day_end()
        ids = [x.id for f in s.fs for x in f.boxes]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), 2)

    def test_merge_keeps_dst_id_then_settlement_renumbers(self):
        s = sim(Params(share_amount=20_000, tower_sell=20.0, remain="sniper", protect_ratio=0.0, **FREE))
        full = hold_f(s, 1, 10, 1000.0)
        hold_f(s, 2, 3, 1000.0, done=False)
        s.tower_sell(full, 1100.0)
        self.assertEqual([(f.id, f.qty, f.cost) for f in s.fs], [(2, 11, 11000.0)])
        s.day_end()
        self.assertEqual([(f.id, f.qty, f.cost) for f in s.fs], [(1, 11, 11000.0)])

    def test_settlement_numbers_by_avg_desc_and_4dp(self):
        s = sim(P())
        hold_f(s, 3, 10, 90.0); hold_f(s, 7, 10, 120.0, indep=True); hold_f(s, 9, 10, 80.0)
        s.day_end()
        self.assertEqual([(f.id, f.avg) for f in s.fs], [(1, 120.0), (2, 90.0), (3, 80.0)])
        s = sim(P())
        hold_f(s, 1, 10, 100.00004); hold_f(s, 2, 10, 100.00001)   # 4자리로는 같음 → 원본처럼 합병
        s.day_end()
        self.assertEqual(len(s.fs), 1)

    def test_ids_unique_on_random_paths(self):
        original = Sim.day_end
        seen = []

        def checked(s):
            original(s)
            ids = [b.id for f in s.fs for b in f.boxes]
            seen.append(len(ids) == len(set(ids)))
            assert [f.id for f in s.fs] == list(range(1, len(s.fs) + 1))
        Sim.day_end = checked
        try:
            for seed in range(8):
                for remain in ("hold", "sniper"):
                    simulate(prepare(walk(250, seed, swing=0.008)), P(remain=remain, tower_tp=3.0), ETF)
        finally:
            Sim.day_end = original
        self.assertTrue(seen and all(seen))


class Fix11Probe(unittest.TestCase):
    def test_browser_user_agent_is_sent(self):
        sent = []

        class Fake:
            def open(self, req, timeout=None):
                sent.append(req)
                raise RuntimeError("no network in test")
        original = urllib.request.build_opener
        urllib.request.build_opener = lambda *a: Fake()      # 실제 접속 없음
        try:
            with self.assertRaisesRegex(RuntimeError, "no network in test"):
                K.http_post(K.CHART_PATH, {}, {"api-id": "ka10080"})
        finally:
            urllib.request.build_opener = original
        self.assertIn("Chrome/120", sent[0].get_header("User-agent"))


class CliTracks(unittest.TestCase):
    def test_run_and_sweep_apply_rules_end_to_end(self):
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "T.csv"), "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=["date", "open", "high", "low", "close", "volume"])
                w.writeheader()
                for i, r in enumerate(walk(600, 11, swing=0.008)):
                    r["date"] = (dt.date(2024, 1, 1) + dt.timedelta(days=i)).isoformat()
                    w.writerow(r)
            cfg = dict(period=dict(start="2024-01-01", end="2025-12-31"), source="csv:" + os.path.join(td, "{ticker}.csv"),
                       symbols=[dict(ticker="T", name="합성", kind="etf_equity", price_limit=0.3)],
                       segments=[dict(name=y, start=f"{y}-01-01", end=f"{y}-12-31", type="auto") for y in ("2024", "2025")],
                       auto_up_threshold=0.15, params={},
                       sweep=dict(tower_order=["id_desc", "avg_desc"], protect_ratio=[0.0, 0.2], remain=["hold", "sniper"],
                                  tower_tp=[5.0, 10.0], portion_pct=[8.0, 10.0]))
            cp = os.path.join(td, "c.json")
            with open(cp, "w", encoding="utf-8") as f:
                json.dump(cfg, f)
            for cmd in ("run", "sweep"):
                with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                    R.main([cmd, "--config", cp, "--out", os.path.join(td, cmd), "--jobs", "1"])
            with open(os.path.join(td, "run", "segments.csv"), encoding="utf-8-sig") as f:
                got = {(r["tower_order"], r["protect_ratio"]) for r in csv.DictReader(f)}
            self.assertEqual(got, {(o, r) for o in ("id_desc", "avg_desc") for r in ("0.0", "0.2")})
            with open(os.path.join(td, "sweep", "sweep_all.csv"), encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len({r["track"] for r in rows}), 8)
            self.assertTrue(any(r["verdict"] == "제외(고갈)" for r in rows))
            for r in rows:
                self.assertEqual(bool(r["rank"]), r["verdict"] == "순위")
                if r["rank"]:
                    self.assertEqual((r["depletion"], r["late_depletion"]), ("0", "0"))


if __name__ == "__main__":
    unittest.main()
