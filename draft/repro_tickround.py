# 호가 반올림 8개 값 재현. 저장소 루트에서: python3 draft/repro_tickround.py draft data/kiwoom_minute/229200_1m.csv
import sys, json, hashlib
draft, data = sys.argv[1], sys.argv[2]
sys.path.insert(0, draft)
import hbt_engine as E, hbt_minute
rows, info, sess = hbt_minute.load(data, {}, "2025-11-03", "2026-11-26")
bars = E.prepare(rows)
sym = E.Symbol("229200.KS", "KODEX 코스닥150", "etf_equity")
exp = {("etf","primary"):[(4.83,209,198,1),(-3.26,160,150,0)], ("etf","nearest"):[(0.90,209,203,0),(0.86,208,202,0)],
       ("stock","primary"):[(-0.32,189,183,0),(-1.30,180,174,0)], ("stock","nearest"):[(4.68,201,190,1),(4.68,201,190,1)]}
print("data", info["sha256"], info["bars"], info["first"], info["last"], info["days"])
ok = True
for (tab, mode), vals in exp.items():
    E.TICK_KIND["etf_equity"] = tab   # 주식표 대조는 호가표만 바꿈 (종목 kind·세금은 ETF 그대로)
    for slip, want in zip((0.0, 0.000025), vals):
        p = E.Params(sell_rule="fixed", fixed_tp=1.0, protect_ratio=0.0, remain="hold", tick_mode=mode, slippage=slip).check()
        r = E.simulate(bars, p, sym, log=True)
        m = E.metrics(r, liquidate=True)
        got = (round(m["ret"]*100, 2), r["buys"], r["sells"], r["tower_sells"])
        off = sum(1 for t in r["trades"] if t[5] % E.tick_size(t[5], "etf_equity"))
        match = got == want; ok &= match and off == 0
        print(tab, mode, slip, got, "ret=%.10f" % m["ret"], "offgrid=%d/%d" % (off, len(r["trades"])), "OK" if match else f"MISMATCH {want}")
print("ALL", ok)
