"""하이브리드 매매 백테스트 엔진. 기준 = meritz_laoer v1.5.3 매매 규칙을 한국장·원화로 옮긴 것. 주문 기능 없음.

가격 경로: 봉 하나를 시가 → (저가, 고가) → 종가 4점으로 본다. 봉 시작은 점프(갭), 봉 안의 3구간은 연속이라고 가정.
분봉과 일봉은 같은 엔진을 쓰고, 일봉에는 시각 조건(10분 게이트·개장 쉴드·마감 전 신규차단·1분봉 필터)을 적용할 수 없다.
"""
import datetime as dt
import math
from dataclasses import dataclass, field

from hbt_rules import Box, Trail, tag_on_buy, tiers

STOCK_SELL_TAX = {2022: .0023, 2023: .0020, 2024: .0018, 2025: .0015, 2026: .0020}


@dataclass(frozen=True)
class Params:
    share_amount: float = 20_000_000          # 몫 = F 하나의 목표 금액
    max_shares: int = 3
    after_shares: str = "stop"                # stop | add (몫을 추가 입금)
    portion_pct: float = 10.0                 # 1회 매수 = 몫의 %
    pull_tiers: tuple = ((4, -1.0), (8, -2.0), (10, -3.5))
    pull_deep: float = -5.5
    pull_scale: float = 1.0
    sell_rule: str = "code"                   # code(트레일링+2층 동반+3층↑ 졸업) | fixed(매수 건마다 +N%)
    fixed_tp: float = 1.0
    trail_ignite: float = 1.0
    trail_steps: tuple = ((1.35, 1.15), (2.0, 1.65), (3.0, 2.60), (4.0, 3.55), (5.0, 4.50))
    trail_cap: float = 6.0
    upper_tp: float = 1.0                     # 2층·3층↑ 졸업 %
    l2_solo: bool = False                     # 1층 공석일 때 2층 단독 매도 허용
    tower_tp: float = 10.0
    tower_sell: float = 50.0
    tower_per_day: int = 1
    remain: str = "sniper"                    # sniper(코드) | hold(남은 주식 계속 보유)
    first_buy_filter: bool = True             # 분봉 전용
    gate_min: int = 10                        # 분봉 전용: 추가매수는 이 구간당 1번 (0 = 끔)
    open_shield_min: int = 7                  # 분봉 전용: 개장 직후 매수 금지 분
    late_cutoff_min: int = 60                 # 분봉 전용: 마감 전 이 분 동안 신규 진입 금지
    path: str = "auto"                        # auto(양봉: 저→고, 음봉: 고→저) | low_first | high_first
    fill_tol_pct: float = 0.6667              # F 완성 허용 오차 (코드 $100 / $15,000)
    buy_fee: float = 0.00015
    sell_fee: float = 0.00015
    slippage: float = 0.0
    sell_tax_override: float = -1.0
    halts: tuple = ()                         # ((시작, 끝, stop_buy|stop_all), ...) 사용자가 판단해 넣는 중지 기간

    def check(self):
        bad = []
        if not 0 < self.portion_pct < 100: bad.append("portion_pct 는 0~100 미만 (1회 매수액 < F 금액)")
        if self.share_amount <= 0 or self.max_shares < 1: bad.append("몫 금액·몫 수")
        if self.after_shares not in ("stop", "add"): bad.append("after_shares=stop|add")
        if self.sell_rule not in ("code", "fixed"): bad.append("sell_rule=code|fixed")
        if self.remain not in ("sniper", "hold"): bad.append("remain=sniper|hold")
        if self.path not in ("auto", "low_first", "high_first"): bad.append("path")
        if not 0 < self.tower_sell <= 100 or self.tower_tp <= 0 or self.tower_per_day < 1: bad.append("탑 매도 설정")
        if not (0 < self.trail_ignite < self.trail_cap) or not self.trail_steps: bad.append("트레일링 설정")
        if any(lock >= trig for trig, lock in self.trail_steps): bad.append("보존선은 발동 % 보다 낮아야 함")
        if self.fixed_tp <= 0 or self.upper_tp <= 0 or self.pull_scale <= 0: bad.append("익절·눌림 설정")
        if any(h[2] not in ("stop_buy", "stop_all") for h in self.halts): bad.append("halts mode")
        if bad:
            raise ValueError("설정 오류: " + "; ".join(bad))
        return self


