# Plan: 設定ファイル3分割リファクタリング（ハードコード値の集約含む）

## Context

### 課題
設定値が以下の3箇所に散在しており、チューニング時にどこを変えるか分からない:
1. `symbol_presets.yaml` — signal/risk_mgmt/filter/pm_config のうち一部のみYAML化
2. Python dataclass のデフォルト値 — スコアリング係数・段階的制御・EdgeValidator・PositionSizer等
3. `live_trading.yaml` / `demo_trading.yaml` — モード別設定が分離

### ゴール
「パラメータを変えたければYAMLだけ見れば良い」状態にする。
3ファイルに集約し、Python側のデフォルト値はあくまでフォールバックに格下げ。

---

## 3ファイル構成

```
config/
  trading_defaults.yaml    # ① 全トレードロジックのグローバルデフォルト
  symbol_overrides.yaml    # ② 通貨ペア別設定（①をオーバーライド）
  modes.yaml               # ③ その他設定（デモ/ライブ差分）
  accounts.yaml            # 変更なし
```

---

## ① trading_defaults.yaml（新規作成）

`symbol_presets.yaml` のグローバルセクション + ハードコード中のパラメータを追加。

```yaml
# === シグナル生成・スコアリング ===
signal:
  # 既存（symbol_presets.yaml から移動）
  consensus_threshold: 18.0
  consensus_primary_weight: 2.0
  consensus_entry_weight: 1.5
  consensus_confirm_weight: 3.0
  consensus_manage_weight: 0.5
  consensus_other_weight: 1.0
  bca_enabled: true
  bca_min_edge: 0.60
  bca_penalty_scale: 1.0
  htf_score_filter_enabled: true
  htf_score_filter_min_alignment: 0.1
  htf_score_filter_threshold_add: 1.0
  macd_slope_filter_threshold: -2.0
  trend_strength_max: 0.6
  regime_trend_threshold_add: 1.5
  # NEW: MACDスロープスコアリング係数（config.py からYAML化）
  macd_norm_factor: 0.5
  macd_slope_bonus: 2.5
  macd_slope_penalty: 2.0
  # NEW: HTF整合スコアリング係数（config.py からYAML化）
  htf_align_bonus_strong: 4.0
  htf_align_bonus_weak: 2.0
  # NEW: ブレイクアウト設定（config.py からYAML化）
  regime_breakout_lookback: 20
  regime_breakout_tp_multiplier: 1.5
  # NEW: ボラティリティ検出閾値（config.py からYAML化）
  vol_expanding_threshold: 0.3
  vol_compressing_threshold: -0.2
  vol_expanding_penalty: 0.1
  # NEW: Choppy検出（config.py からYAML化）
  choppy_ci_threshold: 61.8
  choppy_threshold_add: 3.0
  # NEW: M1実行ゲート（config.py からYAML化）
  m1_exec_gate_bb_low: 0.3
  m1_exec_gate_bb_high: 0.7

# === リスク管理 ===
risk_mgmt:
  sl_min_pips: 20.0
  sl_max_pips_default: 50.0
  default_tp_sl_ratio_range: [1.1, 1.4]
  use_dynamic_lot: true
  slippage_buffer_pips: 2.0

# === フィルター ===
filter:
  range_day_bbw_threshold: 0.25
  range_day_score_premium: 0.3
  weak_hours_enabled: true
  weak_hours_score_premium: 0.5
  sg_spread_penalty_rate: 0.2
  sg_off_hours_penalty: 0.5
  sg_volatility_penalty: 0.05
  sg_recent_loss_penalty: 0.1
  sg_penalty_hours: [15]
  sg_penalty_hours_value: 0.3
  regime_threshold_enabled: true

# === ポジション管理 ===
pm_config:
  # 既存（symbol_presets.yaml から移動）
  partial_close_1r_ratio: 0.50
  partial_close_2r_ratio: 0.05
  breakeven_at_1r: true
  trailing_start_r: 0.5
  trailing_atr_multiplier: 2.0
  early_breakeven_r: 0.3
  signal_rev_close_ratio: 0.0
  range_day_be_disabled: true
  range_day_early_be_r: 0.3
  range_day_fast_be_enabled: true
  range_day_fast_be_minutes: 90.0
  stagnation_exit_minutes: 120.0
  stagnation_min_mfe_r: 0.10
  be_cushion_pips: 3.0
  range_day_insurance_enabled: false
  insurance_trigger_r: 1.0
  insurance_block_high_mfe_r: 0.8
  insurance_min_holding_minutes: 15.0
  range_day_half_r_partial_enabled: false
  range_day_half_r_partial_ratio: 0.20
  range_day_half_r_trigger: 0.5
  early_profit_guard_enabled: true
  stag_pretighten_enabled: true
  # NEW: 段階的トレーリング（position_manager.py からYAML化）
  trailing_stage2_r: 1.5
  trailing_stage2_atr_multiplier: 1.2
  # NEW: Stagnation段階制御（position_manager.py からYAML化）
  stagnation_stage1_minutes: 60.0
  stagnation_stage1_mfe_r: 0.05
  stagnation_stage2_minutes: 90.0
  stagnation_stage2_mfe_r: 0.10
  # NEW: Stagnation事前SL締め（position_manager.py からYAML化）
  stag_pretighten_pct: 0.80
  stag_pretighten_mfe_r: 0.10
  stag_pretighten_sl_r: -0.05
  # NEW: 早期利益ガード詳細（position_manager.py からYAML化）
  early_profit_guard_min_mfe_r: 0.05
  early_profit_guard_max_r: 0.30
  early_profit_guard_score_diff: 1.0
  early_profit_guard_min_opp_score: 4.0
  early_profit_guard_min_hold_minutes: 5.0
  # NEW: エッジ劣化詳細（position_manager.py からYAML化）
  edge_decay_exit_enabled: true
  edge_decay_exit_threshold: 0.40
  edge_decay_exit_min_bars: 5
  edge_decay_exit_max_loss_r: -0.3
  edge_decay_profit_exit_enabled: true
  edge_decay_profit_erosion_threshold: 0.60
  edge_decay_profit_decay_min: 0.25
  edge_decay_stagnation_enabled: true
  edge_decay_stagnation_threshold: 0.35
  edge_decay_stagnation_multiplier: 0.65

# === エッジ検定（NEW: edge_validator.py からYAML化）===
edge_validator:
  expected_wr: 0.80
  info_wr_drop: 0.05
  warning_wr_drop: 0.10
  stop_wr_drop: 0.15
  critical_wr_drop: 0.20
  info_pf_threshold: 2.0
  warning_pf_threshold: 1.5
  stop_pf_threshold: 1.3
  critical_pf_threshold: 1.0

# === ポジションサイザー（NEW: position_sizer.py からYAML化）===
position_sizer:
  base_risk_pct: 0.025
  max_lot_per_trade: 2.5
  max_total_exposure_lot: 4.0
  equity_floor_pct: 0.30
  equity_caution_pct: 0.50
  confidence_high_threshold: 0.7
  confidence_low_threshold: 0.5
  dd_reduction_threshold: 0.015
  dd_max_reduction: 0.5
  dd_early_threshold: 0.008
  consecutive_loss_start: 2
  consecutive_loss_max: 5
  consecutive_loss_min_adjust: 0.2
```

