# verify7 checks 1–2 — raw price check + post-ex-date verification

Executor: Muse (subagent). Date: 2026-11-27. Scope: CereFin §102 checks 1–2 (user-approved 2026-11-27).
Read-only on `github-public` (HEAD 3923968, `git status` clean — no commits/pushes, no file modifications).
No bar prices were rounded. Restoration math was done on computed copies only.
Files in `step4-verify7/`: this report, `post_exdate_verification.csv`, `check1.py`, `check1_resid.py`, `check2.py`, `check2_detail.py`.
(Sibling check 3–4 files in the same directory — `run_check3.py`, `run_check4.py`, `distribution_ledger.csv`, `same_window_comparison.csv` — are not mine; untouched.)

Data: Kiwoom minute CSVs `github-public/data/kiwoom_minute/229200_1m.csv` (99,768 bars, 2025-11-03~2026-11-27),
`132030_1m.csv` (96,083 bars, same span); Yahoo daily cache `step4-sweep/engine/draft/data/<ticker>.KS_yahoo_2022-01-03_2026-11-26.csv`;
verify6 `step4-verify6/etf_raw_data.csv` (cross-checked, not re-derived from scratch).

## 1(a) — Kiwoom's official method (ka10080) + what the probe actually requested

### What the local probe requested (exact, from `github-public/draft/kiwoom_minute_probe.py`, identical in all copies)
- `POST https://api.kiwoom.com/api/dostk/chart`, header `api-id: ka10080`, body:
  `{"stk_cd": <code>, "tic_scope": "1", "upd_stkpc_tp": "1"}` — **no `base_dt` sent**.
- First call starts from the latest bars, then continuous paging (`cont-yn`/`next-key`) backwards to 2025-11-03.
- `upd_stkpc_tp = "1"` = 수정주가 적용 (adjusted prices). Prices arrive as signed strings; the probe takes `abs()` (minute bars carry a +/- sign vs the prior bar).
- The probe saves only `cntr_tm, open, high, low, cur_prc, trde_qty`. It does **not** save the per-bar response fields `upd_stkpc_tp` (adjustment-event code) and `upd_rt` (수정비율) — so the collected CSVs carry no record of which bars Kiwoom flagged with an adjustment event.

