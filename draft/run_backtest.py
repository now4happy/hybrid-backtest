#!/usr/bin/env python3
"""하이브리드 매매 백테스트 실행기. 주문·서버 기능 없음.

  python run_backtest.py run      # 일봉 전체 기간, 설정 1개
  python run_backtest.py sweep    # 일봉 전체 기간, 설정 비교
  python run_backtest.py compare  # 분봉 구간: 분봉 vs 일봉 근사 (같은 구간·같은 설정)
"""
import argparse, csv, itertools, json, os, statistics, sys
from dataclasses import asdict, replace
from multiprocessing import Pool

import hbt_data, hbt_minute
from hbt_engine import Params, Symbol, prepare, simulate, metrics, buy_and_hold, required_share

HERE = os.path.dirname(os.path.abspath(__file__))
NO_MINUTE = ("**분봉이 없는 구간은 일봉만으로 판단한다.** 일봉은 하루 안의 가격 순서를 모르고, "
             "10분 게이트·개장 쉴드·마감 전 신규차단·1분봉 필터를 적용하지 못한다.")
RULES = {"sell_rule": ("code", "fixed"), "remain": ("sniper", "hold"), "l2_solo": (False, True)}


def pct(x):
    return "n/a" if x is None else f"{x * 100:+.1f}%"


def upct(x):
    return "n/a" if x is None else f"{x * 100:.1f}%"


def num(x):
    return "n/a" if x is None else f"{x:.2f}"


def table(head, rows):
    return "\n".join(["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] +
                     ["| " + " | ".join(str(c) for c in r) + " |" for r in rows])


def write_csv(path, rows):
    if rows:
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(dict.fromkeys(k for r in rows for k in r)))
            w.writeheader()
            w.writerows(rows)


def convert(key, v):
    cur = getattr(Params(), key)
    if key in ("pull_tiers", "trail_steps", "halts"):
        if isinstance(v, str):
            v = json.loads(v)
        return tuple(tuple(x) for x in v)
    if isinstance(cur, bool):
        return v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes")
    return type(cur)(v)


def load_config(a):
    with open(a.config, encoding="utf-8") as f:
        cfg = json.load(f)
    kw = {k: convert(k, v) for k, v in cfg.get("params", {}).items()}
    for item in a.set or []:
        k, v = item.split("=", 1)
        kw[k] = convert(k, v)
    if a.symbols:
        cfg["symbols"] = [s for s in cfg["symbols"] if s["ticker"] in a.symbols]
        if not cfg["symbols"]:
            raise SystemExit("--symbols 가 설정 파일의 종목과 맞지 않음")
    cfg["period"]["start"] = a.start or cfg["period"]["start"]
    cfg["period"]["end"] = a.end or cfg["period"]["end"]
    cfg["source"] = a.source or cfg["source"]
    return cfg, Params(**kw).check()


def grid_params(base, grid):
    """조합 생성. 그 설정에서 쓰이지 않는 값만 다른 조합은 하나로 합침"""
    keys = list(grid)
    seen = {}
    for combo in itertools.product(*(grid[k] for k in keys)):
        kw = {k: convert(k, v) for k, v in zip(keys, combo)}
        p = replace(base, **kw)
        if p.sell_rule == "fixed":
            p = replace(p, trail_ignite=base.trail_ignite, trail_cap=base.trail_cap, upper_tp=base.upper_tp, l2_solo=False)
        else:
            p = replace(p, fixed_tp=base.fixed_tp)
        seen.setdefault(p.check(), None)
    return keys, list(seen)


