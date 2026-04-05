# PM Deep Analysis: AutoTraderV4 8-Pair BT 2020-2025 (5,343 trades / 4,603 original trades)

## Trade Structure

The 5,343 rows represent 4,603 original trades:
- **740 trades** reached 1R and were split via 50% partial close into 2 entries:
  - TP_1R (740 rows): the 50% partial close portion at 1R
  - Remainder (740 rows): the remaining 50% lot (exits as SL_HIT=461, TP_HIT=276, EDGE_DECAY=3)
- **3,863 trades** never reached 1R (single entry each)

---

## 1. EXIT REASON ANALYSIS

| exit_reason | count | pct% | WR% | avg_pips | total_PL | avg_hold_min |
|---|---|---|---|---|---|---|
| SL_HIT | 2,531 | 47.4% | 88.9% | +6.5 | +3,443,415 | 41.0 |
| EDGE_DECAY | 1,685 | 31.5% | 61.1% | +0.3 | +140,512 | 38.4 |
| TP_1R | 740 | 13.9% | 100.0% | +20.5 | +1,925,415 | 32.4 |
| TP_HIT | 318 | 6.0% | 100.0% | +29.9 | +1,345,154 | 32.1 |
| STAGNATION | 65 | 1.2% | 0.0% | -8.0 | -128,833 | 83.2 |
| WEEKEND | 4 | 0.1% | 25.0% | -0.5 | -1,292 | 182.5 |

### P/L Contribution
| exit_reason | total_PL | share |
|---|---|---|
| SL_HIT | +3,443,415 | 51.2% |
| TP_1R | +1,925,415 | 28.6% |
| TP_HIT | +1,345,154 | 20.0% |
| EDGE_DECAY | +140,512 | 2.1% |
| STAGNATION | -128,833 | -1.9% |
| WEEKEND | -1,292 | 0.0% |

**Key finding**: SL_HITの高WR(88.9%)はトレーリング/BE移動の結果。SL_HITの大半は利益確定exit。EDGE_DECAYはP/L貢献が2.1%と小さく、本質的には損失軽減（ダウンサイド保護）の機能を果たしている。

---

## 2. MFE UTILIZATION ANALYSIS

### Winning trades profit capture
- Winning trades: 4,337 (with MFE>0: 3,597)
- **Avg MFE: 16.5 pips, Avg realized: 9.2 pips**
- **Avg capture ratio: 46.1%** (MFEの半分以下しか捕捉できていない)
- Unrealized pips total: 26,173 pips

### Post-partial-close remaining lot (SL_HIT winners with MFE >= 2R)
| MFE range | count | avg_pips | avg_MFE | avg_capture |
|---|---|---|---|---|
| [1.0R, 1.5R) | 743 | +14.6 | 20.0 | ~73% |
| [1.5R, 2.0R) | 809 | +9.4 | 17.2 | ~55% |
| [2.0R, 3.0R) | 469 | +6.1 | 15.8 | ~39% |
| **[3.0R, 5.0R)** | **188** | **+3.8** | **16.3** | **~23%** |
| **[5.0R, 100R)** | **40** | **+1.8** | **15.9** | **~11%** |

**Problem**: MFE >= 3Rに達した228トレードで平均キャプチャ率はわずか20.9%。合計2,923 pipsが未実現。トレーリングストップが遅すぎるか、ATR倍率が大きすぎてMFEピークから大きく戻される。

### Reversal Losses (MFE > 2*SL but SL_HIT)
- 715 trades (13.4%): MFE > 2*SLに達したのにSL_HITで終了
- Avg MFE: 15.8 pips, Avg realized: +5.0 pips (利益は出ているが大幅に減少)
- Lost potential: 7,743 pips
- GBPJPY(172), CHFJPY(142), EURJPY(130)に集中

---

## 3. MAE ANALYSIS

### All trades
| stat | mae_pips |
|---|---|
| mean | 5.7 |
| median | 3.7 |
| p75 | 7.3 |
| p90 | 14.0 |
| p95 | 20.0 |
| max | 53.2 |

