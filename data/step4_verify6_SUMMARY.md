# Step 4 verify6 — CereFin §99 checks 1–3 (2026-11-27)

Executor: Muse (subagent). Engine: draft commit 40a1b05 (fix 2 + negcalmar),
`run_backtest.py` sha256 `f43481d535cda21eb3192fb856069cbacb832b884c16759933a773c6b6e1afe7`.
Repo `github-public` untouched (HEAD 4a63db4, `git status` clean — no commits/pushes).
Baseline: capital FIXED 60,000,000 KRW, period 2025-11-03~2026-11-26, path auto.
No real trading, no orders, no server changes.

Files: `SUMMARY.md` (this), `etf_raw_data.csv` (526 rows: daily off-tick share +
Kiwoom/Yahoo close ratio), `execution_model.csv` (12 cells),
`statewise_orderability.csv`, `flat_successes_in_window.csv`,
`config_step4_verify6.json`, `check1_summary.json`, `portions_full.json`,
`run_check1.py`, `run_check2.py`, `run_check3.py`, `tick_new.csv`,
`trace_*.json`, `trades_*.json`, `base_check2.json`.

---

## Check 1 — ETF raw data: what causes the off-grid OHLC?

### Flip dates (recomputed from the raw Kiwoom CSVs; overall shares reproduce exactly)

| Ticker | Overall off-grid (5-KRW) | Flip date | Before flip | After flip |
|---|---|---|---|---|
| 229200 KODEX 코스닥150 | **43.63%** (174,135 / 399,072 price fields) | **2026-04-27** | ~100% through 2026-04-24 | 4.6% on 04-27; 1.4% on 04-29; ~1–2% after |
| 132030 KODEX 골드선물(H) | **11.38%** (43,726 / 384,332) | **2025-12-29** | messy Nov–Dec 2025 (many ~100% days interspersed with low days) | 0.4% on 12-29; ~0.5–1% after |

Flip rule: first day with daily off-tick share < 10% whose next-10-trading-day mean
is also < 10%. 229200 2026-04-28 is anomalous at 60.2% (intraday price-level mix,
not a mid-day regime flip — hourly shares 09:00 85%, 10:00 79%, 11:00 100%,
12:00 41%, 13:00 4%, 14:00 59%, 15:00 44%).

### Hypothesis tests

**(a) Distribution (분배금) adjustment — SUPPORTED.**
- 229200 distributes ONCE a year, end of April (not quarterly — the earlier
  quarterly assumption was wrong). Most recent ex-date **2026-04-29**, record
  2026-04-30, **28 KRW/share**, pay date 2026-05-06. Previous: 2025-04-29, 44 KRW;
  2024-04-29, 20 KRW. (Sources: Samsung Asset Management fund doc, stockanalysis.com.)
- 132030 distributes ONCE a year, December. Ex-date **2025-12-29**, record
  2025-12-30, **130 KRW/share**, pay date 2026-01-05 (FunETF's table lists
  2026-01-02 in one row and 2026-01-05 in another — pay-date discrepancy between
  sources, immaterial).
- Both ex-dates match the ratio-step dates EXACTLY (see (b)). This also explains
  why 229200 shows only one step (no Jan/Jul/Oct distributions exist).

**(b) Adjusted vs unadjusted price — SUPPORTED.**
Kiwoom minute-derived daily close (last bar of day) / Yahoo unadjusted daily close:

| Ticker | Pre-step mean | stdev | n days | Step date | Post-step mean | n days |
|---|---|---|---|---|---|---|
| 229200 | **0.998803** | 0.000016 | 120 (2025-11-03~2026-04-28) | **2026-04-29** (0.998824 → 1.000000) | 1.000000 | 142 |
| 132030 | **0.995099** | 0.000011 | 39 (2025-11-03~2025-12-26) | **2025-12-29** (0.9951 → 1.000000) | 1.000000 | 223 |

Kiwoom is distribution-adjusted before the ex-date and unadjusted from the ex-date
onward; Yahoo is unadjusted throughout. No other ratio steps anywhere in the window.