def sized(cfg, sym, bars, plist):
    """1회 매수액이 1주보다 작으면 이 종목의 몫을 늘림. 설정끼리 같은 자본이 되도록 가장 작은 1회 비중 기준"""
    top = max(b[4] for b in bars)
    base = plist[0].share_amount
    share = required_share(base, top, min(p.portion_pct for p in plist))
    low = min(p.portion_pct for p in plist)
    note = (f"- {sym.name}: 기간 최고가 {top:,.0f}원, 가장 작은 1회 비중 {low:g}% → "
            + (f"기본 몫 {base:,.0f}원으로 백테스트 가능" if share == base else
               f"**몫을 {share:,.0f}원으로 늘림** (총 {share * plist[0].max_shares:,.0f}원 이상이어야 이 종목을 백테스트할 수 있음)")
            + ". 최고가는 지나고 나서 안 값이다.")
    return [replace(p, share_amount=share) for p in plist], note


def daily_bars(cfg, s):
    sym = Symbol(**s)
    rows, info = hbt_data.load(sym.ticker, cfg["period"]["start"], cfg["period"]["end"], cfg["source"],
                               os.path.join(HERE, "data"))
    if len(rows) < 2:
        raise SystemExit(f"{sym.ticker}: 일봉 데이터 부족")
    return sym, prepare(rows), info


def segments(cfg, bars, p, sym):
    """연도 구간. 분류(상승/하락·박스)는 지나고 나서 본 결과이며 매매에는 쓰지 않는다"""
    out = []
    for seg in cfg["segments"]:
        sub = [b for b in bars if seg["start"] <= b[1] <= seg["end"]]
        if len(sub) < 20:
            continue
        kind = seg.get("type", "auto")
        if kind == "auto":
            kind = "상승" if sub[-1][6] / sub[0][3] - 1 >= cfg["auto_up_threshold"] else "하락·박스"
        out.append((seg["name"], kind, {b[1] for b in sub}))
    return out


HEAD = ["구간", "분류", "수익률", "CAGR", "MDD", "칼마", "평균주식비중", "최장동결(일)", "보유 수익률", "보유 MDD", "보유 칼마"]


def summarize(res, hold, segs):
    spans = [(n, k, d) for n, k, d in segs]
    for kind in ("하락·박스", "상승"):
        days = set().union(*[d for _, k, d in segs if k == kind]) if any(k == kind for _, k, _ in segs) else set()
        if days:
            spans.append((f"{kind} 합산", kind, days))
    spans.append(("전체", "전체", None))
    rows = []
    for name, kind, days in spans:
        m, h = metrics(res, days), metrics(hold, days)
        rows.append([name, kind, pct(m["ret"]), pct(m["cagr"]), upct(m["mdd"]), num(m["calmar"]), upct(m["exposure"]),
                     m["freeze"], pct(h["ret"]), upct(h["mdd"]), num(h["calmar"])])
    return rows


def counts(r):
    return (f"매수 {r['buys']} / 스나이퍼 매도 {r['sells']} / 탑 매도 {r['tower_sells']} · F 생성 {r['f_created']}개 "
            f"(1년 넘게 보유 {r['f_over_1y']}개, 최장 {r['f_max_age']}일) · 최대 층 {r['max_layers']} · "
            f"추가 입금 {r['added']:,.0f}원 · 몫 소진으로 못 삼 {r['shares_blocked']}회 · 현금 부족 {r['no_cash']}회 · "
            f"1주 미달 {r['unaffordable']}회 · 미체결 지정가 {r['rest_orders'] - r['rest_filled']}/{r['rest_orders']} · "
            f"상하한가 차단 {r['limit_blocked']}회")


def header(title, p, infos, notes):
    lines = [f"# {title}", "", NO_MINUTE, "", "구간을 합쳐 낼 때 CAGR 은 1년 = 245거래일로 환산한 근사값이다. 호가 단위는 반영하지 않았다.", "", "설정: `" + json.dumps(asdict(p), ensure_ascii=False) + "`", "",
             "## 시드", *notes, "", "## 데이터"]
    lines.append(table(["종목", "소스", "봉 수", "첫날", "끝날", "sha256"],
                       [[i["ticker"], i["source"], i["n"], i["first"], i["last"], i["sha256"][:12]] for i in infos]))
    return lines


