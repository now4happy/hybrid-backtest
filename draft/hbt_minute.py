"""키움 분봉 CSV(kiwoom_minute_probe.py 출력: time,open,high,low,close,volume) 읽기."""
import collections
import csv
import hashlib

OPEN, CLOSE = 9 * 60, 15 * 60 + 30


def hm(text):
    return int(text[:2]) * 60 + int(text[-2:])


def load(path, sessions=None, since="", until=""):
    """sessions: {'YYYY-MM-DD': ('10:00','16:30')} 장 시간이 다른 날.
    정규장 밖 봉은 버리고 개수를 보고한다. 마지막 날에 마감 봉이 없으면(장중에 받은 것) 그 날을 뺀다."""
    sess = {d: (hm(a), hm(b)) for d, (a, b) in (sessions or {}).items()}
    days = collections.defaultdict(dict)
    outside = collections.Counter()
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            t = r["time"].strip()
            day = f"{t[:4]}-{t[4:6]}-{t[6:8]}"
            if (since and day < since) or (until and day > until):
                continue
            minute = int(t[8:10]) * 60 + int(t[10:12])
            a, b = sess.get(day, (OPEN, CLOSE))
            if not a <= minute <= b:
                outside[f"{t[8:10]}시"] += 1
                continue
            row = {k: float(r[k]) for k in ("open", "high", "low", "close", "volume")}
            row["date"] = f"{day} {t[8:10]}:{t[10:12]}"
            days[day][minute] = row
    info = {"outside_session": dict(sorted(outside.items())), "dropped_last_day": "", "suspect_days": []}
    ordered = sorted(days)
    if ordered:
        last = ordered[-1]
        if max(days[last]) < sess.get(last, (OPEN, CLOSE))[1]:
            info["dropped_last_day"] = last
            ordered.pop()
    for d in ordered:
        a = sess.get(d, (OPEN, CLOSE))[0]
        if min(days[d]) >= a + 30:
            info["suspect_days"].append(d)       # 개장이 늦은 날일 수 있음 → sessions 설정으로 지정
    rows = [days[d][m] for d in ordered for m in sorted(days[d])]
    with open(path, "rb") as f:
        info["sha256"] = hashlib.sha256(f.read()).hexdigest()
    info.update(bars=len(rows), days=len(ordered), first=ordered[0] if ordered else "", last=ordered[-1] if ordered else "")
    return rows, info, sess


def to_daily(rows):
    """분봉을 일봉으로 합침: 같은 자료로 만든 일봉이라 '일봉 근사' 오차만 남는다"""
    out = []
    for r in rows:
        d = r["date"][:10]
        if not out or out[-1]["date"] != d:
            out.append(dict(r, date=d))
        else:
            x = out[-1]
            x["high"] = max(x["high"], r["high"])
            x["low"] = min(x["low"], r["low"])
            x["close"] = r["close"]
            x["volume"] += r["volume"]
    return out