---

## ② symbol_overrides.yaml（新規作成）

`symbol_presets.yaml` の `defaults:` / `symbols:` をそのまま移動（内容変更なし）。

---

## ③ modes.yaml（新規作成）

`demo_trading.yaml` の内容を移動。

```yaml
live:
  # ライブ固有の上書きが必要な場合のみ記載（現状は空）

demo:
  bot_config:
    demo_mode: true
    demo_consensus_threshold: 1.5
    range_day_bbw_threshold: 0.10
    weak_hours_enabled: false
    base_risk_pct: 0.01
    max_lot_per_trade: 0.5
    ...
  pm_config:
    trailing_start_r: 2.0
    ...
```

---

## 変更ファイル

### YAML（設定ファイル）

| ファイル | 変更内容 |
|---------|---------|
| `config/trading_defaults.yaml` | **新規作成**（グローバルデフォルト全集約） |
| `config/symbol_overrides.yaml` | **新規作成**（symbol_presets.yaml の defaults/symbols を移動） |
| `config/modes.yaml` | **新規作成**（demo_trading.yaml の内容を移動） |
| `config/symbol_presets.yaml` | 後方互換として残存、deprecation コメントを追加 |
| `config/live_trading.yaml` | 後方互換として残存 |
| `config/demo_trading.yaml` | 後方互換として残存 |

### Python（アダプター）

| ファイル | 変更内容 |
|---------|---------|
| `autotrader/config/config_loader.py` | 3ファイル読込・マージ・新設定クラス対応 |
| `autotrader/config/trading_params.py` | `_load_presets()` を `symbol_overrides.yaml` 優先に更新 |
| `autotrader/decision/unified/adaptive/edge_validator.py` | `EdgeValidatorConfig` に `from_yaml_section()` 追加 |
| `autotrader/decision/unified/risk/position_sizer.py` | `PositionSizerConfig` に `from_yaml_section()` 追加 |

---

## アダプター更新詳細

### マージ優先順位（低→高）

```
① trading_defaults.yaml（全グローバルデフォルト）
  ↓ symbol_overrides.yaml[symbol] で上書き
② symbol_overrides.yaml（通貨ペア別差分）
  ↓ modes.yaml[mode] で上書き（デモ時のみ）
③ modes.yaml（モード別上書き）
```