### Losing trades MAE/SL ratio
- **MAE > 95% of SL: 292 (29.0%)** -- 約3割の負けトレードがSLギリギリまで逆行
- MAE > 80% of SL: 319 (31.7%)
- **MAE < 50% of SL: 569 (56.6%)** -- 過半数はSLの半分以下で損切り(EDGE_DECAY効果)

EDGE_DECAYが効いている。656件のEDGE_DECAY負けトレードの平均pipsは-1.8、もしSLまで待てば-20.4。**12,178 pipsの損失を回避**。

---

## 4. HOLDING TIME ANALYSIS

### Distribution
| percentile | minutes |
|---|---|
| 10% | 12 |
| 25% | 19 |
| 50% | 30 |
| 75% | 51 |
| 90% | 77 |
| 95% | 96 |

### By quintile
| quintile | count | range | WR% | avg_pips |
|---|---|---|---|---|
| Q1 (shortest) | 1,171 | 0-17m | 80.7% | +8.5 |
| Q2 | 1,058 | 18-25m | 83.5% | +8.1 |
| Q3 | 977 | 26-36m | 86.2% | +8.6 |
| Q4 | 1,098 | 37-58m | 82.1% | +7.5 |
| **Q5 (longest)** | **1,039** | **59-448m** | **73.6%** | **+5.7** |

- **Long holds (>120m)**: 105 trades, WR=72.4%, avg_pips=+4.0 -- 収益性が低い
- **Short holds (<10m)**: 316 trades, WR=81.6%, avg_pips=+10.3 -- 良好
- Pearson r = -0.089 (p<0.001) -- 保有時間が長いほど微減だが有意

**Finding**: Q5(最長保有)のWR低下・pips低下が顕著。120分超のSL_HIT(60件)やEDGE_DECAY(31件)は改善余地あり。

---

## 5. SL SETTING ANALYSIS

### SL pips by symbol
| symbol | count | mean_sl | median_sl | min_sl | max_sl | p10 | p90 |
|---|---|---|---|---|---|---|---|
| GBPJPY | 1,004 | 14.8 | 14.2 | 0.0 | 46.2 | 5.0 | 22.9 |
| CHFJPY | 909 | 14.2 | 14.1 | 0.4 | 50.0 | 4.4 | 21.0 |
| EURJPY | 860 | 14.3 | 14.2 | 0.0 | 50.0 | 4.4 | 20.3 |
| AUDJPY | 803 | 14.6 | 16.2 | 0.4 | 31.0 | 5.6 | 20.0 |
| CADJPY | 617 | 15.4 | 20.0 | 1.0 | 36.7 | 6.6 | 20.0 |
| USDJPY | 482 | 15.2 | 17.5 | 0.2 | 36.4 | 5.6 | 20.0 |
| GBPUSD | 365 | 13.2 | 12.7 | 0.5 | 29.6 | 4.1 | 20.0 |
| EURUSD | 303 | 13.8 | 14.0 | 1.0 | 25.5 | 5.0 | 20.0 |

### Winners vs Losers SL
| | count | mean_sl | median_sl |
|---|---|---|---|
| **Win** | 4,337 | **13.3** | **12.3** |
| **Loss** | 1,006 | **19.8** | **20.0** |

**Critical finding**: 負けトレードのSLは勝ちトレードの1.49倍。SLが広い（20pips=max cap）トレードほど負けやすい。ATRが大きい環境でのエントリーがSLを広げ、それが不利に働いている可能性。

### SL_HIT with prior MFE
- **SL_HIT 2,531件中 2,350件(92.8%) がMFE > 5pipsを経験**
- これらの平均MFE=17.4, avg MAE=6.1, avg SL=10.9
- ほぼ全てのSL_HITが「一度は利益方向に動いた後」にSLに引っかかっている
- 70.4% of SL_HITs have MAE < 95% of SL (BE/トレーリングで移動されたSLにヒット)

---

## 6. REVERSAL LOSS DEEP DIVE (MFE > SL, P/L < 0)

