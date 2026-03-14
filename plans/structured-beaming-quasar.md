# USDペア CS品質改善計画

## Context

USDペア6通貨のバックテストで、CS>=17の通過率はJPYペアより高い(20.6% vs 15.3%)にもかかわらず、同じCS値での勝率がJPYより低い(CS17帯: USD 71.8% vs JPY 78.1%)。CSがシグナル品質を正確に反映できていない。

根本原因: `macd_hist_slope`の符号判定が微小値でランダムに+2.5/-2.0を付与し、CSをinflateするが予測力がない。加えてUSDペアのsignal/filter設定が未チューニング。さらに、スコアリングロジック内に通貨ペア別に調整できないハードコード定数が多数存在する。

## 調査結果: ペア別設定不可能なハードコード定数

### 影響度「高」（価格スケール・ボラティリティ依存）

| 定数 | ファイル:行 | 現在値 | 問題 |
|------|-----------|--------|------|
| macd_hist_slope閾値 | timeframe_evaluator.py:348 | `> 0` | USDペアでノイジー判定 |
| macd_slope bonus/penalty | timeframe_evaluator.py:349,357 | `+2.5/-2.0` | ペア別の重み調整不可 |
| HTF整合ボーナス | timeframe_evaluator.py:738-742 | `4.0/2.0` | ペア別の信頼度反映不可 |
| macd_norm_factor | config.py:25 | `0.5` | USDペアで事実上ゼロ（※CSには非影響） |

### 影響度「中」（ペアの値動き特性依存）

| 定数 | ファイル:行 | 現在値 | 問題 |
|------|-----------|--------|------|
| RSI極値排除 | timeframe_evaluator.py:317,326 | `80/20` | ペア別調整不可 |
| M1/M5最小ADX | timeframe_evaluator.py:852 | `10.0/8.0` | 低ボラUSDペアでM1過剰排除 |
| consensus一致率閾値 | consensus.py:190 | `0.50` | レンジ型ペアで影響大 |
| consensus競合比率 | consensus.py:213 | `0.60` | 同上 |

### 既存の伝搬メカニズム

YAML signal/filter/risk_mgmt → `bot_ovr` 辞書 → `_valid_fields` フィルタ → `UnifiedBotConfig`。
**UnifiedBotConfigのフィールド名と一致すれば、どのセクションに書いても適用される。**
ネストdataclass（`EvaluatorConfig`, `StrengthConfig`）は YAML 経由で設定不可能。

---

## Phase 1: スコアリングパラメータのコンフィグ化（コード変更）

### 設計方針
- 全パラメータを `UnifiedBotConfig` に追加（YAML signal セクションで上書き可能にする）
- デフォルト値 = 現在のハードコード値（JPYペアの動作を完全保持）
- USDペアのsignalセクションでのみ変更値を設定

### 1-A. `config.py` に追加するフィールド

```python
# --- MACDスロープスコアリング ---
# ATR比デッドゾーン（0.0=無効、0.01=ATR1%未満の変化を無視）
macd_slope_deadzone_atr_ratio: float = 0.0
# MACDスロープ順方向ボーナス
macd_slope_bonus: float = 2.5
# MACDスロープ逆方向ペナルティ（正の値で指定、内部で減算）
macd_slope_penalty: float = 2.0
# --- HTF整合スコアリング ---
# HTF強整合ボーナス（2TF以上一致）
htf_align_bonus_strong: float = 4.0
# HTF弱整合ボーナス（1TF一致）
htf_align_bonus_weak: float = 2.0
```

### 1-B. `EvaluatorConfig` への伝搬

`EvaluatorConfig` に同名フィールドを追加し、`get_evaluator_config()` で `UnifiedBotConfig` から伝搬:

```python
@dataclass(frozen=True)
class EvaluatorConfig:
    ...
    macd_slope_deadzone_atr_ratio: float = 0.0
    macd_slope_bonus: float = 2.5
    macd_slope_penalty: float = 2.0
    htf_align_bonus_strong: float = 4.0
    htf_align_bonus_weak: float = 2.0
```

`get_evaluator_config()` を更新して新フィールドを伝搬。

### 1-C. `timeframe_evaluator.py` の変更

#### `_calculate_score()` L343-361: MACDスロープ
```python
macd_hist_slope = row.get("macd_hist_slope")
if macd_hist_slope is not None and not pd.isna(macd_hist_slope):
    _slope_active = True
    # デッドゾーン判定
    atr = row.get("atr_14")
    dz = self.config.macd_slope_deadzone_atr_ratio
    if (dz > 0 and atr is not None
            and not pd.isna(atr) and atr > 0):
        if abs(macd_hist_slope) / atr < dz:
            _slope_active = False

    if _slope_active:
        if buy_score > 0 and macd_hist_slope > 0:
            buy_score += self.config.macd_slope_bonus  # 2.5 → config
            _bd_macd_slope = self.config.macd_slope_bonus
            reasons.append("MACD加速↑")
        elif sell_score > 0 and macd_hist_slope < 0:
            sell_score += self.config.macd_slope_bonus
            _bd_macd_slope = self.config.macd_slope_bonus
            reasons.append("MACD加速↓")
        elif buy_score > 0 and macd_hist_slope < 0:
            buy_score -= self.config.macd_slope_penalty  # 2.0 → config
            _bd_macd_slope = -self.config.macd_slope_penalty
        elif sell_score > 0 and macd_hist_slope > 0:
            sell_score -= self.config.macd_slope_penalty
            _bd_macd_slope = -self.config.macd_slope_penalty
```

