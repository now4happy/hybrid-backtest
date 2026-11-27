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
from hbt_engine import Params, Symbol, prepare, simulate, metrics, buy_and_hold, required_share, fits_one_share

HERE = os.path.dirname(os.path.abspath(__file__))
NO_MINUTE = ("**분봉이 없는 구간은 일봉만으로 판단한다.** 일봉은 하루 안의 가격 순서를 모르고, "
             "10분 게이트·개장 쉴드·마감 전 신규차단·1분봉 필터를 적용하지 못한다.")
RULES = {"sell_rule": ("code", "fixed"), "remain": ("sniper", "hold"), "l2_solo": (False, True)}
TOWER_ORDERS = ("id_desc", "avg_desc")
ORDER_NAME = {"id_desc": "원본식 ID 내림차순", "avg_desc": "사용자 결정식 평단 높은 순"}
CAUSES = ("no_cash", "cash_protection", "unaffordable", "shares_blocked")
RANKED = "순위"


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
    cfg["share_basis"] = a.share_basis or cfg.get("share_basis", "fixed")
    if cfg["share_basis"] not in ("fixed", "first_bar"):
        raise SystemExit("share_basis=fixed|first_bar")
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
    """자본은 시험 시작 전에 알 수 있는 값(설정, 첫 봉 시가)으로만 정하고 모든 설정에 같은 몫을 씀.
    fixed(기본): 설정한 몫 그대로. 첫 봉 시가로 1주가 안 되는 설정은 실행 불가 (몫을 늘리지 않음).
    first_bar(직접 선택): 첫 봉 시가와 가장 작은 1회 비중으로 필요한 몫까지 공개적으로 늘림."""
    first = bars[0][3]
    base = plist[0].share_amount
    low = min(p.portion_pct for p in plist)
    need = required_share(base, first, low)
    share = need if cfg.get("share_basis") == "first_bar" else base
    out = [replace(p, share_amount=share) for p in plist]
    bad = sum(not fits_one_share(p, first) for p in out)
    note = (f"- {sym.name}: 첫 봉 시가 {first:,.0f}원, 가장 작은 1회 비중 {low:g}% · 자본 방식 {cfg.get('share_basis', 'fixed')} · "
            f"몫 {share:,.0f}원 × {out[0].max_shares}")
    if share != base:
        note += f" (설정값 {base:,.0f}원에서 첫 봉 기준으로 늘림)"
    if bad:
        note += (f" · **실행 불가 설정 {bad}/{len(out)}개** (1회 매수액 < 1주). 몫은 늘리지 않음. "
                 f"첫 봉 기준 필요 몫 {need:,.0f}원 (share_amount 로 지정하거나 --share-basis first_bar)")
    return out, note


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


HEAD = ["구간", "성과 기준", "분류", "수익률", "CAGR", "MDD", "칼마", "평균주식비중", "최장동결(일)", "보유 수익률", "보유 MDD", "보유 칼마"]


def summarize(res, hold, segs):
    """구간 행은 평가성과(청산 비용 차감 전)만. 전체 기간 행은 평가성과와 청산차감 둘 다."""
    spans = [(n, k, d, False) for n, k, d in segs]
    for kind in ("하락·박스", "상승"):
        days = set().union(*[d for _, k, d in segs if k == kind]) if any(k == kind for _, k, _ in segs) else set()
        if days:
            spans.append((f"{kind} 합산", kind, days, False))
    spans += [("전체", "전체", None, False), ("전체", "전체", None, True)]
    rows = []
    for name, kind, days, liq in spans:
        m, h = metrics(res, days, liq), metrics(hold, days, liq)
        rows.append([name, "청산차감" if liq else "평가(청산 전)", kind, pct(m["ret"]), pct(m["cagr"]), upct(m["mdd"]),
                     num(m["calmar"]), upct(m["exposure"]), m["freeze"], pct(h["ret"]), upct(h["mdd"]), num(h["calmar"])])
    return rows


def counts(r):
    return (f"매수 {r['buys']} / 스나이퍼 매도 {r['sells']} / 탑 매도 {r['tower_sells']} · F 생성 {r['f_created']}개 "
            f"(1년 넘게 보유 {r['f_over_1y']}개, 최장 {r['f_max_age']}일) · 최대 층 {r['max_layers']} · "
            f"추가 입금 {r['added']:,.0f}원 · **고갈 {r['depletion']}회** (원인: 현금 부족 {r['no_cash']}, 현금 보호 {r['cash_protection']}) · "
            f"고갈 아님: 1주 미달 {r['unaffordable']}회, 몫 소진 {r['shares_blocked']}회 · "
            f"같은 창의 뒤늦은 고갈 원인 {r['late_depletion']}회 · 탑 매도 평단 동점 {r['tower_ties']}회 · "
            f"미체결 지정가 {r['rest_orders'] - r['rest_filled']}/{r['rest_orders']} · 상하한가 차단 {r['limit_blocked']}회")