| metric | value |
|---|---|
| Total reversal losses | 30 trades (0.56%) |
| Total P/L | -43,932 |
| Avg MFE | 9.1 pips |
| Avg MAE | 12.3 pips |
| Avg SL | 2.0 pips |

ほぼ全て(29/30)がSL_HIT exit。極端に狭いSL(avg 2.0 pips)で発生。

### By Regime
| regime | count | total_PL |
|---|---|---|
| BREAKOUT | 15 | -22,596 |
| RANGE | 7 | -5,553 |
| TREND | 7 | -15,405 |
| CHOPPY | 1 | -378 |

### By Hour (UTC)
UTC 16時(6件), 10時(5件), 17時(5件)に集中。

---

## 7. EXIT TIMING OPTIMALITY

### EDGE_DECAY (1,685 trades)
- WR: 61.1%, avg pips: +0.3
- **Winners (1,029)**: avg pips=+1.6, avg MFE=8.5 --> capture ratio 18.8%
- **Losers (656)**: avg pips=-1.8, avg MFE=5.1
- Losers saved 12,178 pips vs hitting SL
- Winners left 7,138 pips on the table
- **NET: +5,040 pips saved (EDGE_DECAY is net positive)**

### EDGE_DECAY by Regime
| regime | count | WR% | avg_pips | avg_MFE_R |
|---|---|---|---|---|
| RANGE | 1,051 | 58.5% | +0.1 | 0.86 |
| BREAKOUT | 307 | 72.0% | +1.0 | 1.06 |
| TREND | 163 | 62.6% | +0.6 | 0.46 |
| CHOPPY | 159 | 54.7% | -0.3 | 0.29 |

**CHOPPY**: WR最低(54.7%)、avg_pips=-0.3。EDGE_DECAYの閾値をCHOPPYでは緩めるべきか要検討。

### EDGE_DECAY by Holding Time
| time | count | WR% | total_PL |
|---|---|---|---|
| **0-15m** | **196** | **47.4%** | **-9,586** |
| 15-30m | 676 | 64.6% | +97,613 |
| 30-60m | 519 | 61.8% | +42,789 |
| 60-120m | 260 | 60.8% | +9,906 |
| 120m+ | 34 | 58.8% | -210 |

**Problem**: 15分未満のEDGE_DECAYはWR 47.4%でP/L=-9,586。min_bars=5は約5分(M1)に相当し、短すぎる可能性。最低保有時間を10-15分に引き上げるべき。

### STAGNATION (65 trades)
- **WR: 0.0%** (全件負け)
- avg pips: -8.0, avg hold: 83.2m
- TREND(20): avg -9.1, avg hold 62.4m
- RANGE(43): avg -7.5, avg hold 93.2m

Stagnation exitは設計通り（進捗なしトレードの損切り）だが、TREND 62.4分で発動=TREND stagnation 90分の設定からエッジ劣化連携で短縮されている。

### True SL_HIT Losers (never reached 1R, 282 trades)
- avg pips: -19.7, avg SL: 18.4, avg MFE: 4.4, avg MAE: 21.8
- **MFE < 0.1R: 84 (29.8%)** -- 完全な悪エントリー（PM改善の余地なし）
- MFE 0.1-0.5R: 154 (54.6%) -- 一部利益方向に動いたが不十分
- MFE >= 0.5R: 44 (15.6%) -- trailing/BE開始したが保護失敗
- total P/L: -1,296,141

### Non-split SL_HIT Winners (1,788 trades)
BE/trailing移動後にSLヒットで小利益確定。MFE分布:

| MFE range | count | avg_pips | avg_MFE |
|---|---|---|---|
| [1.0R, 1.5R) | 425 | +10.7 | 15.4 |
| [1.5R, 2.0R) | 686 | +8.4 | 15.5 |
| **[2.0R, inf)** | **677** | **+5.0** | **15.6** |

