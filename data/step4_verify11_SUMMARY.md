# tickround_v1 검증 요약 (Muse 검증, 2026-11-28)

- 제출: `tickround_v1_hybrid-backtest_195_t1qs.zip`, sha256 `444ac3ba66f373a41597dea5d2e3503b199a0d10bc3665779073ce984085cd86`, 40,414 bytes
- 기준: draft commit 40a1b05 (baseline `run_backtest.py` sha256 `f43481d535cda21eb3192fb856069cbacb832b884c16759933a773c6b6e1afe7` — 기지값과 일치)
- 데이터: `229200_1m.csv` sha256 `af01cd490e4177e72ad4e3143ae47eec64497a2485d957f04994104c2c7eefe4`, 99,246봉, 2025-11-03~2026-11-26, 262일
- 환경: Python 3.12.3 (클로드는 3.13.18으로 검증했다고 적음). pytest 없음 — `python3 test_*.py` (unittest)로 실행.
- 작업 dir: `~/workspace/backtest-hybrid-domestic/claude-tickround-verify/` (`patched/`, `baseline/`, `none_equiv.py`, `trail_compare.py`, `trail_trace.py`, `bh_compare.py`, `trail_compare.json`)
- 저장소 `github-public/` 미수정 (검증 종료 시 `git status` clean 확인).

## 결론

- 항목 1~5, 7, 8은 **통과** — 클로드 주장과 측정값이 일치한다.
- 항목 6(트레일링 범위) **재현됨** — 클로드가 VERIFY.md에서 스스로 제기한 범위 차이가 실제 숫자로 확인된다. 판단가격 반올림 여부에 따라 primary 0.0025% 한 쌍이 −3.37%(클로드) 대 +6.43%(참조)로 갈린다. **이건 버그가 아니라 설계 선택 문제**라서, 어느 쪽을 쓸지는 풀이님 결정이 필요하다.
- 어느 숫자도 추측이 아니다. 아래 모든 숫자는 내가 직접 실행한 명령의 출력이다.

## 항목별 판정

### 1. ZIP 내용 — PASS
- VERIFY.md가 루트에 있고 첫 줄이 `# 방: 하이브리드 백테스트 (hybrid-backtest)`이다.
- 파일 7개, 전부 repo-relative (`draft/...`):
  | 파일 | 바이트 |
  |---|---:|
  | VERIFY.md | 6,684 |
  | draft/README.md | 3,949 |
  | draft/hbt_engine.py | 33,796 |
  | draft/hbt_rules.py | 6,131 |
  | draft/repro_tickround.py | 1,555 |
  | draft/run_backtest.py | 31,164 |
  | draft/test_hbt.py | 17,804 |
  | draft/test_tickround.py | 8,688 |