@dataclass(frozen=True)
class Symbol:
    ticker: str
    name: str
    kind: str                                 # stock | etf_equity | etf_other
    price_limit: float = 0.30


@dataclass
class F:
    id: int
    target: float
    qty: int = 0
    cost: float = 0.0
    boxes: list = field(default_factory=list)  # 미완성일 때만
    done: bool = False
    locked: bool = False
    since: str = ""                            # 처음 완성된 날
    indep: bool = False                        # 분리해 만든 F: 합병 안 함
    keep_once: bool = False                    # 다음 일일 정리 한 번은 합병 제외

    @property
    def avg(self):
        return self.cost / self.qty

    @property
    def tower(self):
        return self.done or self.locked


def prepare(rows, daily_closes=None):
    """rows: {date:'YYYY-MM-DD[ HH:MM]', open, high, low, close, volume}.
    → (date, day, minute|None, o, h, l, c, v, prev_day_close, ha_ok)"""
    out, prev_day_close, last_close, last_day = [], None, None, None
    ha_open = None
    prev_ha_close = None
    flags = []                                 # 직전까지 완결된 1분봉으로 본 '첫 매수 가능'
    ok_prev = False
    for i, r in enumerate(rows):
        o, h, l, c = (float(r[k]) for k in ("open", "high", "low", "close"))
        day = r["date"][:10]
        minute = None
        if len(r["date"]) > 10:
            minute = int(r["date"][11:13]) * 60 + int(r["date"][14:16])
        if day != last_day:
            prev_day_close = last_close if last_close is not None else r.get("prev_close")
            last_day = day
        flags.append(ok_prev)
        hc = (o + h + l + c) / 4
        seed = (o + c) / 2 if prev_ha_close is None else prev_ha_close
        ha_open = seed if ha_open is None else 0.5 * seed + 0.5 * ha_open
        prev_ha_close = hc
        shaved_bear = hc < ha_open - 1e-9 and h <= ha_open + 1e-6
        ok_prev = i >= 14 and not shaved_bear
        out.append((r["date"], day, minute, o, h, l, c, float(r.get("volume") or 0), prev_day_close, flags[i]))
        last_close = c
    return out


def sell_tax(sym, day, p):
    if sym.kind != "stock":
        return 0.0
    if p.sell_tax_override >= 0:
        return p.sell_tax_override
    return STOCK_SELL_TAX.get(int(day[:4]), STOCK_SELL_TAX[2026])