def cmd_run(cfg, base, out):
    infos, notes, body, flat, trades = [], [], [], [], []
    for s in cfg["symbols"]:
        sym, bars, info = daily_bars(cfg, s)
        (p,), note = sized(cfg, sym, bars, [base])
        infos.append(info); notes.append(note)
        res = simulate(bars, p, sym, log=True)
        rows = summarize(res, buy_and_hold(bars, p, sym, res["start_cash"]), segments(cfg, bars, p, sym))
        paths = []
        for path in ("low_first", "high_first"):
            m = metrics(simulate(bars, replace(p, path=path), sym))
            paths.append(f"{path}: 수익률 {pct(m['ret'])}, MDD {upct(m['mdd'])}")
        body += [f"## {sym.name} ({sym.ticker})", table(HEAD, rows), "", counts(res),
                 "하루 안 가격 순서 가정에 따른 전체 결과: " + " · ".join(paths), ""]
        flat += [dict(ticker=sym.ticker, **dict(zip(HEAD, r))) for r in rows]
        trades += [dict(zip(["ticker", "day", "minute", "type", "f", "qty", "price"], (sym.ticker,) + t)) for t in res["trades"]]
    text = "\n".join(header("일봉 백테스트 (설정 1개)", base, infos, notes) + [""] + body)
    save(out, "report.md", text)
    write_csv(os.path.join(out, "segments.csv"), flat)
    write_csv(os.path.join(out, "trades.csv"), trades)