- 40a1b05 대비 변경: README.md, hbt_engine.py, hbt_rules.py, run_backtest.py, test_hbt.py. 신규: repro_tickround.py, test_tickround.py.
- `draft/*.py`에서 requests/urllib/socket/http/api-key/secret/token/password/bearer/order( 검색 결과 없음 — 네트워크·주문·비밀 코드 없음.

### 2. 테스트 — PASS, 101/101
40a1b05 새 복사본 + ZIP draft/ 오버레이, `python3 test_*.py`:

| 파일 | 결과 |
|---|---|
| test_hbt.py | 38 OK (14.490s) |
| test_tickround.py | 14 OK (0.864s) |
| test_hbt_fix2.py | 42 OK (9.045s) |
| test_kiwoom_probe.py | 7 OK (0.001s) |
| **합계** | **101/101** |

- 구 코드 교차검사: test_tickround.py를 40a1b05(패치 전) 코드에서 돌리면 **import 단계에서 실패**한다 — `ImportError: cannot import name 'round_tick' from 'hbt_engine'` (tick_size도 없음). 동작 단언으로 실패한 게 아니라 새 기능 자체가 없어서 실패한다. 즉 이 테스트 파일은 패치를 실제로 검사한다.

### 3. test_hbt.py 변경 — PASS, 주장대로 한 줄만
- diff 결과 변경은 line 156 한 줄뿐: `s, f = self.two(P(), (103.0, 102.0, 100.0))` → `self.two(P(tick_mode="none"), (103.0, 102.0, 100.0))` + 주석 "반올림 전 지정가를 보는 시험".
- 새 기본값(primary)으로 같은 테스트를 재현하면 지정가가 **[104, 104, 104]**가 된다 (104.03 지정가가 ETF 1원 호가(~104원, 2,000원 미만 → 1원)로 104에 내림됨). 클로드 주장과 일치.

### 4. 재현 8/8 — PASS, 오프틱 체결 0건
`python3 draft/repro_tickround.py draft <229200_1m.csv>` (patched dir). 조건: sell_rule=fixed, fixed_tp=1.0, protect_ratio=0, remain=hold, path auto, share 20,000,000x3, portion 10%.

| 행 | liq_ret | 매수/매도 | 타워 | 오프틱 체결 |
|---|---|---:|---:|---:|
| ETF primary slip 0 | +4.83% | 209/198 | 1 | 0 |
| ETF primary 0.0025% | −3.26% | 160/150 | 0 | 0 |
| ETF nearest slip 0 | +0.90% | 209/203 | 0 | 0 |
| ETF nearest 0.0025% | +0.86% | 208/202 | 0 | 0 |
| stock primary slip 0 | −0.32% | 189/183 | 0 | 0 |
| stock primary 0.0025% | −1.30% | 180/174 | 0 | 0 |
| stock nearest slip 0 | +4.68% | 201/190 | 1 | 0 |
| stock nearest 0.0025% | +4.68% | 201/190 | 1 | 0 |

8/8 전부 소수 2자리까지 정확히 일치. "stock" 행은 `E.TICK_KIND["etf_equity"] = tab`으로 호가표만 바꾼 것(심볼 종류는 etf_equity, ETF 세금 유지) — VERIFY.md 설명대로다.

### 5. none == baseline — PASS, 36/36 동일
`python3 none_equiv.py baseline/draft patched/draft <229200_1m.csv>` (36회: 229200 분봉 12회 + 합성 일봉 24회, code/fixed × 슬리피지 0/0.0025%/0.1%/0.4% × 3종류, 보호 off, 타워 5%).

- baseline 해시: `a223d7aaeeda488d1435079145d882eb98563264db66293dd90e0ca321d080cb`
- patched-none 해시: `a223d7aaeeda488d1435079145d882eb98563264db66293dd90e0ca321d080cb`
- **IDENTICAL, differing runs: 0**

주의: 내 해시값(a223d7aa…)은 클로드가 적은 해시(60648ec4…)와 다르다. 해시 입력 직렬화와 합성 일봉 재구성이 달라서 그렇고, 재현해야 할 속성은 "baseline과 patched-none이 동일하다"는 것이며 그건 36/36으로 재현됐다.
- 코드 확인: none 경로는 구성상 bit-identical이다. 청산 시 `gross = f.qty * last_close * (1 - p.slippage)`, `liq += cost + f.qty * last_close * p.slippage`로 옛 식을 그대로 복원하고, buy_and_hold의 none 분기도 옛 식(`liq = gross * (p.sell_fee + sell_tax(...) + p.slippage)`, `proceeds = gross * (1 - p.slippage)`)을 복원한다. 클로드가 말한 "처음 두 시도는 청산 비용의 마지막 자리 부동소수점 차이"는 곱셈 순서 문제였고, 옛 식 복원으로 해결됐다는 설명과 코드가 맞는다.

### 6. 트레일링 범위 차이 — 재현됨 (가장 중요)
조건: sell_rule=code, protect_ratio=0, 나머지 기본값, ETF 표, 229200 분봉. `python3 trail_compare.py <csv> patched/draft step4-verify5/engine_tick`.

| 쌍 | 클로드 (측정) | 참조 패치 (측정) | 클로드 주장 | 참조 주장 |
|---|---|---|---|---|
| primary slip 0 | −3.7923% (163/152/0) | −3.7781% (156/146/0) | −3.79% (163/152/0) | −3.78% (156/146/0) |
| primary 0.0025% | **−3.3734% (166/156/0)** | **+6.4257% (145/140/0)** | −3.37% (166/156/0) | +6.43% (145/140/0) |
| nearest slip 0 | −5.3444% (135/124/0) | −5.2637% (134/123/0) | −5.34% (135/124/0) | −5.26% (134/123/0) |
| nearest 0.0025% | −5.3822% (135/124/0) | −5.3085% (134/123/0) | −5.38% (135/124/0) | −5.31% (134/123/0) |

클로드가 VERIFY.md에 적은 8개 숫자 전부가 소수 2자리까지 재현됐다.

**첫 갈림 매매 (네 쌍 전부 동일 지점, trade idx 3, 2025-11-04, F1 128주 매도):**

| 쌍 | 클로드 | 참조 |
|---|---|---|
| primary 0.0025% | 548분, 체결 15,775 | 545분, 체결 15,775 |
| primary 0 | 548분, 체결 15,780 | 545분, 체결 15,780 |
| nearest 0 | 548분, 체결 15,780 | 562분, 체결 15,855 |
| nearest 0.0025% | 548분, 체결 15,780 | 562분, 체결 15,855 |

체결가는 같고(또는 nearest 참조만 다름) **분만 다르다**. 판단 반올림 효과다.

**메커니즘 (primary 0.0025% 쌍, `trail_trace.py`로 분 단위 추적해 확정):**
- F2 평단 15,605원. 1단계 보존선 = 15,605 × 1.0115 = **15,784.46원**.
- 참조: lock을 **15,784.46 그대로** 둔다. 엔진이 이벤트 가격만 반올림한다 (`events()`에서 `td = [tick_round(x, "sell") for x in td]` → 하향 레벨 15,780). px=15,780에서 판단 `15,780 < 15,784.46`이 참 → STOP. 반환 한도 15,784.46을 엔진이 15,780으로 반올림하고, px(15,780) ≥ 한도(15,780)라 **545분에 즉시 매도** (체결 15,775 = floor(15,780 × (1−0.000025))).
- 클로드: lock을 저장할 때 이미 반올림한다 (`_ratchet`에서 `lock = max(lock, price(1.15))`, price가 sell_tick → **15,780**). 하향 레벨은 lock − 0.001 = **15,779.999**. px=15,779.999에서 판단 `15,779.999 < 15,780`이 참 → STOP이지만, 반환 한도는 15,780이고 px(15,779.999)가 한도보다 **0.001원 낮아서** 즉시 매도가 아니라 **대기 주문(resting order)** 으로 들어간다. 가격이 15,780으로 돌아온 **548분**에야 체결된다 (체결가 동일 15,775).
- 즉 반올림된 lock은 판단 가격(lock−0.001)을 반올림된 한도(lock)보다 0.001원 아래에 두어, STOP 신호를 즉시 체결이 아닌 대기 주문으로 바꾼다. 이 3분 지연이 그 뒤 262일 전체 매매 순서를 바꾸어 최종 −3.37% 대 +6.43%(약 9.8pp 차이)로 벌어진다. 첫 갈림 이후의 차이는 이 타이밍 이동의 누적이다.

**클로드가 반올림하고 참조가 안 하는 트레일 판단 지점 (site by site, 코드 대조):**

| 지점 | 클로드 (`patched/draft/hbt_rules.py` Trail, `rnd=self.sell_tick` 주입) | 참조 (`step4-verify5/engine_tick/hbt_rules.py` Trail, rnd 없음) |
|---|---|---|
| `price(pct)` | 반올림한 트리거 가격 반환 (`rnd(x)`) | 반올림 없음 (`ref * (1 + pct/100)`) |
| `step()` 점화 판단 | `p < price(ignite)` — 반올림된 점화 가격과 비교 | `p < price(ignite)` — 반올림 안 된 값과 비교 |
| `step()` 하드캡 판단 | `p >= price(cap)` — 반올림된 캡 가격과 비교 | 반올림 안 된 캡 가격과 비교 |
| `step()` peak 초기값 | `max(p, price(ignite))` — 반올림된 점화 가격 | 반올림 안 된 점화 가격 |
| `step()` CAP 분기 lock | `price(steps[0][1])` — 반올림 | 반올림 안 됨 |
| `_ratchet()` 단계 발동 | `peak >= price(trig)` — 반올림된 트리거 가격과 비교 (퍼센트 비교가 아님) | `gain >= trig` — 퍼센트 비교 |
| `_ratchet()` lock 값 | `lock = max(lock, price(lock))` — 반올림된 값 저장 | `lock = max(lock, price(lock))` — 반올림 안 된 값 저장 |
| `fire()` 점화 하한 | `p < price(ignite)` — 반올림 | 반올림 안 됨 |
| `fire()` CAP 반환 | `price(cap)` — 반올림 | 반올림 안 됨 |
| `fire()` limit | `rnd(p * 0.998)` — 반올림 | `p * 0.998` — 반올림 안 됨 (엔진이 나중에 반올림) |

**둘 다 같은 것:** `levels()`의 반올림 효과와 최종 한도/체결가 반올림. 다만 lock 값 자체가 달라서 레벨 값이 갈린다 — 클로드 하향 레벨 15,779.999(=15,780−0.001) 대 참조 하향 레벨 15,780(=tick_round(15,784.46−0.001)). 참조는 `events()`에서 tu/td를 `tick_round(x, "sell")`로 반올림하고(참조 엔진 519–520행), `fire()` 반환값도 엔진에서 반올림한다(437행 `floor = tick_round(floor, "sell")`). 클로드는 Trail 안에서 이미 반올림해서 엔진이 tu/td를 다시 반올림하지 않는다.

**판정:** 클로드의 주장은 사실이다. 트레일 판단가격을 반올림하면 결과가 크게 바뀔 수 있다. 어느 쪽이 "맞는" 동작인지는 이 검증으로 정할 수 없다 — 실제 거래소에서 지정가 주문이 어떻게 체결되는지(보존선 이탈 시 지정가가 즉시 체결되는지, 대기 주문이 되는지)에 달린 설계 결정이다. 풀이님 결정이 필요하다.

### 7. 범위 메모
**(a) buy_and_hold 체결가 반올림 — 정량화됨.** `python3 bh_compare.py <csv>` (현금 60,000,000원, 229200 구간, 기본 슬리피지 0):

| 엔진 | 최종 자산 | 청산 비용 | 수익률 | baseline 대비 |
|---|---:|---:|---:|---:|
| baseline 40a1b05 | 42,000,036.20 | 6,300.05 | −29.999940% | — |
| patched primary | 41,989,203.82 | 6,298.42 | −30.017994% | −10,832.38 (−0.018pp) |
| patched nearest | 42,003,907.78 | 6,300.05 | −29.993487% | +3,871.58 (+0.006pp) |
| patched none | 42,000,036.20 | 6,300.05 | −29.999940% | 0 (동일) |

메커니즘: 첫 봉 시가 15,496원. primary는 매수 가격을 15,500원으로 올림 → 수량 3,871주 → 3,870주(−1주) → 매도 대금 −10,850원. nearest는 매수 가격을 15,495원으로 내림 → 수량은 같고 남은 현금만 +3,871.58원. 마지막 봉 종가 10,850원은 이미 5원 배수라 매도 가격 반올림은 영향 없음. 참조 패치는 buy_and_hold를 건드리지 않았다(참조 `engine_tick.patch`에 buy_and_hold 관련 변경 없음, 참조 엔진 tick_round 호출 지점에 BH 없음). 즉 이 패치는 BH 벤치마크 자체를 약간 바꾼다 — 작지만 0은 아니다.
**(b) nearest 동점 올림 — 코드 확인.** `round_tick` nearest는 `math.floor(x + 0.5 + 1e-9) * t`라서 두 호가 정중앙인 값은 위로 간다 (예: 15,497.5원 → 15,500원). 실측: 15,496원 → 15,495원 (동점이 아닌 경우 내림).
**(c) fits_one_share(sym 없음) 반올림 안 함 — 코드 확인.** `fits_one_share(p, first_price, sym=None)`는 `sym is not None`일 때만 `px = round_tick(px, "buy", ...)` 한다. sym이 없으면 반올림 없음.
**(d) 주식 호가표 날짜 전환 없음 — 코드 확인.** `TICK_TABLES`는 날짜 조건이 없고, 주석에 "날짜별 전환은 하지 않아 그 전 날짜에도 같은 표를 씀"이라고 적혀 있다. 2022년 일봉에도 현재 표가 그대로 적용된다.

### 8. 지시문 준수
- **매수 신호 하락 목표(d drop level) 반올림 안 함 — PASS.** 반올림 호출 지점은 전부 체결가·트리거·레벨·청산·BH·fits_one_share다. 매수 판단은 반올림 없음: `evaluate()` 479행 `sig = px <= cap + 1e-6 and (px / ref - 1) * 100 <= self.drop() + 1e-9`, `events()` 529행 `lv.append(ref * (1 + self.drop() / 100) * (1 - 1e-12))` — 둘 다 원값 그대로다.
- **날짜별 세션 규칙(variant C) 손대지 않음 — PASS.** diff에 세션 변경이 없다. `hbt_minute.py`는 40a1b05과 동일하고, 패치 파일에서 수능/CSAT/16:30/2025-11-13 관련 추가가 없다 (검색 히트: 기존 `sessions` docstring과 sweep `variants()`뿐).

## 재현 명령 (전부 이 검증에서 실행한 것)
```
# 테스트 (40a1b05 복사본 + ZIP 오버레이)
python3 test_hbt.py; python3 test_tickround.py; python3 test_hbt_fix2.py; python3 test_kiwoom_probe.py
# 구 코드 교차검사: 40a1b05 원본에서 python3 test_tickround.py → ImportError
# 재현 8/8
python3 draft/repro_tickround.py draft <229200_1m.csv>
# none 동등성 (36회)
python3 none_equiv.py baseline/draft patched/draft <229200_1m.csv>
# 트레일링 비교 (8회) + JSON
python3 trail_compare.py <csv> patched/draft ~/workspace/backtest-hybrid-domestic/step4-verify5/engine_tick
# 첫 갈림 추적
python3 trail_trace.py <csv> patched/draft ~/workspace/backtest-hybrid-domestic/step4-verify5/engine_tick
# BH 비교
python3 bh_compare.py <csv>
```

## 남은 질문 (풀이님 결정 필요)
1. 트레일 판단가격 반올림을 유지할 것인가(클로드 방식), 아니면 참조 패치처럼 판단은 원값으로 두고 이벤트 가격만 반올림할 것인가. primary 0.0025%에서 −3.37% 대 +6.43%로 갈리는 만큼, 이 선택이 결과에 미치는 영향이 크다.
2. buy_and_hold 반올림을 둘 것인가(벤치마크가 ±0.02pp 움직인다), 참조처럼 BH는 원값으로 둘 것인가.