class Sim:
    def __init__(self, p, sym, log=False, sessions=None):
        self.p, self.sym = p, sym
        self.sessions = sessions or {}
        self.start_cash = p.share_amount * p.max_shares
        self.cash = self.start_cash
        self.fs = []
        self.next_f, self.next_box = 1, 1
        self.anchor, self.peak_layers = 0.0, 0
        self.trail = Trail(p.trail_ignite, p.trail_steps, p.trail_cap)
        self.resting = []                      # 당일 지정가 매도: (limit, f, box)
        self.log = [] if log else None
        self.st = dict(buys=0, sells=0, tower_sells=0, f_created=0, unaffordable=0, no_cash=0, shares_blocked=0,
                       limit_blocked=0, rest_orders=0, rest_filled=0, sniper_pnl=0.0, tower_pnl=0.0, added=0.0,
                       max_layers=0)
        self.day = self.minute = None

    # ---- 보조
    def layers(self):
        return sum(1 if f.tower else len(f.boxes) for f in self.fs)

    def lowest_avg(self):
        return min((f.avg if f.tower else min(b.avg for b in f.boxes) for f in self.fs), default=0.0)

    def sniper_boxes(self):
        return [(f, b) for f in self.fs if not f.tower for b in f.boxes]

    def drop(self):
        n = max(1, self.layers(), self.peak_layers)
        pct = next((d for hi, d in self.p.pull_tiers if n <= hi), self.p.pull_deep)
        return pct * self.p.pull_scale

    def buy_ref(self):
        anchor = self.anchor or min((f.avg for f in self.fs), default=0.0)
        cap = self.lowest_avg()
        return min(anchor, cap) if anchor and cap else anchor or cap

    def incomplete(self):
        return next((f for f in sorted(self.fs, key=lambda f: f.id) if not f.tower), None)

    def halt(self):
        for a, b, mode in self.p.halts:
            if a <= self.day <= b:
                return mode
        return ""

    # ---- 체결
    def _sell(self, f, qty, price, cost_part, kind):
        px = price * (1 - self.p.slippage)
        gross = qty * px
        net = gross * (1 - self.p.sell_fee - sell_tax(self.sym, self.day, self.p))
        if self.sym.kind == "etf_other":
            net -= max(0.0, gross - cost_part) * 0.154
        self.cash += net
        self.st["tower_pnl" if kind == "tower" else "sniper_pnl"] += net - cost_part * (1 + self.p.buy_fee)
        self.st["tower_sells" if kind == "tower" else "sells"] += 1
        self.traded = True
        if self.log is not None:
            self.log.append((self.day, self.minute, "탑매도" if kind == "tower" else "매도", f.id, qty, round(px, 2)))

    def sell_box(self, f, box, price):
        if box not in f.boxes:
            return
        self._sell(f, box.qty, price, box.cost, "sniper")
        f.boxes.remove(box)
        f.qty -= box.qty
        f.cost -= box.cost
        self.anchor = price
        if f.qty <= 0:
            self.fs.remove(f)

    def tower_sell(self, f, price):
        q = f.qty
        sq = max(0, min(int(math.floor(q * self.p.tower_sell / 100 + 0.5)), q - 1))
        if sq < 1:
            return False
        had_other = self.incomplete()
        avg = f.avg
        self._sell(f, sq, price, avg * sq, "tower")
        f.qty -= sq
        f.cost -= avg * sq
        self.anchor = avg
        self.blocked.add(f.id)
        if self.p.remain == "hold":
            f.done, f.locked = False, True
        elif f.target - f.cost > f.target * self.p.fill_tol_pct / 100:
            f.done = False
            f.boxes = [Box(self.next_box, f.qty, f.cost)]
            self.next_box += 1
            if had_other is not None and had_other is not f:
                self.merge_into(had_other, f)
        return True

    def merge_into(self, dst, src):
        """탑 매도로 미완성 F가 2개가 되면 원래 미완성 F에 합치고, 목표 초과분은 새 미완성 F로 분리"""
        boxes = dst.boxes + src.boxes
        dst.keep_once = True
        dst.indep = dst.indep or src.indep
        dst.qty += src.qty
        dst.cost += src.cost
        dst.boxes = boxes
        self.fs.remove(src)
        self.blocked.add(dst.id)
        if dst.cost <= dst.target + 1e-6:
            if dst.cost >= dst.target * (1 - self.p.fill_tol_pct / 100):
                self.complete(dst)
            return
        avg = dst.avg
        n = int(math.floor(dst.target / avg + 1e-9))
        if not 1 <= n < dst.qty:
            self.complete(dst)
            return
        rest = F(self.next_f, dst.target, dst.qty - n, dst.cost - n * avg, indep=True)
        self.next_f += 1
        rest.boxes = [Box(self.next_box, rest.qty, rest.cost)]
        self.next_box += 1
        dst.qty, dst.cost = n, n * avg
        self.complete(dst)
        self.fs.append(rest)
        self.blocked.add(rest.id)

    def complete(self, f):
        f.done, f.boxes = True, []
        if not f.since:
            f.since = self.day
            self.st["f_created"] += 1

    def buy(self, price):
        p = self.p
        f = self.incomplete()
        new = f is None
        target = p.share_amount if new else f.target
        if new and len(self.fs) >= p.max_shares:
            if p.after_shares == "stop":
                self.st["shares_blocked"] += 1
                return False
        remaining = target - (0.0 if new else f.cost)
        amount = min(target * p.portion_pct / 100, remaining)
        px = price * (1 + p.slippage)
        q = int(amount // px) if px > 0 else 0
        if q < 1:
            self.st["unaffordable"] += 1
            return False
        if new and p.after_shares == "add" and len(self.fs) >= p.max_shares and self.cash < target:
            add = target - self.cash
            self.cash += add
            self.flow += add
            self.st["added"] += add
        q = min(q, int(self.cash // (px * (1 + p.buy_fee))))
        if q < 1:
            self.st["no_cash"] += 1
            return False
        if new:
            f = F(self.next_f, target)
            self.next_f += 1
            self.fs.append(f)
        cost = q * px
        self.cash -= cost * (1 + p.buy_fee)
        box = Box(self.next_box, q, cost)
        self.next_box += 1
        tag_on_buy([b for _, b in self.sniper_boxes()], box)
        f.boxes.append(box)
        f.qty += q
        f.cost += cost
        self.anchor = px
        self.st["buys"] += 1
        self.traded = True
        if self.log is not None:
            self.log.append((self.day, self.minute, "매수", f.id, q, round(px, 2)))
        self.st["max_layers"] = max(self.st["max_layers"], self.layers())
        if f.cost >= f.target * (1 - p.fill_tol_pct / 100) or f.target - f.cost < px - 1e-9:
            self.complete(f)
        return True

    def sync_trail(self):
        """1층이 바뀌면(수량·평단) 슬롯을 새로 시작"""
        pairs = self.sniper_boxes()
        gs, has_l1 = tiers([b for _, b in pairs]) if pairs else ([], False)
        if has_l1:
            self.trail.sync(gs[0].boxes[0].qty, gs[0].boxes[0].avg)
        else:
            self.trail.key = None
            self.trail.reset()

    # ---- 한 시점의 판단: 매도 → 탑 매도 → 매수 1회
    def sniper_sell_signals(self, px):
        """→ [(f, box, 지정가)]"""
        pairs = self.sniper_boxes()
        if self.p.sell_rule == "fixed":
            return [(f, b, b.avg * (1 + self.p.fixed_tp / 100)) for f, b in pairs
                    if px >= b.avg * (1 + self.p.fixed_tp / 100) - 1e-9]
        self.sync_trail()
        if not pairs:
            return []
        owner = {id(b): f for f, b in pairs}
        gs, has_l1 = tiers([b for _, b in pairs])
        l2 = (1 if has_l1 else 0)
        l2 = l2 if len(gs) > l2 and not gs[l2].tag >= 3 else -1
        grad = gs[(l2 + 1) if l2 >= 0 else (1 if has_l1 else 0):]
        floor = None
        if has_l1:
            floor = self.trail.fire(px)
        if grad:
            wma = sum(g.cost for g in grad) / sum(g.qty for g in grad)
            target = wma * (1 + self.p.upper_tp / 100)
            if px >= target - 1e-9:
                return [(f, b, target) for f, b in pairs]
        out = []
        if floor is not None:
            out += [(owner[id(b)], b, floor) for b in gs[0].boxes]
        if l2 >= 0:
            raw = gs[l2].price * (1 + self.p.upper_tp / 100)
            if (floor is not None and (raw <= floor + 1e-9 or px >= raw - 1e-9)) or \
                    (not has_l1 and self.p.l2_solo and px >= raw - 1e-9):
                out += [(owner[id(b)], b, raw) for b in gs[l2].boxes]
        return out

    def evaluate(self, px, can_buy, can_sell, bar):
        mode = self.halt()
        if mode == "stop_all":
            return
        self.peak_layers = 0 if not self.fs else max(self.peak_layers, self.layers())
        if can_sell:
            for order in [o for o in self.resting if px >= o[0] - 1e-9]:
                self.resting.remove(order)
                if order[2] in order[1].boxes:
                    self.st["rest_filled"] += 1
                    self.sell_box(order[1], order[2], px)
            sig = self.sniper_sell_signals(px)
            busy = {id(o[2]) for o in self.resting}
            for f, b, limit in sorted(sig, key=lambda s: s[1].avg):
                if id(b) in busy or f.id in self.blocked:
                    continue
                if px >= limit - 1e-9:
                    self.sell_box(f, b, px)
                else:
                    self.resting.append((limit, f, b))
                    self.st["rest_orders"] += 1
            if self.tower_n < self.p.tower_per_day:
                hit = [f for f in self.fs if f.done and not f.locked and f.id not in self.blocked
                       and px >= f.avg * (1 + self.p.tower_tp / 100) - 1e-9]
                for f in sorted(hit, key=lambda f: -f.id):
                    if self.tower_n >= self.p.tower_per_day:
                        break
                    if self.tower_sell(f, px):
                        self.tower_n += 1
        if mode == "stop_buy":
            return
        if not self.fs:                                   # 첫 매수: 가격 조건 없음
            sig = self.first_ok
        else:
            ref = self.buy_ref()
            cap = self.lowest_avg()
            sig = px <= cap + 1e-6 and (px / ref - 1) * 100 <= self.drop() + 1e-9
        if not sig:
            return
        if not can_buy:
            self.limit_hit = True
            return
        if self.minute is not None:
            if self.last_buy_minute == self.minute:       # 주문 뒤 60초 쉼
                return
            if self.fs and self.p.gate_min:
                bucket = (self.minute - self.open_min) // self.p.gate_min
                if bucket == self.gate_bucket:
                    return
        adding = bool(self.fs)
        if self.buy(px) and self.minute is not None:
            self.last_buy_minute = self.minute
            if self.p.gate_min and adding:                # 첫 매수는 10분 구간을 쓰지 않음 (코드와 같음)
                self.gate_bucket = (self.minute - self.open_min) // self.p.gate_min

    def events(self, a, b, can_buy):
        """연속 구간 a→b 에서 상태가 바뀔 수 있는 가격들"""
        up = b > a
        lv = []
        if self.p.sell_rule == "code":
            self.sync_trail()
            tu, td = self.trail.levels()
            lv += tu if up else td
        if up:
            lv += [o[0] for o in self.resting]
            pairs = self.sniper_boxes()
            if pairs and self.p.sell_rule == "fixed":
                lv += [x.avg * (1 + self.p.fixed_tp / 100) for _, x in pairs]
            elif pairs:
                gs, has_l1 = tiers([x for _, x in pairs])
                start = 1 if has_l1 else 0
                if len(gs) > start:
                    lv.append(gs[start].price * (1 + self.p.upper_tp / 100))
                for rest in (gs[start:], gs[start + 1:]):
                    if rest:
                        lv.append(sum(g.cost for g in rest) / sum(g.qty for g in rest) * (1 + self.p.upper_tp / 100))
            lv += [f.avg * (1 + self.p.tower_tp / 100) for f in self.fs if f.done and not f.locked]
            return sorted(x for x in lv if a < x <= b)
        if can_buy and self.fs:
            ref = self.buy_ref()
            lv.append(ref * (1 + self.drop() / 100) * (1 - 1e-12))
        return sorted((x for x in lv if b <= x < a), reverse=True)

    def segment(self, a, b, can_buy, can_sell, bar):
        if a == b:
            return
        up = b > a
        cur = a
        for _ in range(500):
            ev = self.events(cur, b, can_buy)
            if not ev:
                break
            cur = ev[0]
            self.evaluate(cur, can_buy, can_sell, bar)
        self.evaluate(b, can_buy, can_sell, bar)

    def day_start(self, day):
        self.day = day
        self.blocked = set()
        self.tower_n = 0
        self.resting = []
        self.gate_bucket = self.last_buy_minute = None
        self.flow = 0.0
        self.traded = False
        self.limit_hit = False
        o = self.sessions.get(day, (9 * 60, 15 * 60 + 30))
        self.open_min, self.close_min = o

    def day_end(self):
        """매일 동기화 때의 F 정리: 번호가 뒤인 F의 평단이 앞 F 이상이면 합침. 번호는 평단 높은 순으로 다시 붙임"""
        out = []
        for cur in sorted(self.fs, key=lambda f: f.id):
            while out and cur.avg >= out[-1].avg - 1e-9 and not (
                    cur.indep or out[-1].indep or cur.keep_once or out[-1].keep_once):
                top = out.pop()
                m = F(min(top.id, cur.id), max(top.target, cur.target), top.qty + cur.qty, top.cost + cur.cost)
                m.locked = top.locked or cur.locked
                m.since = min((x for x in (top.since, cur.since) if x), default="")
                if m.target - m.cost <= m.target * self.p.fill_tol_pct / 100:
                    m.done = True
                    m.since = m.since or self.day
                elif not m.locked:
                    m.boxes = (top.boxes or [Box(self.next_box, top.qty, top.cost)])
                    m.boxes = m.boxes + (cur.boxes or [Box(self.next_box + 1, cur.qty, cur.cost)])
                    self.next_box += 2
                cur = m
            out.append(cur)
        out.sort(key=lambda f: -f.avg)
        for i, f in enumerate(out, 1):
            f.id = i
            f.keep_once = False
        self.fs, self.next_f = out, len(out) + 1


def _age(since, day):
    return (dt.date.fromisoformat(day) - dt.date.fromisoformat(since)).days


def simulate(bars, p, sym, log=False, sessions=None):
    p.check()
    s = Sim(p, sym, log, sessions)
    curve = []                                  # (day, equity, flow, stock_value, traded)
    long_held, max_age = set(), 0
    for i, bar in enumerate(bars):
        date, day, minute, o, h, l, c, v, prev, ha_ok = bar
        if day != s.day:
            s.day_start(day)
        s.minute = minute
        if v > 0:
            low_first = p.path == "low_first" or (p.path == "auto" and c >= o)
            path = (o, l, h, c) if low_first else (o, h, l, c)
            flat = h == l
            up_lock = flat and prev and c >= prev * (1 + sym.price_limit - 0.005)
            down_lock = flat and prev and c <= prev * (1 - sym.price_limit + 0.005)
            can_buy, can_sell = not up_lock, not down_lock
            s.first_ok = True
            if minute is not None:
                since_open = minute - s.open_min
                if since_open < p.open_shield_min:
                    can_buy_time = False
                else:
                    can_buy_time = True
                late = minute >= s.close_min - p.late_cutoff_min          # 첫 매수(보유 0)에만 쓰임
                s.first_ok = not late and (ha_ok or not p.first_buy_filter)
            else:
                can_buy_time = True
            for k, px in enumerate(path):
                allow = can_buy and can_buy_time
                if k == 0:
                    s.evaluate(px, allow, can_sell, bar)
                else:
                    s.segment(path[k - 1], px, allow, can_sell, bar)
            if s.limit_hit and (up_lock or down_lock):
                s.st["limit_blocked"] += 1
                s.limit_hit = False
        if i + 1 == len(bars) or bars[i + 1][1] != day:
            s.day_end()
            stock = sum(f.qty for f in s.fs) * c
            curve.append((day, s.cash + stock, s.flow, stock, s.traded))
            for f in s.fs:
                if f.since:
                    max_age = max(max_age, _age(f.since, day))
                    if _age(f.since, day) > 365:
                        long_held.add(f.since)
    last_close = bars[-1][6]
    liq = 0.0
    for f in s.fs:
        gross = f.qty * last_close * (1 - p.slippage)
        cost = gross * (p.sell_fee + sell_tax(sym, bars[-1][1], p))
        if sym.kind == "etf_other":
            cost += max(0.0, gross - f.cost) * 0.154
        liq += cost + f.qty * last_close * p.slippage
    res = dict(s.st)
    res.update(curve=curve, start_cash=s.start_cash, liquidation_cost=liq,
               final=curve[-1][1] - liq, f_over_1y=len(long_held), f_max_age=max_age,
               f_open=[dict(id=f.id, qty=f.qty, avg=round(f.avg, 2), done=f.done, locked=f.locked, since=f.since) for f in s.fs],
               trades=s.log or [])
    return res


def metrics(res, days=None):
    """days: 포함할 날짜 집합 (None = 전체). 추가 입금은 수익에서 뺀 일별 수익률을 이어 붙임"""
    prev = res["start_cash"]
    idx = peak = 1.0
    mdd = 0.0
    picked, expo, trades_days, freeze, run, started = [], [], 0, 0, 0, False
    n_all = len(res["curve"])
    for k, (day, eq, flow, stock, traded) in enumerate(res["curve"]):
        base = prev + flow
        value = eq - (res["liquidation_cost"] if k == n_all - 1 else 0.0)
        prev = eq
        started = started or traded
        if days is not None and day not in days:
            run = 0
            continue
        picked.append(day)
        idx *= value / base if base > 0 else 1.0
        peak = max(peak, idx)
        mdd = max(mdd, 1 - idx / peak)
        expo.append(stock / eq if eq > 0 else 0.0)
        run = 0 if traded or not started else run + 1
        freeze = max(freeze, run)
    if not picked:
        return None
    years = len(picked) / 245 if days is not None else \
        max(1, (dt.date.fromisoformat(picked[-1]) - dt.date.fromisoformat(picked[0])).days) / 365.25
    cagr = idx ** (1 / years) - 1 if idx > 0 and years > 0 else -1.0
    return dict(ret=idx - 1, cagr=cagr, mdd=mdd, calmar=cagr / mdd if mdd > 1e-9 else None,
                exposure=sum(expo) / len(expo), freeze=freeze, days=len(picked))


def buy_and_hold(bars, p, sym, cash):
    """첫 봉 시가에 전액 매수 → simulate 결과와 같은 모양"""
    px = bars[0][3] * (1 + p.slippage)
    qty = int(cash // (px * (1 + p.buy_fee)))
    left = cash - qty * px * (1 + p.buy_fee)
    curve = []
    for i, b in enumerate(bars):
        if i + 1 == len(bars) or bars[i + 1][1] != b[1]:
            curve.append((b[1], left + qty * b[6], 0.0, qty * b[6], i == 0 or not curve))
    gross = qty * bars[-1][6]
    liq = gross * (p.sell_fee + sell_tax(sym, bars[-1][1], p) + p.slippage)
    if sym.kind == "etf_other":
        liq += max(0.0, gross * (1 - p.slippage) - qty * px) * 0.154
    return dict(curve=curve, start_cash=cash, liquidation_cost=liq, final=curve[-1][1] - liq)


def required_share(base, max_price, min_portion_pct, unit=1_000_000):
    """1회 매수액으로 1주를 살 수 있는 몫 금액 (기본값 이상, unit 단위 올림)"""
    need = max_price * 1.002 / (min_portion_pct / 100)
    return base if need <= base else math.ceil(need / unit) * unit