**(c) Constant multiplicative factor — SUPPORTED, with a residual formula discrepancy.**
Model: Kiwoom = round(true_on_grid × f). Dividing pre-flip prices by the empirical
factor and rounding to integer restores on-grid prices at **98.40% (229200,
f=0.9988, n=179,800)** / **98.94% (132030, f=0.9951, n=58,460)**. The match
function is extremely peaked — f=0.998803 drops 229200 to 94.05%, f=0.995099 drops
132030 to 96.6% — so the factors are precisely determined by the data.
Residual mismatches (1.6% / 1.1%) are spread evenly across dates and minutes, no
clustering at specific dates or 15:30/15:35.
**Residual discrepancy to report honestly:** the naive formula 1 − D/close does NOT
reproduce the empirical factor — f=0.998628 (229200, using 04-28 close 20,415)
matches at only 1.05%; f=0.995062 (132030, using 12-26 close 26,325) at 6.38%.
Kiwoom's exact reference price/formula is unconfirmed.

Residue mechanism (explains scattered low days and the 04-28 intraday mix):
pre-flip closes are predominantly ≡1 mod 5; post-flip ≡0. With
adjusted = round(true × 0.9988), whether the adjusted price lands on-grid depends
on the price level (sweet spots where 0.001197×P ≈ integer) — so off-share varies
by day/hour with price level.

### Recommendation (not implemented)
Do NOT round bar prices arbitrarily. Options: (i) use the data as-is and disclose
the distribution adjustment; (ii) unadjust pre-ex-date bars by dividing by the
empirical factor (0.9988 / 0.9951) — restores on-grid at 98.4% / 98.9%;
(iii) use only the clean post-flip period. Decision belongs to the user/CereFin.

---

## Check 2 — 229200 execution model: rounding vs fill possibility

Setting: 229200, fixed TP 1.0%, rest baseline. Tick harness = verify5
`engine_tick/` (working copy only; draft untouched). Primary = buy exec ceil,
sell exec/trigger floor; nearest = round-to-nearest; buy-signal thresholds not
rounded; all exec prices asserted tick multiples.

### Comparison table (tick × rounding × slippage) — `execution_model.csv`

| tick | rounding | slippage | liq_ret | Calmar | MDD | buys | sells | tower | trigger 12:35 | sell 12:34 | sell 12:35 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| none | unrounded | 0 | +4.71% | 0.68 | 6.49% | 202 | 191 | 1 | 16,254.03 | N | Y |
| none | unrounded | 0.0025% | −2.99% | −0.25 | 11.36% | 162 | 152 | 0 | 16,255.25 | N | N |
| none | unrounded | 0.05% | +3.50% | 0.50 | 6.53% | 175 | 164 | 1 | 16,278.42 | N | N |
| none | unrounded | 0.1% | −3.22% | −0.26 | 11.56% | 173 | 163 | 0 | 16,302.84 | N | N |
| ETF 5 | primary | 0 | +4.83% | 0.70 | 6.12% | 209 | 198 | 1 | 16,250 | Y | N |
| ETF 5 | primary | 0.0025% | −3.26% | −0.27 | 11.50% | 160 | 150 | 0 | 16,260 | N | N |
| ETF 5 | primary | 0.05% | +3.32% | 0.47 | 6.51% | 176 | 165 | 1 | 16,275 | N | N |
| ETF 5 | primary | 0.1% | −1.17% | −0.23 | 11.03% | 114 | 111 | 0 | 16,305 | N | N |
| ETF 5 | nearest | 0 | +0.90% | 0.16 | 6.17% | 209 | 203 | 0 | 16,255 | N | Y |
| ETF 5 | nearest | 0.0025% | +0.86% | 0.15 | 6.15% | 208 | 202 | 0 | 16,255 | N | Y |
| ETF 5 | nearest | 0.05% | +3.54% | 0.51 | 6.37% | 177 | 166 | 1 | 16,285 | N | N |
| ETF 5 | nearest | 0.1% | −3.22% | −0.26 | 11.57% | 173 | 163 | 0 | 16,300 | N | N |

New cells (this run): primary 0.05% / 0.1%, nearest 0.05% / 0.1%. The table is
non-monotonic in slippage under BOTH rounding rules (primary +4.83 → +3.32 →
−1.17; nearest +0.90 → +3.54 → −3.22) and tower flips with slippage (primary
1/0/1/0; nearest 0/0/1/0 across 0 / 0.0025% / 0.05% / 0.1%). No single
"profit survives up to X% slippage" break-even exists.