**Problem**: MFE >= 2Rに達した677トレードの平均realized=+5.0 pips。MFEの平均15.6 pipsの32%しか捕捉できていない。トレーリングが広すぎてMFEピークから大幅にリトレースされている。

---

## 8. REGIME-LEVEL PM EFFICIENCY

| regime | count | WR% | avg_pips | avg_hold | SL_HIT% | EDGE_DECAY% | STAG% |
|---|---|---|---|---|---|---|---|
| RANGE | 3,072 | 79.0% | +6.9 | 40.0m | 46.2% | 34.2% | 1.4% |
| BREAKOUT | 1,422 | 89.5% | +10.4 | 34.4m | 51.8% | 21.6% | 0.0% |
| TREND | 489 | 74.2% | +5.6 | 43.1m | 47.6% | 33.3% | 4.1% |
| CHOPPY | 334 | 75.7% | +5.8 | 44.9m | 40.4% | 47.6% | 0.6% |
| HIGH_VOL | 24 | 87.5% | +16.3 | 37.2m | 29.2% | 16.7% | 0.0% |

**CHOPPY EDGE_DECAY 47.6%**: CHOPPY環境ではほぼ半数がEDGE_DECAYで決済。スコアが安定しないCHOPPY環境でエッジ劣化が過剰発火している可能性。

---

## IDENTIFIED PM PROBLEMS (Improvement Potential Order)

### HIGH PRIORITY

#### H1. トレーリングストップのMFE捕捉効率が低い
- **データ**: MFE >= 2Rのトレード(non-split)677件で平均capture 32%
- **データ**: MFE >= 3Rの228件で平均capture 20.9%, 未実現2,923 pips
- **原因**: `trailing_atr_multiplier=2.0`が広すぎ。Stage2(1.2R, ATR x1.2)も引き締めが遅い
- **設定**: `trailing_start_r=0.5`, `trailing_stage2_r=1.2`, `trailing_stage2_atr_multiplier=1.2`
- **改善案**: 
  - Stage2開始をを1.0Rに前倒し（1.2Rは遅すぎる。1Rで50%利確後すぐにStage2が欲しい）
  - Stage2 ATR倍率を1.0に引き締め（現在1.2）
  - Stage3を有効化（1.5R, ATR x0.7）で高R領域の捕捉率向上
- **推定改善**: 677件 x 平均5pips改善 = ~3,385 pips, 228件の高MFE x 3pips = ~684 pips追加

#### H2. EDGE_DECAY 15分未満の早期発動がnet negative
- **データ**: 196件, WR=47.4%, P/L=-9,586
- **原因**: `edge_decay_exit_min_bars=5`（M1で約5分）が短すぎ
- **設定**: `edge_decay_exit_min_bars: 5`
- **改善案**: `edge_decay_exit_min_bars`を10-15に引き上げ（10-15分の猶予）
- **推定改善**: P/L +9,586（net negative解消）

#### H3. SLが広いトレード(20 pips cap)の勝率低下
- **データ**: 負けトレードの平均SL=19.8 vs 勝ちトレードの平均SL=13.3（1.49倍）
- **原因**: ATRが大きい環境でsl_max_pips_default=50にキャップされているが、sl_min=20でフロアが高い
- **設定**: `sl_min_pips: 20.0`, `sl_max_pips_default: 50.0`, symbol presets `default_sl_pips`
- **改善案**:
  - SLが広い環境（高ATR）ではロットを追加削減するか、エントリーを厳格化
  - SL > 18pipsの場合にconsensus_thresholdを+1する等のガード
- **根拠**: True SL losers 282件の77%がSL >= 15pips。全体平均の2倍の損失

### MEDIUM PRIORITY

#### M1. CHOPPY環境でのEDGE_DECAY過剰発火
- **データ**: CHOPPY 334件中47.6%がEDGE_DECAY、WR=54.7%
- **原因**: CHOPPYはスコアが方向転換しやすく、エッジ劣化が誤発火
- **改善案**: CHOPPY時のedge_decay_exit_thresholdを0.40→0.55に緩和
- **推定改善**: CHOPPY EDGE_DECAY 159件 x WR改善 = ~10-20件の負け回避