def verdict(r, pre):
    """순위에 넣을 수 있나. 순서: 실행 불가 → 고갈 → 판정 보류 3가지"""
    if not r["feasible"]:
        return "실행 불가"
    if r["depletion"] > 0:
        return "제외(고갈)"
    if r["late_depletion"] > 0:
        return "판정 보류(창 안 뒤늦은 고갈)"
    if r["tower_ties"] > 0:
        return "판정 보류(평단 동점)"
    if score(r, pre) is None:
        return "판정 보류(칼마 미산출)"
    return RANKED


def rank(rows, pre):
    """→ (순위에 든 index 목록, 설정별 판정)"""
    verdicts = [verdict(r, pre) for r in rows]
    order = sorted((i for i, v in enumerate(verdicts) if v == RANKED), key=lambda i: score(rows[i], pre), reverse=True)
    return order, verdicts


def protect_name(p):
    return "현금 보호 수정안" if p.protect_ratio > 0 else "보호 끔(부분 매수)"


def track(p):
    """서로 순위를 섞지 않는 비교 묶음"""
    return (p.remain, protect_name(p), p.tower_order)


def track_name(t):
    return f"잔여 F={t[0]} · {t[1]} · {ORDER_NAME[t[2]]}"


def variants(base):
    """설정 1개 실행: 보호 끔/수정안 × 탑 매도 두 식을 따로"""
    ratios = [0.0] + ([base.protect_ratio] if base.protect_ratio > 0 else [])
    return [replace(base, protect_ratio=r, tower_order=o) for r in ratios for o in TOWER_ORDERS]


def header(title, p, infos, notes):
    lines = [f"# {title}", "", NO_MINUTE, "", "CAGR 은 전체·구간 모두 선택된 거래일 수 / 245 로 환산한 근사값이다. 구간 행은 청산 비용 차감 전 평가성과다. 분봉 15:20 이후는 체결 평가에서 뺀다. 호가 단위는 반영하지 않았다.", "", "보호 끔 결과도 원본 봇을 완전히 재현한 것이 아니라 초안 방식이다. 평단 동점의 -id 는 임시안이며 동점이 걸린 설정은 판정 보류다.", "", "설정: `" + json.dumps(asdict(p), ensure_ascii=False) + "`", "",
             "## 시드", *notes, "", "## 데이터"]
    lines.append(table(["종목", "소스", "봉 수", "첫날", "끝날", "sha256"],
                       [[i["ticker"], i["source"], i["n"], i["first"], i["last"], i["sha256"][:12]] for i in infos]))
    return lines


def cmd_run(cfg, base, out):
    infos, notes, body, flat, trades = [], [], [], [], []
    for s in cfg["symbols"]:
        sym, bars, info = daily_bars(cfg, s)
        plist, note = sized(cfg, sym, bars, variants(base))
        infos.append(info); notes.append(note)
        body.append(f"## {sym.name} ({sym.ticker})")
        for p in plist:
            label = track_name(track(p)) + (f" · 보호 비율 {p.protect_ratio:g}" if p.protect_ratio else "")
            body.append(f"### {label}")
            if not fits_one_share(p, bars[0][3]):
                body += ["**실행 불가**: 첫 봉 시가 기준 1회 매수액이 1주보다 작음", ""]
                continue
            res = simulate(bars, p, sym, log=True)
            rows = summarize(res, buy_and_hold(bars, p, sym, res["start_cash"]), segments(cfg, bars, p, sym))
            paths = []
            for path in ("low_first", "high_first"):
                alt = simulate(bars, replace(p, path=path), sym)
                m, ml = metrics(alt), metrics(alt, liquidate=True)
                paths.append(f"{path}: 평가 {pct(m['ret'])} / 청산차감 {pct(ml['ret'])}, MDD {upct(m['mdd'])}, 고갈 {alt['depletion']}회")
            one = _row(res)
            body += [table(HEAD, rows), "", counts(res), f"전체(청산차감) 판정: {verdict(one, 'liq_')}",
                     "하루 안 가격 순서 가정에 따른 전체 결과: " + " · ".join(paths), ""]
            tag = dict(ticker=sym.ticker, remain=p.remain, tower_order=p.tower_order, protect_ratio=p.protect_ratio)
            flat += [dict(tag, **dict(zip(HEAD, r))) for r in rows]
            trades += [dict(tag, **dict(zip(["day", "minute", "type", "f", "qty", "price"], t))) for t in res["trades"]]
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