### What the official documentation says (Kiwoom REST API guide, ka10080 section, p.188 of the 467-page guide)
- Request body, all required (`Require Y`): `stk_cd` (종목코드), `tic_scope` (`1,3,5,10,15,30,45,60` minutes), `upd_stkpc_tp` (수정주가구분, `"0 or 1"`).
- **There is no `base_dt` parameter for ka10080 at all** (unlike ka10081 daily, where `base_dt` is required). There is **no default value**: `upd_stkpc_tp` is a mandatory field the caller must supply as 0 or 1.
- Official request example: `{"stk_cd": "005930", "tic_scope": "1", "upd_stkpc_tp": "1"}` — exactly what the probe sends.
- Response bars include per-bar `upd_stkpc_tp` as an event bitmask (1 유상증자, 2 무상증자, 4 배당락, 8 액면분할, 16 액면병합, 32 기업합병, 64 감자, 256 권리락) and `upd_rt` (수정비율).
- The guide gives **no adjustment formula and no reference price** anywhere in the ka10080 section. Kiwoom's exact distribution-adjustment formula remains unconfirmed.
- Caveat: this was read from a copy of the official guide PDF hosted on GitHub (not from inside the login-gated portal; `openapi.kiwoom.com` itself renders only a landing page via text fetch). A secondary source (AlgoLab, checked 2026-08-23 against Kiwoom's official Postman collection) quotes the official spec for ka10081: adjustment applies when `base_dt` is after the rights date, and the working rule is to start from today with `upd_stkpc_tp="1"` and page backwards. The exact sentence "수정주가 적용을 원하시는 경우, 권리발생일 이후 일자를 base_dt에 넣어…" was **not found** in the PDF copy searched — so the base_dt condition is second-hand for ka10080, and ka10080 has no base_dt to apply it to anyway.

Conclusion for 1(a): the collected minute bars were requested **with adjustment ON** (`upd_stkpc_tp="1"`, no base_dt, paged backwards from latest). That is consistent with the §101 finding that pre-ex-date bars are distribution-adjusted. The flag exists and is mandatory, not defaulted.

## 1(b) — Independent raw minute bars / second unadjusted source

- **Naver Finance daily closes: NOT obtained this session.** A direct fetch of `finance.naver.com/item/sise_day.naver?code=229200&page=1` failed with HTTP 403 (access denied); per task rules the request was not retried. Naver values are therefore **not** confirmed here — a live-browser re-check would be needed for a Naver-sourced number.
- **Finnhub daily candles (independent unadjusted source) — matched Yahoo exactly on every checked date (13/13):**

| Ticker | Date | Finnhub close | Yahoo close | Kiwoom close (last bar) |
|---|---|---|---|---|
| 229200 | 2025-12-26 | 15,380 | 15,380 | 15,362 |
| 229200 | 2025-12-29 | 15,580 | 15,580 | 15,561 |
| 229200 | 2025-12-30 | 15,430 | 15,430 | 15,411 |
| 229200 | 2026-04-24 | 20,295 | 20,295 | 20,271 |
| 229200 | 2026-04-27 | 20,670 | 20,670 | 20,645 |
| 229200 | 2026-04-28 | 20,415 | 20,415 | 20,391 |
| 229200 | 2026-04-29 | 20,490 | 20,490 | 20,490 |
| 229200 | 2026-04-30 | 19,855 | 19,855 | 19,855 |
| 132030 | 2025-12-24 | 26,115 | 26,115 | 25,987 |
| 132030 | 2025-12-26 | 26,325 | 26,325 | 26,196 |
| 132030 | 2025-12-29 | 25,955 | 25,955 | 25,955 |
| 132030 | 2025-12-30 | 25,235 | 25,235 | 25,235 |
| 132030 | 2026-01-02 | 25,310 | 25,310 | 25,310 |

  Finnhub = Yahoo on all 13 closes; Kiwoom differs pre-ex-date by the constant factor and equals Yahoo from the ex-date onward. (Also: stockanalysis.com history page, recent ~2 months only, closes match Yahoo, e.g. 2026-11-26 10,850; its date filter did not return April 2026 rows. Digrin monthly "real" vs "adjusted" prices are equal after the ex-date months and differ before — same direction, coarse granularity.)
- **No independent unadjusted MINUTE-bar source was found.** No public source offers free unadjusted 1-minute bars for these ETFs (KRX data is paywalled/login-gated; Naver does not publish minute history). This is stated plainly, not as a gap to paper over.
- What the ratio-step evidence does and does not prove: it proves Kiwoom pre-ex-date closes are a **constant multiple** of the unadjusted closes (229200 mean 0.998803, stdev 0.000016, n=120; 132030 mean 0.995099, stdev 0.000011, n=39) with the step landing exactly on the ex-date, and 1.000000 on every post-ex-date day (142/142 and 223/223 days). It does **not** by itself prove the multiple is a distribution adjustment rather than another corporate action — but no split, merger, or other action is recorded for either ETF in the window in any public source checked, and the public distribution records (ex-date, record date, amount) match the step dates exactly.

## 1(c) — Restoration accuracy (re-computed, §101 numbers re-confirmed with one correction)

Overall off-grid shares reproduce exactly: 229200 **43.63%** (174,135/399,072); 132030 **11.38%** (43,726/384,332). Flip dates reproduce: 2026-04-27 / 2025-12-29.

### ⚠️ Correction to the brief's expectation
The brief said dividing by the empirical factors (0.998803 / 0.995099) should restore on-grid at 98.40% / 98.94%. **It does not.** The 98.40% / 98.94% figures reproduce only with the **4-decimal** factors 0.9988 / 0.9951. The full-precision ratio means give materially lower matches:

| Ticker | Pre-flip fields | ÷ 0.998803 / 0.995099 (ratio mean) | ÷ 0.9988 / 0.9951 (4-decimal) |
|---|---|---|---|
| 229200 | 179,800 | 169,109 on-grid = **94.05%** | 176,927 on-grid = **98.40%** |
| 132030 | 58,460 | 56,475 on-grid = **96.60%** | 57,839 on-grid = **98.94%** |

The match function is extremely peaked (a 0.000003 change in the factor moves the match by ~4.4pp at 229200). The ratio-mean factor and the grid-restoring factor are different estimators; the grid-restoring factors are 0.9988 / 0.9951. This matches the verify6 footnote (98.40% vs 98.49% run-to-run wobble is the same peaked-match effect).

### Residual distribution (under the restoring 4-decimal factors)
Remaining off-grid: 229200 **2,873 fields (1.598%)**; 132030 **621 fields (1.062%)**. Distance of the restored price to the nearest 5-KRW grid point, in KRW:

| Ticker | min | median | mean | max | ≤1.0 | 1.0–2.0 | >2.0 |
|---|---|---|---|---|---|---|---|
| 229200 | 0.50 | 2.05 | 1.96 | 2.50 | 178 (6.2%) | 1,120 (39.0%) | 1,575 (54.8%) |
| 132030 | 0.50 | 2.00 | 1.85 | 2.50 | 59 (9.5%) | 253 (40.7%) | 309 (49.8%) |

- No residual sits closer than 0.50 KRW to the grid, and almost none in the 1.0–1.5 band (229200: 3 fields; 132030: 3 fields) — residuals cluster at 1.5–2.5 KRW off-grid (229200: 93.7%; 132030: 90.5%). That is **not** rounding noise (rounding noise would be ≤ ~0.5 KRW); it is consistent with the restoring factor being slightly off the true factor at certain price levels, so the restored price lands mid-way between grid points.
- Concentration: **no strong concentration.** Residual rate by 1,000-KRW price level: 229200 0.98–2.69% (levels 15,000–21,000 all affected); 132030 0.89–2.06%. By hour: 229200 1.36–1.75% flat across 09:00–15:00; 132030 0.70–1.50%. By date: worst day 229200 2026-01-27 at 5.10% (78/1,528), best 0.20%; 132030 worst 2025-11-12 at 4.19%, best 0.00% (2025-12-04). Mild day-to-day variation, no single-day blowout, no 15:30/15:35 clustering (residual hours 16:00 = 0).

### Naive 1 − D/close formula — §101 finding CONFIRMED (does not reproduce)
- 229200: D=28, close day before ex-date 2026-04-28 = 20,415 → naive factor **0.998628** → restores on-grid at **1.04%** (1,868/179,800).
- 132030: D=130, close day before ex-date 2025-12-26 = 26,325 → naive factor **0.995062** → restores at **6.38%** (3,732/58,460).
- The naive formula does not reproduce either empirical factor. Kiwoom's exact reference price/formula remains unconfirmed.

## Check 2 — Post-ex-date period verification

Tentative starts as briefed: 229200 from 2026-04-29; 132030 from 2025-12-29. End 2026-11-26 (last **complete** day in both CSVs; 2026-11-27 is partial — 160 bars for 229200, 163 for 132030 — and is excluded from all counts below).

### Cleanliness summary (exact counts; monthly detail in `post_exdate_verification.csv`)

| Ticker | Trading days | Bars | Price fields | Off-grid fields | Off-grid share | Missing min 09:00–15:19 | Dup timestamps | Zero-vol bars | Bars outside 09:00–15:19 |
|---|---|---|---|---|---|---|---|---|---|
| 229200 | **142** | 53,894 | 215,576 | 3,529 | 1.637% | 410 (10 days) | 0 | 0 | 344 |
| 132030 | **223** | 81,305 | 325,220 | 2,221 | 0.683% | 3,925 (165 days) | 0 | 0 | 490 |

- **Trading-day counts (exact): 229200 = 142 days** (brief expected ≈143 — the exact count is one fewer), **132030 = 223 days** (matches the ≈223 expectation). Span: 2026-04-29~2026-11-26 (142 days) and 2025-12-29~2026-11-26 (223 days). No sufficiency judgment is made here.
- **Daily off-tick share:** 229200 mean 1.63%, median 1.51%, max 4.45% (2026-10-02); 132030 mean 0.68%, median 0.59%, max 3.55% (2026-09-30). Monthly off-grid shares: 229200 0.99% (Jun) – 2.33% (Aug); 132030 0.48% (Oct) – 0.91% (Jan). Full monthly table is in the CSV.
- **Post-period off-grid residue — all ≡2 mod 5:** every one of the 5,750 post-period off-grid fields (3,529 + 2,221) is exactly 2 KRW above a 5-KRW grid point. Scattered across all regular minutes and all four OHLC fields (open most: 229200 opens 2.81% off-grid vs closes 1.08%); mostly one off-grid field per bar; **zero** in the 15:30/15:35 bars. Mechanism unconfirmed — it is not a rounding artifact and is not explained by anything in the Kiwoom documentation read.
- **Bar completeness:** duplicate timestamps 0, zero-volume bars 0 for both. Standard day = 382 bars (380 regular minutes + 15:30 + 15:35 bars) on 131/142 days for 229200.
  - 229200 missing minutes (410) sit on 10 days in contiguous blocks (worst: 2026-11-19 missing 09:00–09:59 = 60 min; 2026-06-08 59; 2026-07-28 58) — i.e. a few disrupted days, not scattered loss.
  - 132030 missing minutes (3,925) sit on 165/223 days, scattered single minutes concentrated midday (12:00 hour 1,015; 13:00 989; 11:00 495) — consistent with a low-liquidity ETF simply having no trade in that minute (no zero-volume bar is emitted instead), not with data loss. Worst day 2026-10-29: 110 missing, 272 bars.
  - **2026-11-19 is a CSAT (수능) delayed-open day:** both ETFs start at 10:00 and run to 16:35 that day (same pattern as 2025-11-13, outside the 229200 window). The 60 "missing" 09:00–09:59 minutes on 2026-11-19 are the market-wide late open, not missing data. The only stamps after 15:35 in the whole dataset (15:36–16:19, 16:30, 16:35, one bar each) come from these two CSAT days.
  - The 15:30 and 15:35 bars occur on essentially every day (229200: 15:30 on 141/142 days, 15:35 on 142/142; 132030: 223/223 and 209/223). The daily close used for the ratio check is the last bar (15:35); it equals the Yahoo/Finnhub close exactly on every post-period day.
- **Other corporate actions in the window: none found.** Kiwoom/Yahoo close ratio = 1.000000 on **every** post-period day (229200 142/142; 132030 223/223; min = max = 1.0) — no other adjustment step exists in the window. Public sources agree: 229200 distributes once a year in April (2026: 28 KRW; next April 2027 is outside the window); 132030 once a year in December (2025: 130 KRW; next record date 2026-12-30 is outside the window ending 2026-11-26). No splits, mergers, or other distributions for either ETF in the window in any source checked.

## Distribution ledger inputs (for sibling check 4)

| ETF | Ex-date | Record date | Distribution | Pay date | Pay-date sources |
|---|---|---|---|---|---|
| 229200 KODEX 코스닥150 | 2026-04-29 | 2026-04-30 | 28 KRW/share | **2026-05-06** | KRX KIND disclosure (2026-04-28, row KR7229200001: 기준일 2026-04-30, 지급예정일 2026-05-06, 28원); stockanalysis.com; etfshopping.com; FunETF — all agree. Confirmed. |
| 132030 KODEX 골드선물(H) | 2025-12-29 | 2025-12-30 | 130 KRW/share | **2026-01-05** (majority) | etfro.com, wolbae.com, TradingView, stockinvest.us, Naver blog all list 2026-01-05. **Unresolved discrepancy:** FunETF's table lists 2026-01-02 (same row duplicated). No KRX KIND disclosure for 132030 was retrieved this session. Majority says 2026-01-05; the 01-02 entry is not confirmed or refuted. |

**Withholding tax on ETF distributions: confirmed 15.4%** (general account). 15.4% = 배당소득세 14% (소득세법 §129) + 지방소득세 1.4%, withheld at payment on the distribution, for both domestic-equity ETFs (229200) and other domestic-listed ETFs (132030). Sources: ETFstyle ETF tax guide (citing 소득세법 시행령 §26조의2, 소득세법 §129/§14) and two independent explainers, all stating 15.4% for general-account ETF distributions. (ISA 9.9% over the allowance and pension-account deferral are different regimes and not used here.)

## What remains undone / unconfirmed
- Naver Finance daily closes not obtained (HTTP 403 on text fetch, not retried). Finnhub + stockanalysis + Digrin corroborate Yahoo instead.
- No independent unadjusted **minute**-bar source exists in public access; the minute-level adjustment is evidenced only by the Kiwoom-vs-unadjusted-daily ratio step.
- Kiwoom's exact distribution-adjustment formula / reference price: unconfirmed (official docs give the flag, no formula). The probe also discarded the per-bar `upd_stkpc_tp`/`upd_rt` fields, so event flags cannot be recovered from the CSVs — a re-pull with `upd_stkpc_tp="0"` (and saving those fields) is the only way to get truly raw bars.
- The post-period ≡2 mod 5 residue (1.64% / 0.68% of fields) has no confirmed mechanism.
- 132030 pay date 2026-01-02 (FunETF) vs 2026-01-05 (5 other sources): unresolved.
- Sample sufficiency (142 / 223 days): counts and spans reported above; judgment left to CereFin/the user.