### Fill possibility — 2025-11-04 12:34 / 12:35 bars (Kiwoom adjusted prices)

- 12:34 bar (minute 754): O 16,245 / H 16,250 / L 16,240 / C 16,245, V 24,884.
- 12:35 bar (minute 755): O 16,250 / H 16,255 / L 16,240 / C 16,250, V 27,387.
- Trigger inside [low, high] means the price was touched; whether a fill would
  actually occur also depends on volume/queue at that price — **unmodeled, no fill
  probability invented**. (Bar volume 24,884 / 27,387 shares vs sell qty ~124
  shares is total bar volume, not volume at the trigger price.)
- Touched: unrounded slip 0 (trigger 16,254.03 < 12:35 high 16,255 → sold 12:35);
  primary slip 0 (trigger 16,250 = 12:34 high exactly → sold 12:34);
  nearest slip 0 and 0.0025% (trigger 16,255 = 12:35 high exactly → sold 12:35).
- NOT touched: unrounded 0.0025% (16,255.25, 0.25 above the high), unrounded
  0.05% (16,278.42), unrounded 0.1% (16,302.84), primary 0.0025% (16,260),
  primary 0.05% (16,275), primary 0.1% (16,305), nearest 0.05% (16,285),
  nearest 0.1% (16,300).
- Note: these bar prices are themselves distribution-adjusted (pre-ex-date), so
  the trigger comparison is internally consistent within the adjusted series —
  but the absolute price level carries the Check-1 adjustment.

### Tower sell — fill possibility

