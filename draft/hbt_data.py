"""시세 수집·보정. 기본: 야후(KRX 정규장 원시가) + 깨진 봉·누락일만 네이버로 보정."""
import ast, csv, datetime as dt, hashlib, json, os, time, urllib.request

KST = dt.timezone(dt.timedelta(hours=9))
FIELDS = ["date", "open", "high", "low", "close", "volume", "src"]

def _get(url, tries=3):
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(3 * (k + 1))

def _epoch(d):
    return int(dt.datetime.fromisoformat(d).replace(tzinfo=KST).timestamp())

def fetch_yahoo(ticker, start, end):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?period1={_epoch(start)}"
           f"&period2={_epoch(end) + 86400}&interval=1d&events=div,split")
    res = json.loads(_get(url))["chart"]["result"][0]
    q, out = res["indicators"]["quote"][0], []
    for i, t in enumerate(res["timestamp"]):
        v = [q[k][i] for k in ("open", "high", "low", "close")]
        if any(x is None for x in v):
            continue
        d = dt.datetime.fromtimestamp(t, KST).date().isoformat()
        if start <= d <= end:
            out.append(dict(date=d, open=v[0], high=v[1], low=v[2], close=v[3],
                            volume=q["volume"][i] or 0, src="yahoo"))
    return out

def fetch_naver(ticker, start, end):
    code = ticker.split(".")[0]
    url = (f"https://api.finance.naver.com/siseJson.naver?symbol={code}&requestType=1"
           f"&startTime={start.replace('-', '')}&endTime={end.replace('-', '')}&timeframe=day")
    out = []
    for r in ast.literal_eval(_get(url).strip())[1:]:
        d = f"{r[0][:4]}-{r[0][4:6]}-{r[0][6:]}"
        out.append(dict(date=d, open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5], src="naver"))
    return out

def repair(yahoo, naver):
    """야후의 O=H=L=C 봉과 누락일만 네이버로 교체/추가.
    네이버 ETF는 수정주가라 수준이 달라, 직전 정상일의 야후/네이버 종가비로 환산한다."""
    y = {r["date"]: r for r in yahoo}
    out, ratio, fixed, added = [], 1.0, [], []
    for n in naver:
        d, a = n["date"], y.get(n["date"])
        broken = a is not None and a["open"] == a["high"] == a["low"] == a["close"] and n["high"] != n["low"]
        if a is None or broken:
            b = {k: (n[k] * ratio if k in ("open", "high", "low", "close") else n[k]) for k in n}
            b["src"] = "naver_scaled"
            out.append(b)
            (fixed if broken else added).append(d)
        else:
            out.append(a)
            if n["close"]:
                ratio = a["close"] / n["close"]
    have = {r["date"] for r in out}
    out += [r for r in yahoo if r["date"] not in have]
    out.sort(key=lambda r: r["date"])
    return out, fixed, added

def load(ticker, start, end, source="yahoo", data_dir="data", refresh=False):
    """반환: (rows, info). source = yahoo(보정 포함) | naver | csv:<경로>"""
    os.makedirs(data_dir, exist_ok=True)
    info = {"ticker": ticker, "source": source, "repaired": [], "added": []}
    if source.startswith("csv:"):
        path = source[4:].replace("{ticker}", ticker)
    else:
        path = os.path.join(data_dir, f"{ticker}_{source}_{start}_{end}.csv")
    if refresh or not os.path.exists(path):
        if source == "yahoo":
            rows, info["repaired"], info["added"] = repair(fetch_yahoo(ticker, start, end),
                                                           fetch_naver(ticker, start, end))
        elif source == "naver":
            rows = fetch_naver(ticker, start, end)
        else:
            raise FileNotFoundError(path)
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(rows)
        time.sleep(0.5)
    with open(path, "rb") as f:
        info["sha256"] = hashlib.sha256(f.read()).hexdigest()
    rows = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            for k in ("open", "high", "low", "close", "volume"):
                r[k] = float(r[k] or 0)
            if start <= r["date"][:10] <= end:
                rows.append(r)
    if not info["repaired"] and not info["added"]:
        info["repaired"] = [r["date"] for r in rows if r.get("src") == "naver_scaled"]
    info.update(n=len(rows), first=rows[0]["date"] if rows else None, last=rows[-1]["date"] if rows else None)
    return rows, info