def save(out, name, text):
    with open(os.path.join(out, name), "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print(os.path.join(out, name))


_CTX = {}


def _init(ctx):
    _CTX.update(ctx)


def _one(p):
    res = simulate(_CTX["bars"], p, _CTX["sym"], sessions=_CTX.get("sessions"))
    full = metrics(res)
    main = metrics(res, _CTX["main_days"]) if _CTX.get("main_days") else None
    up = metrics(res, _CTX["up_days"]) if _CTX.get("up_days") else None
    row = {"ret": full["ret"], "cagr": full["cagr"], "mdd": full["mdd"], "calmar": full["calmar"],
           "exposure": full["exposure"], "freeze": full["freeze"], "final": res["final"]}
    for k in ("buys", "sells", "tower_sells", "f_created", "f_over_1y", "added", "unaffordable", "shares_blocked"):
        row[k] = res[k]
    for pre, m in (("main", main), ("up", up)):
        for k in ("ret", "cagr", "mdd", "calmar"):
            row[f"{pre}_{k}"] = m[k] if m else None
    return row


def run_many(ctx, plist, jobs):
    if jobs > 1 and len(plist) > 1:
        with Pool(jobs, initializer=_init, initargs=(ctx,)) as pool:
            return pool.map(_one, plist, chunksize=4)
    _CTX.clear()
    _init(ctx)
    return [_one(p) for p in plist]


def score(r, pre):
    """칼마 1순위. CAGR 0 이하는 칼마가 왜곡되므로 플러스 설정 뒤에 CAGR → MDD 순"""
    cagr, mdd, cal = r[pre + "cagr"], r[pre + "mdd"], r[pre + "calmar"]
    if cagr > 0:
        return (1, cal if cal is not None else float("inf"), cagr)
    return (0, cagr, -mdd)


def show(p, k):
    v = getattr(p, k)
    return f"{v / 10000:,.0f}만" if k == "share_amount" else str(v)


def rule_effects(keys, plist, rows, pre):
    """규칙 하나만 다른 설정끼리 짝지어 비교"""
    out = []
    for key, (a, b) in RULES.items():
        if key not in keys:
            continue
        def sig(p):
            d = asdict(p)
            for drop in (key,) + (("trail_ignite", "trail_cap", "upper_tp", "l2_solo", "fixed_tp") if key == "sell_rule" else ()):
                d.pop(drop, None)
            return json.dumps(d, sort_keys=True)
        left, right = {}, {}
        for p, r in zip(plist, rows):
            (left if getattr(p, key) == a else right).setdefault(sig(p), []).append(r)
        pairs = []
        for s in set(left) & set(right):
            best = lambda rs: max(rs, key=lambda r: score(r, pre))
            pairs.append((best(left[s]), best(right[s])))
        if not pairs:
            continue
        wins = sum(score(y, pre) > score(x, pre) for x, y in pairs)
        ties = sum(score(y, pre) == score(x, pre) for x, y in pairs)
        out.append([key, f"{a} → {b}", len(pairs), wins, ties, len(pairs) - wins - ties,
                    pct(statistics.median(y[pre + "ret"] - x[pre + "ret"] for x, y in pairs)),
                    pct(statistics.median(y[pre + "mdd"] - x[pre + "mdd"] for x, y in pairs))])
    return table(["규칙", "변경", "짝 수", "바꾼 쪽 우세", "같음", "열세", "수익률 차(중앙값)", "MDD 차(중앙값)"], out)


def cmd_sweep(cfg, base, out, grid, jobs, top):
    keys, plist0 = grid_params(base, grid)
    infos, notes, body, flat = [], [], [], []
    for s in cfg["symbols"]:
        sym, bars, info = daily_bars(cfg, s)
        plist, note = sized(cfg, sym, bars, plist0)
        infos.append(info); notes.append(note)
        segs = segments(cfg, bars, base, sym)
        ctx = dict(bars=bars, sym=sym,
                   main_days=set().union(set(), *[d for _, k, d in segs if k == "하락·박스"]),
                   up_days=set().union(set(), *[d for _, k, d in segs if k == "상승"]))
        rows = run_many(ctx, plist, jobs)
        pre = "main_" if ctx["main_days"] else ""
        order = sorted(range(len(rows)), key=lambda i: score(rows[i], pre), reverse=True)
        head = ["순위"] + keys + ["기준 수익률", "CAGR", "MDD", "칼마", "평균주식비중", "상승 수익률", "상승 MDD",
                                  "전체 수익률", "전체 MDD", "F생성/1년초과", "거래(매수/매도/탑)", "추가입금", "최장동결"]
        lines = []
        for n, i in enumerate(order[:top], 1):
            r = rows[i]
            lines.append([n] + [show(plist[i], k) for k in keys] +
                         [pct(r[pre + "ret"]), pct(r[pre + "cagr"]), upct(r[pre + "mdd"]), num(r[pre + "calmar"]),
                          upct(r["exposure"]), pct(r["up_ret"]), upct(r["up_mdd"]), pct(r["ret"]), upct(r["mdd"]),
                          f"{r['f_created']}/{r['f_over_1y']}", f"{r['buys']}/{r['sells']}/{r['tower_sells']}",
                          f"{r['added']:,.0f}", r["freeze"]])
        for n, i in enumerate(order, 1):
            flat.append(dict(ticker=sym.ticker, rank=n, **{k: show(plist[i], k) for k in keys}, **rows[i]))
        body += [f"## {sym.name} ({sym.ticker})",
                 "구간 분류(지나고 나서 본 것): " + ", ".join(f"{n}={k}" for n, k, _ in segs),
                 "순위 기준: " + ("하락·박스 합산" if pre else "전체 (하락·박스 구간 없음)") + " 칼마", "",
                 table(head, lines), "", "### 규칙 비교", rule_effects(keys, plist, rows, pre), ""]
        print(f"{sym.ticker}: {len(plist)}개 완료", file=sys.stderr)
    text = header(f"일봉 설정 비교 ({len(plist0)}개)", base, infos, notes)
    text += ["", "같은 과거 자료에서 여러 설정 중 1등을 고른 것이라 과최적화 가능성이 있다. 선택은 사용자가 한다.", ""] + body
    save(out, "sweep_report.md", "\n".join(text))
    write_csv(os.path.join(out, "sweep_all.csv"), flat)


def ranks(values):
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j) / 2
        i = j + 1
    return out