### config_loader.py の変更

**`load_preset_config(symbol)`**
```python
# ① trading_defaults.yaml 読込
# ② symbol_overrides.yaml[symbol] で上書き
# ③ 従来通り UnifiedBotConfig + PositionManagerConfig を生成
# 後方互換: trading_defaults.yaml 未存在時は symbol_presets.yaml にフォールバック
```

**新メソッド: `load_edge_validator_config()`**
```python
# trading_defaults.yaml[edge_validator] → EdgeValidatorConfig を生成
# セクション未存在時は Python デフォルト値を使用
```

**新メソッド: `load_position_sizer_config()`**
```python
# trading_defaults.yaml[position_sizer] → PositionSizerConfig を生成
# セクション未存在時は Python デフォルト値を使用
```

**`load_demo_config()`**
```python
# load_preset_config() の結果に modes.yaml[demo] の値で上書き
```

**`save_bot_config()` / `save_pm_config()`**
```python
# 保存先: trading_defaults.yaml の signal/pm_config セクション（旧: live_trading.yaml）
```

### EdgeValidatorConfig・PositionSizerConfig への YAML 読込経路

これら2クラスは現在 Python デフォルト値のみ。
`ConfigLoader` 経由で呼び出し元に渡す方式に変更:

```python
# バックテストや engine 起動時（変更後）:
loader = ConfigLoader()
edge_val_cfg = loader.load_edge_validator_config()
sizer_cfg    = loader.load_position_sizer_config()
```

呼び出し元（`simulator.py`, `engine.py`）でこれらを受け取るよう修正。

---

## 設定ソース検知（YAMLvsデフォルト透明化）

### 課題
現状: あるパラメータが「YAMLから来た値」か「Pythonデフォルト値」か区別できない。
BTの設定が意図通りに認識されているか確認する手段がない。

### 対策: ConfigLoader にソーストラッキングを追加

**実装方針**:
```python
# ConfigLoader.load_preset_config() の内部で、
# YAMLから取得できたフィールド名と、デフォルトを使用したフィールド名を記録

def load_preset_config(self, symbol: str) -> tuple[UnifiedBotConfig, PositionManagerConfig]:
    yaml_keys = set(_merged_dict.keys())       # YAML由来のキー一覧
    all_fields = {f.name for f in dataclasses.fields(UnifiedBotConfig)}
    default_used = all_fields - yaml_keys       # Python デフォルト使用フィールド

    # WARNING: デフォルト使用フィールドを警告ログ
    if default_used:
        logger.warning(
            "[%s] YAML未定義→Pythonデフォルト使用: %s",
            symbol, sorted(default_used)
        )
```

**バックテスト開始時に effective config をログ出力**:
```
[USDJPY] 有効設定（YAML源泉）:
  consensus_threshold=18.0 (yaml)
  trend_strength_max=0.85 (yaml/symbol_override)
  edge_decay_exit_threshold=0.40 (yaml/trading_defaults)
  trailing_stage2_r=1.5 (yaml/trading_defaults)  ← 今回NEW
[USDJPY] Pythonデフォルト使用（YAML未定義）:
  （理想: 空リスト）
```

**`job_config.json` への記録**（BTキュー経由時）:
```json
{
  "effective_pm_config": {
    "edge_decay_exit_threshold": 0.40,
    "trailing_stage2_r": 1.5,
    ...（全フィールドの実効値）
  },
  "config_sources": {
    "yaml_defined": ["consensus_threshold", "edge_decay_exit_threshold", ...],
    "python_default": []   ← 理想的には空
  }
}
```

これにより「BTの設定が意図通りか」をログとjob_config.jsonで確認できる。

---

## 検証方法

1. **既存テスト**: `pytest tests/unit/ --ignore=tests/unit/backtest -q` が pre-existing 以外 PASS
2. **設定ロード確認**:
   ```python
   from autotrader.config.config_loader import ConfigLoader
   loader = ConfigLoader()
   bot, pm = loader.load_preset_config("USDJPY")
   assert bot.consensus_threshold == 18.0
   assert bot.trend_strength_max == 0.85      # USDJPY 固有値
   assert bot.macd_slope_bonus == 2.5         # NEW: ハードコードから移行
   assert pm.edge_decay_exit_threshold == 0.40
   assert pm.trailing_stage2_r == 1.5         # NEW: ハードコードから移行
   ev = loader.load_edge_validator_config()
   assert ev.expected_wr == 0.80              # NEW: ハードコードから移行
   ```
3. **デモモード確認**: `loader.load_demo_config()` で `demo_mode=True`
4. **後方互換確認**: `symbol_presets.yaml` 残存状態でもエラーなし
5. **バックテスト動作確認**: USDJPY 2023年 キューランナー経由で正常完了
