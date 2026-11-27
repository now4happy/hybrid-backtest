"""meritz_laoer v1.5.3 의 층 태그·1층 트레일링 규칙을 옮긴 순수 계산 (봇 코드를 import 하지 않음)."""

MAX_TIERS = 7


class Box:
    """매수 1건. tag 0 = 태그 없음, 1 = 1층, 2~7 = 상단층"""
    __slots__ = ("id", "qty", "cost", "tag")

    def __init__(self, id, qty, cost, tag=0):
        self.id, self.qty, self.cost, self.tag = id, qty, cost, tag

    @property
    def avg(self):
        return self.cost / self.qty


class _G:
    """한 층 (같은 태그로 합쳐진 버킷들)"""
    __slots__ = ("qty", "cost", "price", "tag", "boxes")

    def __init__(self, boxes, tag, price=None):
        self.boxes = list(boxes)
        self.qty = sum(b.qty for b in boxes)
        self.cost = sum(b.cost for b in boxes)
        self.price = self.cost / self.qty if price is None else price
        self.tag = tag


def _by_price(g):
    return round(g.price, 4)


def _relabel(uppers, vacant=None):
    v = list(uppers)
    if not v:
        return []
    if vacant is None:
        vacant = v[0].tag >= 3
    start = 3 if vacant else 2
    limit = MAX_TIERS - (start - 1)
    while len(v) > limit:          # 넘치면 2번째로 낮은 칸과 가장 높은 칸을 합침 (L2_L7_MERGE_SORT)
        low, high = v[1], v[-1]
        v = [v[0]] + v[2:-1] + [_G(low.boxes + high.boxes, MAX_TIERS)]
        v.sort(key=_by_price)
    for i, g in enumerate(v):
        g.tag = min(MAX_TIERS, i + start)
    return v


def group(boxes):
    """큐 순서의 층 목록. 태그 없는 버킷이 하나라도 있으면 None"""
    if not boxes or any(b.tag <= 0 for b in boxes):
        return None
    tags = {}
    for b in boxes:
        tags.setdefault(b.tag, []).append(b)
    return [_G(sorted(tags[t], key=lambda b: (round(b.avg, 4), b.id)), t) for t in sorted(tags)]


def tiers(boxes):
    """매도 판정용 층 목록 → (층 목록, 1층 있음). 태그가 불완전하면 평단 오름차순, 가장 싼 것이 1층"""
    g = group(boxes)
    if g is not None:
        return g, g[0].tag == 1
    g = [_G([b], i + 1) for i, b in enumerate(sorted(boxes, key=lambda b: (b.avg, -b.id)))]
    return g, bool(g)


def tag_on_buy(old_boxes, new):
    """새 매수 한 건 뒤 모든 버킷의 층 태그를 다시 정함 (box.tag 를 바꿈)"""
    queue = group(old_boxes)
    if queue is None and old_boxes:
        rows = [_G([b], 2) for b in sorted(old_boxes, key=lambda b: (round(b.avg, 4), b.id))]
        queue = [rows[0]] + _relabel(rows[1:])
        queue[0].tag = 1
    queue = queue or []
    n = _G([new], 1)
    if not queue:
        out = [n]
    elif queue[0].tag != 1:                       # 1층 공석
        if n.price <= queue[0].price + 1e-6:
            out = [n] + _relabel(queue)
        else:
            out = _relabel(sorted(queue + [n], key=_by_price))
    elif n.price > queue[0].price + 1e-6:
        comb = sorted(queue + [n], key=_by_price)
        low = comb[0]
        if low.tag >= 2:
            low.tag = 1
        out = [low] + _relabel(comb[1:])
    else:
        out = [n] + _relabel(queue, vacant=False)
    for g in out:
        for b in g.boxes:
            b.tag = g.tag


class Trail:
    """1층 트레일링 슬롯: 점화 → 보존선(단계 상향) → 보존선 이탈 또는 하드캡에서 매도 신호"""

    def __init__(self, ignite, steps, cap):
        self.ignite, self.steps, self.cap = ignite, steps, cap
        self.key = None
        self.reset()

    def reset(self):
        self.armed, self.peak, self.lock = False, 0.0, 0.0

    def sync(self, qty, avg):
        key = (qty, round(avg, 4))
        if key != self.key:
            self.key, self.ref = key, avg
            self.reset()

    def price(self, pct):
        return self.ref * (1 + pct / 100)

    def _ratchet(self):
        gain = (self.peak / self.ref - 1) * 100
        for trig, lock in self.steps:
            if gain >= trig - 1e-9:
                self.lock = max(self.lock, self.price(lock))

    def step(self, p):
        """→ '' | 'CAP' | 'STOP'"""
        if not self.armed:
            if p < self.price(self.ignite) - 1e-9:
                return ""
            self.armed = True
            if p >= self.price(self.cap) - 1e-9:
                self.peak, self.lock = p, self.price(self.steps[0][1])
                self._ratchet()
                return "CAP"
            self.peak = max(p, self.price(self.ignite))
            self._ratchet()
            return ""
        self.peak = max(self.peak, p)
        self._ratchet()
        if p >= self.price(self.cap) - 1e-9:
            return "CAP"
        if self.lock > 0 and p < self.lock - 1e-9:
            return "STOP"
        return ""

    def fire(self, p):
        """신호 확인 → 매도 하한가(지정가) 또는 None"""
        code = self.step(p)
        if not code:
            return None
        if p < self.price(self.ignite) - 1e-9:
            self.reset()
            return None
        lock = self.lock if self.lock > 0 else self.price(self.ignite)
        if code == "CAP" and p >= self.price(self.cap) - 1e-9:
            return self.price(self.cap)
        if p >= lock - 1e-9:
            return max(lock, p * 0.998)
        return lock           # 이미 보존선 아래: 보존선 지정가라 가격이 돌아와야 체결

    def levels(self):
        """가격이 이 값을 지날 때 상태가 바뀜 → (오를 때, 내릴 때)"""
        if self.key is None:
            return [], []
        up = [self.price(self.ignite), self.price(self.cap)] + [self.price(t) for t, _ in self.steps]
        down = [self.lock - 0.001] if self.armed and self.lock > 0 else []
        return up, down
