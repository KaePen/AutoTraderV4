# バックテスト回帰修正プラン

## Context

A1-A6修正の実装後、バックテスト結果が大幅に悪化した。

| 指標 | 修正前 | 修正後 | 変化 |
|------|--------|--------|------|
| 取引数 | 2930 (244/月) | 2257 (188/月) | -23% |
| 勝率 | 56.04% | 42.3% | **-13.7pt** |
| 純利益 | +2,300 | -263,145 | **大幅悪化** |
| 最大DD | — | 29.02% | — |
| シャープ | — | -2.556 | — |

3名のエージェントによる調査の結果、A2/A3/A4/A5の4修正が複合的に悪影響を及ぼしていることが判明。
A1（SELLトレーリング修正）とA6（クールダウン修正）は正当なバグ修正のため保持する。

---

## 根本原因

### 1. A2: evaluatorのTP=SL×1.0固定 → A5の実効RRフィルターでほぼ全トレード拒否
- evaluatorが `tp_pips = sl_pips * 1.0` を返す
- base.pyでtp_sl_ratio_rangeにクランプ → Scalp min_ratio=1.0なのでTP変わらず
- A5フィルター: cost=2.0pips → effective_tp=18, effective_sl=22, 比率0.82 < 1.0 → **拒否**

### 2. A3: base.pyのHTF factor=0.0が全トレード完全ブロック
- 全戦略htf_weight>=0.5 → HTF1つ逆行でedge_score=0.0
- 修正前の-5.0ペナルティは「スコア低下」、修正後の0.0は「完全ブロック」

### 3. A4: MODE_THRESHOLDS 2倍化で達成不可能な閾値
- SWING閾値8.0に対し最大可能スコア~6.5 → **物理的に到達不可**
- SCALPING閾値5.0に対し最大~7.0 → 71%のTFが満点必要

### 4. A5: 実効TP/SL比率<1.0で拒否 → A2との複合で壊滅的

---

## 修正内容（5項目）

### Step 1: A3修正 — HTF factor緩和（最重要）

**ファイル**: `src/autotrader/decision/unified/strategies/base.py`
**メソッド**: `_calculate_htf_factor()` (L276-278)

```python
# 変更前
if conflict_count > 0 and htf_weight >= 0.5:
    return 0.0

# 変更後
if conflict_count > 0:
    if conflict_count >= 2:
        # 複数HTFが逆行 → 完全ブロック
        return 0.0
    # 単一HTF逆行 → 減衰（htf_weightに応じて）
    decay = 0.5 - htf_weight * 0.25
    return max(0.3, decay)
```

**効果**: 単一HTF逆行時、edge_score=0.0→0.3-0.4に。複数逆行は引き続きブロック。

---

### Step 2: A2修正 — evaluator TP計算をTF別デフォルトに復元

**ファイル**: `src/autotrader/decision/unified/timeframe_evaluator.py`
**メソッド**: `_calculate_sl_tp()` (L722付近)

```python
# 変更前
tp_pips = sl_pips * 1.0

# 変更後
# TF別デフォルトTP/SL比率（戦略のtp_sl_ratio_rangeで最終補正される）
_DEFAULT_TP_RATIOS = {
    "M1": 1.2, "M5": 1.3, "M15": 1.4,
    "H1": 1.5, "H4": 1.6, "D1": 1.8,
}
tp_ratio = _DEFAULT_TP_RATIOS.get(self.timeframe, 1.4)
tp_pips = sl_pips * tp_ratio
```

**設計意図**: ハードコード辞書の復元だが、以前と異なり「デフォルト値」として機能。base.pyのtp_sl_ratio_rangeが最終的にクランプするため二重設定にはならない。

---

### Step 3: A4修正 — MODE_THRESHOLDS中間値に

**ファイル**: `src/autotrader/decision/unified/mode_aware_consensus.py`

MODE_THRESHOLDS (L130):
| モード | 修正前(元) | 現在 | 新値 |
|--------|-----------|------|------|
| SCALPING | 2.5 | 5.0 | **3.5** |
| DAY_TRADE | 3.5 | 6.0 | **4.5** |
| SWING | 4.0 | 8.0 | **6.0** |

ConsensusConfig defaults:
- `scalping_threshold`: 5.0 → **3.5**
- `day_trade_threshold`: 6.0 → **4.5**
- `swing_threshold`: 8.0 → **6.0**

---

### Step 4: A4修正 — min_confidence中間値に

| ファイル | 戦略 | 修正前(元) | 現在 | 新値 |
|---------|------|-----------|------|------|
| `strategies/scalp.py` | Scalp | 0.22 | 0.30 | **0.25** |
| `strategies/short_mid.py` | ShortMid | 0.25 | 0.35 | **0.30** |
| `strategies/swing.py` | Swing | 0.28 | 0.40 | **0.35** |

---

### Step 5: A5修正 — 実効TP/SL比率閾値を緩和

**ファイル**: `src/autotrader/decision/unified/strategies/base.py`
**メソッド**: `_calculate_sl_tp()` (L393付近)

```python
# 変更前
if effective_sl > 0 and effective_tp / effective_sl < 1.0:
    return None

# 変更後
if effective_sl > 0 and effective_tp / effective_sl < 0.8:
    return None
```

**根拠**: 勝率56%×RR0.8 = 期待値+0.008/トレード（微小プラス）。完全に1未満でも勝率で補える。

---

## テスト修正

Step 3/4の閾値変更に伴い、以下のテストのアサーション値を更新:

- `tests/unit/decision/unified/test_mode_aware_consensus.py` — 閾値アサーション
- `tests/unit/backtest/test_mode_aware_parallel.py` — シグナル強度（必要に応じて）

---

## 修正対象ファイル一覧

| ファイル | Step | 変更内容 |
|---------|------|---------|
| `src/autotrader/decision/unified/strategies/base.py` | 1,5 | HTF factor緩和、実効RR閾値0.8 |
| `src/autotrader/decision/unified/timeframe_evaluator.py` | 2 | TF別デフォルトTP比率復元 |
| `src/autotrader/decision/unified/mode_aware_consensus.py` | 3 | MODE_THRESHOLDS + ConsensusConfig |
| `src/autotrader/decision/unified/strategies/scalp.py` | 4 | min_confidence=0.25 |
| `src/autotrader/decision/unified/strategies/short_mid.py` | 4 | min_confidence=0.30 |
| `src/autotrader/decision/unified/strategies/swing.py` | 4 | min_confidence=0.35 |
| `tests/unit/decision/unified/test_mode_aware_consensus.py` | 3 | アサーション更新 |

---

## 検証方法

1. `pytest tests/ -x` で全242テストパス確認
2. バックテスト実行: `.venv/bin/python scripts/run_backtest.py --years 2023`
3. 期待結果:
   - 取引数: 200-300/月（修正前244/月に近い）
   - 勝率: 54-58%（修正前56%に近い）
   - PF: 1.0-1.10（修正前1.01以上）
   - 最大DD: 15-20%以下