def spearman(a, b):
    if len(a) < 3:
        return None
    x, y = ranks(a), ranks(b)
    mx, my = sum(x) / len(x), sum(y) / len(y)
    den = (sum((v - mx) ** 2 for v in x) * sum((v - my) ** 2 for v in y)) ** 0.5
    return sum((u - mx) * (v - my) for u, v in zip(x, y)) / den if den else None


ROWS = [("수익률", "ret", pct), ("CAGR", "cagr", pct), ("MDD", "mdd", upct), ("칼마", "calmar", num),
        ("평균주식비중", "exposure", upct), ("매수", "buys", str), ("스나이퍼 매도", "sells", str),
        ("탑 매도", "tower_sells", str), ("F 생성", "f_created", str), ("추가 입금(원)", "added", lambda v: f"{v:,.0f}"),
        ("최장동결(일)", "freeze", str)]


def cmd_compare(cfg, base, out, minute_dir, grid, jobs, daily_source):
    m_cfg = cfg["minute"]
    keys, grid_list = grid_params(base, grid) if grid else ([], [])
    infos, notes, body, flat = [], [], [], []
    for s in cfg["symbols"]:
        sym = Symbol(**s)
        path = os.path.join(minute_dir, m_cfg["file"].format(code=sym.ticker.split(".")[0]))
        if not os.path.exists(path):
            body += [f"## {sym.name}", f"분봉 파일 없음: {os.path.basename(path)} → 이 종목은 일봉만으로 판단", ""]
            continue
        rows, info, sess = hbt_minute.load(path, m_cfg.get("sessions"), m_cfg.get("since", ""), cfg["period"]["end"])
        if info["days"] < 2:
            raise SystemExit(f"{sym.ticker}: 분봉 데이터 부족")
        mbars, agg = prepare(rows), prepare(hbt_minute.to_daily(rows))
        sources = [("분봉", mbars), ("일봉 근사(분봉 합성)", agg)]
        if daily_source != "none":
            days = {b[1] for b in agg}
            ext, dinfo = hbt_data.load(sym.ticker, info["first"], info["last"], daily_source, os.path.join(HERE, "data"))
            ext = [r for r in ext if r["date"][:10] in days]
            if len(ext) >= 2:
                sources.append((f"일봉 근사({daily_source.split(':')[0]})", prepare(ext)))
        plist, note = sized(cfg, sym, mbars, [base] + grid_list)
        p, glist = plist[0], plist[1:]
        notes.append(note)
        infos.append(dict(ticker=sym.ticker, source="kiwoom 1m", n=info["bars"], first=info["first"], last=info["last"], sha256=info["sha256"]))
        body += [f"## {sym.name} ({sym.ticker}) · {info['first']} ~ {info['last']} · {info['days']}일",
                 f"- 정규장 밖이라 뺀 봉: {info['outside_session'] or '없음'}",
                 f"- 마감 전이라 뺀 마지막 날: {info['dropped_last_day'] or '없음'}",
                 f"- 개장이 늦어 보이는 날(장 시간 확인 필요): {', '.join(info['suspect_days']) or '없음'}"]
        if len(sources) == 3:
            body.append(f"- 비교용 일봉 날짜 수: {len(sources[2][1])} / 분봉 날짜 수: {info['days']}")
        # 같은 설정 비교 (일봉은 가격 순서 가정 3가지를 함께)
        cols = list(sources) + [("합성 일봉·저가 먼저", agg), ("합성 일봉·고가 먼저", agg)]
        values = []
        for i, (label, bars) in enumerate(cols):
            q = p
            if label.endswith("저가 먼저"): q = replace(p, path="low_first")
            if label.endswith("고가 먼저"): q = replace(p, path="high_first")
            _CTX.clear(); _init(dict(bars=bars, sym=sym, sessions=sess))
            v = _one(q)
            values.append(v)
            flat.append(dict(ticker=sym.ticker, config="base", method=label, **v))
        hold = metrics(buy_and_hold(mbars, p, sym, p.share_amount * p.max_shares))
        body += ["", table(["항목"] + [c[0] for c in cols], [[n] + [f(v[k]) for v in values] for n, k, f in ROWS]),
                 f"\n단순 보유(같은 구간): 수익률 {pct(hold['ret'])}, MDD {upct(hold['mdd'])}"]
        # 설정 여러 개: 일봉 순위가 분봉에서도 유지되는가
        if glist:
            per = [run_many(dict(bars=bars, sym=sym, sessions=sess), glist, jobs) for _, bars in sources[:2]]
            mi, da = per
            n = len(glist)
            top_d = sorted(range(n), key=lambda i: score(da[i], ""), reverse=True)
            top_m = sorted(range(n), key=lambda i: score(mi[i], ""), reverse=True)
            k = min(10, n)
            body += ["", f"### 설정 {n}개: 일봉 순위를 믿을 수 있나",
                     f"- 순위 상관(스피어만) 수익률 {num(spearman([r['ret'] for r in da], [r['ret'] for r in mi]))}, "
                     f"MDD {num(spearman([r['mdd'] for r in da], [r['mdd'] for r in mi]))} (1 = 같은 순서, 0 = 무관)",
                     (f"- 일봉 칼마 상위 {k}개 중 분봉 상위 {k}개에도 든 것: {len(set(top_d[:k]) & set(top_m[:k]))}개"
                      if n > k else "- 설정이 10개 이하라 상위권 겹침은 계산하지 않음"), "",
                     table(["일봉 순위"] + keys + ["분봉 순위", "일봉 수익률", "분봉 수익률", "일봉 MDD", "분봉 MDD"],
                           [[a + 1] + [show(glist[i], x) for x in keys] +
                            [top_m.index(i) + 1, pct(da[i]["ret"]), pct(mi[i]["ret"]), upct(da[i]["mdd"]), upct(mi[i]["mdd"])]
                            for a, i in enumerate(top_d[:k])])]
            for label, res in (("분봉", mi), ("일봉 근사", da)):
                flat += [dict(ticker=sym.ticker, config=json.dumps({x: show(g, x) for x in keys}, ensure_ascii=False), method=label, **r)
                         for g, r in zip(glist, res)]
        body.append("")
    text = header("분봉 vs 일봉 근사 (같은 구간·같은 설정)", base, infos, notes)
    text += ["", "분봉 구간은 1년 남짓이라 CAGR·칼마는 한 시장 국면의 값이다. 분봉도 1분 안의 가격 순서는 가정이다.", ""] + body
    save(out, "compare_report.md", "\n".join(text))
    write_csv(os.path.join(out, "compare_all.csv"), flat)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["run", "sweep", "compare"])
    ap.add_argument("--config", default=os.path.join(HERE, "config_default.json"))
    ap.add_argument("--set", nargs="*", help="설정 덮어쓰기 key=value")
    ap.add_argument("--symbols", nargs="*")
    ap.add_argument("--start"); ap.add_argument("--end"); ap.add_argument("--source")
    ap.add_argument("--out"); ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--minute-dir", default=os.path.join(HERE, "kiwoom_minute"))
    ap.add_argument("--compare-daily", default=None, help="비교할 외부 일봉 소스: yahoo | naver | csv:경로 | none")
    ap.add_argument("--no-grid", action="store_true", help="compare 에서 설정 여러 개 비교를 생략")
    a = ap.parse_args(argv)
    cfg, base = load_config(a)
    out = a.out or os.path.join(HERE, "results", a.command)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "run_config.json"), "w", encoding="utf-8") as f:
        json.dump(dict(config=cfg, params=asdict(base)), f, ensure_ascii=False, indent=1)
    if a.command == "run":
        cmd_run(cfg, base, out)
    elif a.command == "sweep":
        cmd_sweep(cfg, base, out, cfg["sweep"], a.jobs, a.top)
    else:
        cmd_compare(cfg, base, out, a.minute_dir, None if a.no_grid else cfg["sweep"], a.jobs,
                    a.compare_daily or cfg["source"])


if __name__ == "__main__":
    main()