#### M2. Non-splitトレードのBE/トレーリングSLヒット後の小利益
- **データ**: Non-split SL_HIT winners 1,788件のavg pips=+7.7、但しavg MFE=15.5
- **原因**: early_breakeven_r=0.6でBE移動後、0.6R-1.0R間でSLにヒット
- **改善案**: 
  - early_breakeven_rを0.7Rに引き上げ（ノイズによるBEヒットを削減）
  - be_cushion_pipsを3.0→4.0に拡大
- **リスク**: BE移動が遅れることで一部の利益保護が効かなくなる

#### M3. STAGNATION exitが全敗（65件、P/L=-128,833）
- **データ**: 全65件がloss、avg pips=-8.0
- **原因**: stagnation発動時点で既に含み損が大きい（avg MAE=13.3）
- **改善案**:
  - stagnation_min_mfe_rを0.10→0.15に緩和（MFE 0.10-0.15Rのトレードを救済）
  - TREND stagnation時間を90分→75分に短縮（TREND avg hold=62.4mで発動=エッジ劣化短縮済み）
- **注意**: STAGNATIONは損切り機構なので全敗は設計通り。問題は損失額の大きさ

### LOW PRIORITY

#### L1. 15分以上のEDGE_DECAY winners改善余地
- EDGE_DECAY winners 1,029件のMFE capture=18.8%（avg MFE=8.5, avg realized=1.6）
- これは本質的にtrade-off: 早期exitの代償として利益が小さい
- EDGE_DECAYはnet positive(+5,040 pips)なので、現設定は概ね妥当

#### L2. WEEKEND_CLOSEの最適化
- わずか4件。統計的意味なし。現設定で問題なし

---

## CONFIG vs DATA CROSS-REFERENCE

| PM設定 | 現在値 | データからの所見 |
|---|---|---|
| `partial_close_1r_ratio` | 0.50 | 740件で1R利確。これ自体は良好 |
| `partial_close_2r_ratio` | 0.05 | CSV上に2R partial close rowなし(TP_2Rなし) -- 2R到達自体が少ない |
| `breakeven_at_1r` | true | 1R後BE移動は機能している |
| `trailing_start_r` | 0.5 | 0.5Rで開始は妥当 |
| `trailing_atr_multiplier` | 2.0 | **広すぎ -- MFE捕捉率低下の主因** |
| `trailing_stage2_r` | 1.2 | **遅すぎ -- 1.0Rに前倒しすべき** |
| `trailing_stage2_atr_multiplier` | 1.2 | **緩すぎ -- 1.0に引き締め検討** |
| `trailing_stage3_enabled` | false | **有効化すべき（1.5R, ATR x0.7）** |
| `early_breakeven_r` | 0.6 | 概ね妥当だが0.7Rでもテストの価値あり |
| `be_cushion_pips` | 3.0 | 妥当 |
| `edge_decay_exit_threshold` | 0.40 | 妥当（net positive） |
| `edge_decay_exit_min_bars` | 5 | **短すぎ -- 10-15に引き上げ** |
| `stagnation_exit_minutes` | 120.0 | RANGE/CHOPPYでは妥当、TRENDは90分(短縮あり) |
| `stagnation_min_mfe_r` | 0.10 | STAG全敗だが設計通り |
| `edge_decay_stagnation_multiplier` | 0.65 | TREND stagnation短縮に効いている |

---

## RECOMMENDED BACKTEST QUEUE

以下の優先順位で検証:

1. **H1**: `trailing_stage2_r: 1.0`, `trailing_stage2_atr_multiplier: 1.0`, `trailing_stage3_enabled: true`
2. **H2**: `edge_decay_exit_min_bars: 12`
3. **H1+H2**: 合成テスト
4. **M1**: CHOPPY限定 `edge_decay_exit_threshold` 緩和（要コード変更）
5. **H3**: SL >= 18pips時のリスク削減ロジック追加（要コード変更）