#### `_score_htf_alignment()` L738-742: HTFボーナス
```python
if aligned_count >= 2:
    return self.config.htf_align_bonus_strong, f"HTF強整合({aligned_count}TF)"
elif aligned_count >= 1:
    return self.config.htf_align_bonus_weak, f"HTF整合({aligned_count}TF)"
return 0.0, ""
```

### 1-D. 変更ファイル一覧

| ファイル | 変更内容 |
|---------|---------|
| `autotrader/decision/unified/config.py` | EvaluatorConfig + UnifiedBotConfig にフィールド追加、get_evaluator_config() 更新 |
| `autotrader/decision/unified/scoring/timeframe_evaluator.py` | _calculate_score() と _score_htf_alignment() でconfig参照に変更 |
| `config/symbol_presets.yaml` | 変更なし（デフォルト値で既存動作保持） |

---

## Phase 2: BT検証（デッドゾーン閾値探索）

### Step 1: EURUSD単独で4値スイープ
code_dir: worktree, overrides.bot で設定:
- DZ-T1: `macd_slope_deadzone_atr_ratio: 0.005`
- DZ-T2: `macd_slope_deadzone_atr_ratio: 0.01`
- DZ-T3: `macd_slope_deadzone_atr_ratio: 0.02`
- DZ-T4: `macd_slope_deadzone_atr_ratio: 0.03`
- 期間: 2020-2025

### Step 2: 最適値で全6 USDペア検証
- BL (デッドゾーンなし) との比較: WR, PF, DD, トレード数
- JPYペアはデフォルト0.0（無効）のため回帰テスト不要

### Step 3: macd_slope_bonus/penalty, htf_align_bonus の調整検証
- デッドゾーン最適値を固定した上で、bonus/penalty 値を変更
- 例: `macd_slope_bonus: 1.5`（影響を半減）, `htf_align_bonus_strong: 3.0`

---

## Phase 3: USDペア signal/filter設定追加

デッドゾーン最適値を固定した上で、JPYペア最適化で確立されたパラメータを段階的に適用。

### 適用候補（優先順）
1. `volume_filter_penalty: 0.5` + `volume_filter_threshold: 1.0`
2. `trend_strength_max: 0.85`
3. `regime_trend_threshold_add: 2.0`
4. `penalty_cap: 0.5`（filterセクション）
5. `bca_min_edge` チューニング（0.60/0.65/0.70）
6. `base_risk_pct: 0.004`（DD2%目標、統一）

### 検証方法
- EURUSD + GBPUSD で各パラメータを個別検証
- 最良の組み合わせを全6 USDペアに横展開
- 最終: 12ALL multi_pair BT

---

## Phase 4: symbol_presets.yaml 反映

検証済みパラメータを各USDペアの `signal:` / `filter:` セクションに追加。
JPYペアと同じフォーマットでコメント付き。

---

## 実装順序

| # | 作業 | ファイル |
|---|------|---------|
| 1 | EvaluatorConfig にフィールド追加 | `config.py` |
| 2 | UnifiedBotConfig にフィールド追加 + get_evaluator_config() 更新 | `config.py` |
| 3 | _calculate_score() でconfig参照に変更 + デッドゾーン追加 | `timeframe_evaluator.py` |
| 4 | _score_htf_alignment() でconfig参照に変更 | `timeframe_evaluator.py` |
| 5 | worktreeでコミット → PR → マージ | - |
| 6 | BT検証ジョブ投入 (Phase 2) | `backtest_queue.json` |
| 7 | 結果分析 → 最適値決定 | - |
| 8 | signal/filter設定 BT検証 (Phase 3) | `backtest_queue.json` |
| 9 | 最終パラメータをYAMLに反映 → PR | `symbol_presets.yaml` |

## 検証基準

- CS17帯WR: 71.8% → 76%以上（JPY同等）
- PF: 改善 or 維持
- DD: 悪化しない
- JPYペア: デフォルト値で既存動作完全保持（回帰テスト不要）
- 月+率: 維持 or 改善

## 注意事項
- 全パラメータのデフォルト値 = 現在のハードコード値（JPYペアに影響なし）
- USDペアのみsignal設定で有効値を指定する運用
- `macd_norm_factor` はCSに影響しないため今回の対象外（IndicatorStrengthはSL/TP計算にのみ使用）
- Phase 2以降はBT結果次第で計画を調整