METRIC_KEYS = ("ret", "cagr", "mdd", "calmar")
COUNT_KEYS = ("buys", "sells", "tower_sells", "f_created", "f_over_1y", "added") + CAUSES + \
    ("depletion", "late_depletion", "tower_ties")


def _row(res, main_days=None, up_days=None):
    """결과 1행. 접두사 없음 = 전체 평가(청산 전), liq_ = 전체 청산차감, main_/up_ = 구간 평가"""
    full = metrics(res)
    parts = {"": full, "liq_": metrics(res, liquidate=True),
             "main_": metrics(res, main_days) if main_days else None,
             "up_": metrics(res, up_days) if up_days else None}
    row = {"feasible": True, "exposure": full["exposure"], "freeze": full["freeze"], "final": res["final"]}
    for pre, m in parts.items():
        for k in METRIC_KEYS:
            row[pre + k] = m[k] if m else None
    for k in COUNT_KEYS:
        row[k] = res[k]
    return row


def _one(p):
    first = _CTX.get("first_price") or _CTX["bars"][0][3]
    if not fits_one_share(p, first):
        keys = [pre + k for pre in ("", "liq_", "main_", "up_") for k in METRIC_KEYS]
        return dict({k: None for k in keys + ["exposure", "freeze", "final"] + list(COUNT_KEYS)}, feasible=False)
    res = simulate(_CTX["bars"], p, _CTX["sym"], sessions=_CTX.get("sessions"))
    return _row(res, _CTX.get("main_days"), _CTX.get("up_days"))


def run_many(ctx, plist, jobs):
    if jobs > 1 and len(plist) > 1:
        with Pool(jobs, initializer=_init, initargs=(ctx,)) as pool:
            return pool.map(_one, plist, chunksize=4)
    _CTX.clear()
    _init(ctx)
    return [_one(p) for p in plist]


def score(r, pre):
    """칼마 1순위. CAGR 0 이하는 칼마가 왜곡되므로 플러스 설정 뒤에 CAGR → MDD 순.
    칼마가 없으면 CAGR 부호와 무관하게 None (판정 보류: 순위에 넣지 않음)"""
    cagr, mdd, cal = r[pre + "cagr"], r[pre + "mdd"], r[pre + "calmar"]
    if cal is None:
        return None
    if cagr > 0:
        return (1, cal, cagr)
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
            if verdict(r, pre) != RANKED:             # 순위에 든 설정끼리만 비교
                continue
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


def excluded_summary(verdicts):
    c = {}
    for v in verdicts:
        if v != RANKED:
            c[v] = c.get(v, 0) + 1
    return ", ".join(f"{k} {n}개" for k, n in sorted(c.items())) or "없음"


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
        pre = "main_" if ctx["main_days"] else "liq_"
        head = ["순위"] + keys + ["기준 수익률", "CAGR", "MDD", "칼마", "평균주식비중", "상승 수익률", "상승 MDD",
                                  "전체 평가 수익률", "전체 청산차감 수익률", "전체 MDD(청산차감)", "F생성/1년초과", "거래(매수/매도/탑)",
                                  "고갈", "no_cash", "cash_protection", "unaffordable", "shares_blocked", "추가입금", "최장동결"]
        body += [f"## {sym.name} ({sym.ticker})",
                 "구간 분류(지나고 나서 본 것): " + ", ".join(f"{n}={k}" for n, k, _ in segs),
                 "순위 기준: " + ("하락·박스 합산 평가성과" if pre == "main_" else "전체 청산차감 (하락·박스 구간 없음)") + " 칼마", ""]
        groups = {}
        for i, p in enumerate(plist):
            groups.setdefault(track(p), []).append(i)
        for t, idx in sorted(groups.items()):
            local, verdicts = rank([rows[i] for i in idx], pre)
            order = [idx[j] for j in local]
            rank_of = {i: n for n, i in enumerate(order, 1)}
            lines = []
            for n, i in enumerate(order[:top], 1):
                r = rows[i]
                lines.append([n] + [show(plist[i], k) for k in keys] +
                             [pct(r[pre + "ret"]), pct(r[pre + "cagr"]), upct(r[pre + "mdd"]), num(r[pre + "calmar"]),
                              upct(r["exposure"]), pct(r["up_ret"]), upct(r["up_mdd"]), pct(r["ret"]), pct(r["liq_ret"]),
                              upct(r["liq_mdd"]), f"{r['f_created']}/{r['f_over_1y']}",
                              f"{r['buys']}/{r['sells']}/{r['tower_sells']}", r["depletion"]] +
                             [r[c] for c in CAUSES] + [f"{r['added']:,.0f}", r["freeze"]])
            done = [rows[i] for i in idx if rows[i]["feasible"]]
            missed = " · ".join(f"{c}>0 설정 {sum(r[c] > 0 for r in done)}개" for c in CAUSES)
            body += [f"### {track_name(t)}",
                     f"설정 {len(idx)}개 중 순위 {len(order)}개 · 순위 밖: {excluded_summary(verdicts)}",
                     f"놓친 신호가 있는 설정 수(실행한 {len(done)}개 중): {missed}", "",
                     table(head, lines) if lines else "**순위에 넣을 수 있는 설정 없음**", ""]
            for i, v in zip(idx, verdicts):
                flat.append(dict(ticker=sym.ticker, track=track_name(t), rank=rank_of.get(i, ""), verdict=v,
                                 **{k: show(plist[i], k) for k in keys}, **rows[i]))
        body += ["### 규칙 비교", rule_effects(keys, plist, rows, pre), ""]
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