- ETF primary slip 0: **2026-10-02 09:07 (minute 547), 708 shares @ 15,490**.
  Bar: O 15,480 / H 15,520 / L 15,465 / C 15,510, V 319,702. Trigger 15,490 is
  inside [15,465, 15,520] → touched.
  (Correction: the brief said 709 shares — that was the UNROUNDED run's F of
  1,418 shares. The tick variant's F is smaller; the trade log shows 708.)
- ETF nearest slip 0.05% (tower 1 in the new cells): 2026-10-06 09:08
  (minute 548), 704 shares @ 15,555. Bar: O 15,525 / H 15,585 / L 15,520 /
  C 15,585, V 304,453. Trigger 15,555 inside [15,520, 15,585] → touched.
- Volume/queue at the trigger price is unmodeled for both.

---

## Check 3 — 000660 state-wise orderability: actual vs synthetic

Setting: 000660, fixed TP 3.0%, portions 10/12/15/20, minute bars.
All 4 portions re-run this session and reproduce `orderable_portions.csv`
exactly (ret to 1e-9, unaffordable, buys). Sources: verify3 buy logs,
verify4 conditional files, verify2 orderable file (minute_fixed_tp=3.0 rows).

### State-wise table — `statewise_orderability.csv`

| portion | amount/buy | flat min | first-buy attempts (flat) | first-buy successes (flat) | of which in window 05-26~07-13 | add-buy attempts (F open) | unaff. failure evals (flat) | unaff. counter | cond. unbuyable min (low) | cond. unbuyable min while flat | cond. unbuyable min while flat & eligible | depletion | no_cash | shares_blocked | late_depl | tower_ties |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 10% | 2.0M | 12,391 | 28,329 | 40 | **5** | 188 (all ok) | 28,289 | 7,882 | 12,700 | 12,180 | 7,875 | 0 | 0 | 0 | 0 | 0 |
| 12% | 2.4M | 94 | 20 | 20 | 0 | 87 (all ok) | 0 | 0 | 4,292 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 15% | 3.0M | 16 | 3 | 3 | 0 | 26 (all ok) | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 20% | 4.0M | 16 | 3 | 3 | 0 | 25 (all ok) | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

Synthetic reopen (verify5, SEPARATE — never merged with actual): 12% window
failures 8,983, counter 2,477, depletion 0; 15% control 0 failures, depletion 0,
**71 shares_blocked on 2026-07-14 (synthetic only)**. 10% / 20% reopen not run.

### CORRECTION — the "228 successes in the window" arithmetic was wrong

The brief said: 28,517 attempts − 28,289 failures = 228 successes while flat
inside 2026-05-26~07-13. That is incorrect. 228 is the TOTAL successes across
the whole period, and it includes add-buys. The buy log
(`buylog_0660_p10.csv`, 56,806 rows: 28,517 buy_attempt + 28,289 failure) shows:

- Attempts 28,517 = 28,329 flat (first-buy) + 188 F-open (add-buy).
- Successes 228 = 40 flat + 188 open (all 188 add-buy attempts succeeded).
- Flat successes 40 = **5 in-window** + 35 outside the window.
- In-window attempts 28,307 = 28,294 flat + 13 open; in-window successes 18
  (5 flat + 13 open). All 28,289 failures are in-window, all unaffordable.
- Failure rows carry no flat flag (empty field); flat attribution follows
  verify4/verify5 (all failures were first-buy while flat).

The 5 in-window flat successes (`flat_successes_in_window.csv`):

| day | minute | price | note |
|---|---|---|---|
| 2026-06-08 | 575 | 1,965,000 | succeeded at first evaluation; no failure row this minute |
| 2026-06-08 | 667 | 2,000,000 | open failed (failure row at 2,001,000), bought at bar low 2,000,000 |
| 2026-06-08 | 800 | 2,000,000 | open failed, bought at bar low 2,000,000 |
| 2026-06-10 | 863 | 2,000,000 | open failed, bought at bar low 2,000,000 |
| 2026-07-13 | 609 | 1,999,000 | open failed (low 1,999,000), bought at bar low |

Four of the five are the verify4 "+4" minutes (open-point failure recorded, buy
succeeded later in the same bar at its low). The 575 success is the only
in-window flat buy that never failed first.

### Reading

- 10%: the counter (7,882) is the dedup of 28,289 failure evaluations; the
  "unbuyable at every path point" set is 7,878 minutes (7,875 eligible + 3
  became-flat-mid-minute) plus the 4 open-only failures above. Depletion is 0
  because unaffordable is excluded from depletion per the confirmed criterion.
- 12%: 4,292 conditional unbuyable minutes exist, but the engine was flat only
  94 minutes in the whole period — all before 2026-04-02 — so actual = 0. The
  synthetic reopen (separate column) shows what would have happened had F been
  empty: 8,983 failure evaluations, counter 2,477, depletion 0.
- 15%/20%: conditional 0 too — observed-peak only, slippage-0 basis.
- Actual shares_blocked / late_depletion / no_cash / tower_ties are 0 in all
  four actual runs.

---

## Verification

- Base reproduction: unrounded slip 0 liq_ret 0.047096719, buys 202, sells 191;
  slip 0.0025% liq_ret −0.029912301, buys 162, sells 152 — exact vs sweep.
- Tick variants reproduce verify5 slip-0 values (primary 0.048258229, 209/198;
  nearest 0.009014402, 209/203). Unrounded 0.05%/0.1% reproduce the sweep
  (0.034961716 / −0.032207626).
- 000660 portions reproduce `orderable_portions.csv` exactly (ret to 1e-9,
  unaffordable, buys; asserts in `run_check3.py`).
- Flip-date computation reproduces the 43.63% / 11.38% overall off-grid shares.
- Factor-match decimals differ ~0.1%p from the earlier Check-1 run
  (98.40% vs 98.49% for 229200; 98.94% vs 99.04% for 132030) — same
  conclusion, likely a slightly different pre-period/rounding convention;
  values reported here are this run's own computation.
- Repo untouched: `github-public` HEAD 4a63db4, `git status` clean.

## What remains undone

- Full minute grid (1,008 settings × 4 tickers) — still not run.
- Original-engine live-loop replay on the same minute bars — still not done.
- Kiwoom's exact distribution-adjustment reference price/formula — unconfirmed
  (empirical factors 0.9988 / 0.9951 are precisely determined but the naive
  1−D/close formula does not reproduce them).
- Whether to unadjust the Kiwoom ETF bars (or use only the clean post-flip
  period) — decision pending (user/CereFin).
- 132030 pay-date discrepancy between sources (2026-01-02 vs 2026-01-05) —
  immaterial, unresolved.
- 10% / 20% synthetic reopen not run (only 12% + 15% control exist, from verify5).