ROWS = [("평가 수익률", "ret", pct), ("평가 CAGR", "cagr", pct), ("평가 MDD", "mdd", upct), ("평가 칼마", "calmar", num),
        ("청산차감 수익률", "liq_ret", pct), ("청산차감 CAGR", "liq_cagr", pct), ("청산차감 MDD", "liq_mdd", upct), ("청산차감 칼마", "liq_calmar", num),
        ("고갈", "depletion", str), ("현금 부족", "no_cash", str), ("현금 보호", "cash_protection", str),
        ("1주 미달", "unaffordable", str), ("몫 소진", "shares_blocked", str), ("탑 평단 동점", "tower_ties", str),
        ("평균주식비중", "exposure", upct), ("매수", "buys", str), ("스나이퍼 매도", "sells", str),
        ("탑 매도", "tower_sells", str), ("F 생성", "f_created", str), ("추가 입금(원)", "added", lambda v: "n/a" if v is None else f"{v:,.0f}"),
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
        vbase = variants(base)
        plist, note = sized(cfg, sym, mbars, vbase + grid_list)
        vlist, glist = plist[:len(vbase)], plist[len(vbase):]
        first = mbars[0][3]
        notes.append(note)
        infos.append(dict(ticker=sym.ticker, source="kiwoom 1m", n=info["bars"], first=info["first"], last=info["last"], sha256=info["sha256"]))
        body += [f"## {sym.name} ({sym.ticker}) · {info['first']} ~ {info['last']} · {info['days']}일",
                 f"- 정규장 밖이라 뺀 봉: {info['outside_session'] or '없음'}",
                 f"- 마감 전이라 뺀 마지막 날: {info['dropped_last_day'] or '없음'}",
                 f"- 개장이 늦어 보이는 날(장 시간 확인 필요): {', '.join(info['suspect_days']) or '없음'}"]
        if len(sources) == 3:
            body.append(f"- 비교용 일봉 날짜 수: {len(sources[2][1])} / 분봉 날짜 수: {info['days']}")
        # 같은 설정 비교 (일봉은 가격 순서 가정 3가지를 함께). 보호 × 탑 매도 식마다 표를 따로 냄
        cols = list(sources) + [("합성 일봉·저가 먼저", agg), ("합성 일봉·고가 먼저", agg)]
        for p in vlist:
            body += ["", f"### {track_name(track(p))}"]
            if not fits_one_share(p, first):
                body.append("**실행 불가**: 첫 봉 시가 기준 1회 매수액이 1주보다 작음")
                continue
            values = []
            for label, bars in cols:
                q = p
                if label.endswith("저가 먼저"): q = replace(p, path="low_first")
                if label.endswith("고가 먼저"): q = replace(p, path="high_first")
                _CTX.clear(); _init(dict(bars=bars, sym=sym, sessions=sess, first_price=first))
                v = _one(q)
                values.append(v)
                flat.append(dict(ticker=sym.ticker, config="base", track=track_name(track(p)), method=label,
                                 verdict=verdict(v, "liq_"), **v))
            res_hold = buy_and_hold(mbars, p, sym, p.share_amount * p.max_shares)
            hold, hold_liq = metrics(res_hold), metrics(res_hold, liquidate=True)
            body += [table(["항목"] + [c[0] for c in cols],
                           [[n] + [f(v[k]) for v in values] for n, k, f in ROWS] +
                           [["판정(청산차감)"] + [verdict(v, "liq_") for v in values]]),
                     f"\n단순 보유(같은 구간): 평가 {pct(hold['ret'])} / 청산차감 {pct(hold_liq['ret'])}, MDD {upct(hold['mdd'])}"]
        # 설정 여러 개: 일봉 순위가 분봉에서도 유지되는가 (묶음별)
        if glist:
            per = [run_many(dict(bars=bars, sym=sym, sessions=sess, first_price=first), glist, jobs) for _, bars in sources[:2]]
            mi, da = per
            groups = {}
            for i, g in enumerate(glist):
                groups.setdefault(track(g), []).append(i)
            for t, idx in sorted(groups.items()):
                ok = [i for i in idx if mi[i]["feasible"]]
                order_d, v_d = rank([da[i] for i in idx], "liq_")
                order_m, v_m = rank([mi[i] for i in idx], "liq_")
                top_d, top_m = [idx[j] for j in order_d], [idx[j] for j in order_m]
                k = min(10, len(top_d))
                both = len(top_m) > 10 and len(top_d) > 10
                body += ["", f"### 설정 비교 · {track_name(t)}: 일봉 순위를 믿을 수 있나",
                         f"- 설정 {len(idx)}개 · 순위에 든 수: 일봉 {len(top_d)} / 분봉 {len(top_m)}",
                         f"- 순위 밖(일봉): {excluded_summary(v_d)} · (분봉): {excluded_summary(v_m)}",
                         f"- 순위 상관(스피어만, 실행한 설정) 청산차감 수익률 {num(spearman([da[i]['liq_ret'] for i in ok], [mi[i]['liq_ret'] for i in ok]))}, "
                         f"MDD {num(spearman([da[i]['liq_mdd'] for i in ok], [mi[i]['liq_mdd'] for i in ok]))} (1 = 같은 순서, 0 = 무관)",
                         (f"- 일봉 칼마 상위 10개 중 분봉 상위 10개에도 든 것: {len(set(top_d[:10]) & set(top_m[:10]))}개"
                          if both else "- 순위에 든 설정이 10개 이하라 상위권 겹침은 계산하지 않음"), "",
                         table(["일봉 순위"] + keys + ["분봉 순위", "일봉 수익률", "분봉 수익률", "일봉 MDD", "분봉 MDD"],
                               [[a + 1] + [show(glist[i], x) for x in keys] +
                                [top_m.index(i) + 1 if i in top_m else v_m[idx.index(i)], pct(da[i]["liq_ret"]), pct(mi[i]["liq_ret"]),
                                 upct(da[i]["liq_mdd"]), upct(mi[i]["liq_mdd"])]
                                for a, i in enumerate(top_d[:k])]) if k else "**일봉에서 순위에 넣을 수 있는 설정 없음**"]
                for label, res, vs in (("분봉", mi, v_m), ("일봉 근사", da, v_d)):
                    flat += [dict(ticker=sym.ticker, config=json.dumps({x: show(glist[i], x) for x in keys}, ensure_ascii=False),
                                  track=track_name(t), method=label, verdict=v, **res[i]) for i, v in zip(idx, vs)]
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
    ap.add_argument("--share-basis", choices=["fixed", "first_bar"], help="fixed(기본): 몫을 늘리지 않음 | first_bar: 첫 봉 시가 기준으로 늘림")
    ap.add_argument("--out"); ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--minute-dir", default=os.path.join(HERE, "kiwoom_minute"))
    ap.add_argument("--compare-daily", default=None, help="비교할 외부 일봉 소스: yahoo | naver | csv:경로 | none")
    ap.add_argument("--no-grid", action="store_true", help="compare 에서 설정 여러 개 비교를 생략")
    a = ap.parse_args(argv)
    cfg, base = load_config(a)
    # remain=sniper 비교 실행이 기준(hold) 결과를 덮어쓰지 않도록 폴더를 나눔
    out = a.out or os.path.join(HERE, "results", a.command if a.command == "sweep" else f"{a.command}_{base.remain}")
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
