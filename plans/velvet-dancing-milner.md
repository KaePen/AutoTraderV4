# 全TFロール動的化 + 設定値集約

## Context

TF組み合わせ網羅検証（255パターン）の結果、M15を含まない組み合わせが全て0取引。
調査の結果、以下の構造的問題が判明:

1. `primary_tf="M15"`が9箇所にハードコード → PRIMARYロール（重み3.0）で構造的バイアス
2. `regime_detection_tf="H1"`がconfig固定 → レジーム検出TFも静的
3. `htf_alignment_tfs=["H4","D1"]`がconfig固定 → HTF整合チェックも静的
4. `manage_tf="M15"`がハードコード → ポジション管理TFも静的
5. 同一設定値が複数箇所に散在し**6件の不整合**が発生中

**目的**:
1. **全TFロール**（primary, entry, manage, regime_detection, htf_alignment）を動的選択に変更
2. 散在する設定値を`UnifiedBotConfig`に集約（Single Source of Truth）
3. 実行順序を再構成し、DynamicTFSelector結果を全TFロールに反映

---

## 現在の実行順序と問題点

```
_generate_signal_new() 現在の実行順序:
  1. _detect_regime()          ← config.regime_detection_tf="H1"（固定）
  2. _get_htf_alignment()      ← config.htf_alignment_tfs=["H4","D1"]（固定）
  3. mode_selector.select()    ← regime/htf結果を渡すが、実際は無視している
  4. tf_router.route(plan)     ← plan.primary_tf="M15"固定のまま
  5. risk_manager.can_trade()
  6. 全TF評価 → tf_signals
  7. DynamicTFSelector.select() ← ここで初めて動的TF決定
  8. planの部分更新             ← entry_tf/holding/tp_slのみ更新（primary_tf未更新）
  9. consensus.consolidate()
  10. フィルター → ConsolidatedSignal
```

**問題**: DynamicTFSelector（ステップ7）の結果が、regime_detection（ステップ1）やhtf_alignment（ステップ2）に反映されない。primary_tfも更新されない。

---

## 新しい実行順序（Phase 3で実装）

```
_generate_signal_new() 新しい実行順序:
  1. risk_manager.can_trade()    ← 早期リターンで不要な計算を回避
  2. 全TF評価 → tf_signals       ← 最初に全シグナル取得
  3. DynamicTFSelector.select()  ← 全TFロールを動的決定
     → entry_tf, primary_tf, manage_tf, regime_tf, htf_alignment_tfs
  4. plan更新（全TFロール反映）
  5. _detect_regime(regime_tf)   ← 動的regime_tfを使用
  6. _get_htf_alignment(htf_tfs) ← 動的htf_tfsを使用
  7. tf_router.route(plan)       ← 動的planでルーティング
  8. consensus_signals構築
  9. consensus.consolidate()
  10. _check_htf_trend_alignment(htf_tfs) ← 動的htf_tfsを使用
  11. フィルター → ConsolidatedSignal
```

---

## TFロール動的導出ルール

DynamicTFSelectorが`entry_tf`（最強シグナルTF）を選択した後、
**メジャーTFラダー**を使って他のロールを導出する:

```
メジャーTFラダー: M1 → M5 → M15 → H1 → H4 → D1

entry_tf=M5 の場合:
  primary_tf      = M15 (1段上)    ← SL/TP計算、MACDフィルター、BB幅
  manage_tf       = M15 (=primary) ← ポジション管理
  regime_tf       = H1  (2段上)    ← レジーム検出
  htf_alignment   = [H4, D1]      ← regime_tfより上の全TF

entry_tf=M15 の場合:
  primary_tf      = H1
  manage_tf       = H1
  regime_tf       = H4
  htf_alignment   = [D1]

entry_tf=H1 の場合:
  primary_tf      = H4
  manage_tf       = H4
  regime_tf       = D1
  htf_alignment   = [D1]          ← 最低1TF保証

entry_tf=H4 の場合:
  primary_tf      = D1
  manage_tf       = D1
  regime_tf       = D1
  htf_alignment   = [D1]
```

---

## Phase 1: UnifiedBotConfig拡張 + TradingPlanファクトリ

### 1.1 UnifiedBotConfig に新フィールド追加

**ファイル**: `autotrader/decision/unified/config.py`

```python
# --- TradingPlan デフォルト設定 ---
default_primary_tf: str = "M15"
default_entry_tf: str = "M5"
default_manage_tf: str = "M15"
default_max_holding_bars: int = 32
default_tp_sl_ratio_range: tuple[float, float] = (1.1, 1.4)

# --- フィルター閾値 ---
macd_slope_filter_threshold: float = -2.0
sl_min_pips: float = 10.0
sl_max_pips_default: float = 50.0

# --- コンセンサス重み ---
consensus_primary_weight: float = 3.0
consensus_entry_weight: float = 2.5
consensus_confirm_weight: float = 2.0
consensus_manage_weight: float = 1.5
consensus_other_weight: float = 1.0
```

デフォルト値は現在のハードコード値と完全一致 → 後方互換を保証。

### 1.2 TradingPlan.create_universal() ファクトリメソッド

**ファイル**: `autotrader/decision/unified/mode_selector.py`

```python
@classmethod
def create_universal(
    cls,
    config: "UnifiedBotConfig | None" = None,
) -> "TradingPlan":
    """UnifiedBotConfigからUNIVERSALプランを生成。唯一の生成手段。"""
    if config is None:
        from autotrader.decision.unified.config import UnifiedBotConfig
        config = UnifiedBotConfig()
    _confirm = [
        tf for tf in config.timeframes
        if tf not in {config.default_primary_tf, config.default_entry_tf}
    ]
    return cls(
        mode=TradingStrategyMode.UNIVERSAL,
        primary_tf=config.default_primary_tf,
        entry_tf=config.default_entry_tf,
        confirm_tfs=_confirm,
        manage_tf=config.default_manage_tf,
        max_holding_bars=config.default_max_holding_bars,
        tp_sl_ratio_range=config.default_tp_sl_ratio_range,
        selection_reason="UNIVERSAL（動的TF選択）",
    )
```

### 1.3 TradingModeSelector の変更

`__init__`に`bot_config`を受け取り、`get_plan_for_mode()`で`TradingPlan.create_universal(config)`を呼ぶ。
`_DEFAULT_CONFIRM_TFS`定数を削除。

---

## Phase 2: ハードコードTradingPlan排除

全モジュールの直接`TradingPlan()`構築を`TradingPlan.create_universal(config)`に置換。

| ファイル | 行 | 変更内容 |
|---------|-----|---------|
| `entry_resolver.py` L32 | `UNIVERSAL_ENTRY_CONFIG`定数削除 → config受取、`create_universal()` |
| `mode_monitor.py` L49-58 | `UNIVERSAL_CONFIG`定数削除 → `from_bot_config()`クラスメソッド追加 |
| `simulator.py` L828-836 | `SimulatorConfig`に`bot_config`フィールド追加、`create_universal()`使用 |
| `live/engine.py` L1723-1729 | `TradingPlan.create_universal(self._bot_config)`に置換 |
| `dynamic_tf_selector.py` L98,L138 | フォールバック値をconfig参照に変更 |
| `strategies/no_trade.py` L32,L132,L156 | planのprimary_tfを参照するよう変更 |
| `strategies/short_mid.py` L41 | primary_tfはconfig参照 |
| `trade_bot.py` L186 | `DEFAULT_TIMEFRAMES`定数削除（`config.timeframes`で統一） |

各モジュールの`__init__`に`bot_config: UnifiedBotConfig | None = None`を追加し、
設定値の流れを **`UnifiedBotConfig → TradingPlan.create_universal() → 各モジュール`** に統一。

**SimulatorConfig変更** (`simulator.py`):
```python
bot_config: "UnifiedBotConfig | None" = None
```

**バックテスト結果一致確認**: Phase 2完了時点でデフォルトconfigを使用し変更前と同一結果を確認。

---

## Phase 3: 全TFロール動的化 + 実行順序再構成

### 3.1 DynamicTFResult の拡張

**ファイル**: `autotrader/decision/unified/dynamic_tf_selector.py`

```python
@dataclass(frozen=True)
class DynamicTFResult:
    selected_entry_tf: str
    selected_primary_tf: str
    selected_manage_tf: str              # 新規
    selected_regime_tf: str              # 新規
    selected_htf_alignment_tfs: list[str]  # 新規
    all_tf_scores: dict[str, float]
    max_holding_bars: int
    tp_sl_ratio_range: tuple[float, float]
    selection_reason: str
```

### 3.2 メジャーTFラダーによるロール導出

**ファイル**: `autotrader/decision/unified/dynamic_tf_selector.py`

現在の`_get_primary_tf()`はTF_HIERARCHY（19TF全列挙）で1つ上を返すため、
M5→M6のような無意味な結果になる。これを**メジャーTFラダー**に変更:

```python
# メジャーTFラダー（トレーディングで意味のあるTFジャンプ）
MAJOR_TF_LADDER = ["M1", "M5", "M15", "H1", "H4", "D1"]

def _derive_all_roles(self, entry_tf: str) -> dict[str, any]:
    """entry_tfから全TFロールを導出"""
    ladder = self.MAJOR_TF_LADDER
    # entry_tfを最も近いラダー段に吸着
    entry_idx = self._snap_to_ladder(entry_tf)

    primary_idx = min(entry_idx + 1, len(ladder) - 1)
    regime_idx = min(entry_idx + 2, len(ladder) - 1)

    primary_tf = ladder[primary_idx]
    regime_tf = ladder[regime_idx]
    # htf_alignment: regime_tfより上の全TF（最低1つ保証）
    htf_tfs = ladder[regime_idx + 1:] if regime_idx + 1 < len(ladder) else [ladder[-1]]

    return {
        "primary_tf": primary_tf,
        "manage_tf": primary_tf,  # primary_tfに連動
        "regime_tf": regime_tf,
        "htf_alignment_tfs": htf_tfs or [ladder[-1]],
    }
```

`_snap_to_ladder()`: 任意のTFを最も近い（以下の）ラダー段に吸着。
例: M10 → M5段、M20 → M15段、H2 → H1段。

### 3.3 DynamicTFSelector.select() の更新

`select()`内で`_derive_all_roles()`を呼び、`DynamicTFResult`に全ロールを格納:

```python
roles = self._derive_all_roles(best_tf)
return DynamicTFResult(
    selected_entry_tf=best_tf,
    selected_primary_tf=roles["primary_tf"],
    selected_manage_tf=roles["manage_tf"],
    selected_regime_tf=roles["regime_tf"],
    selected_htf_alignment_tfs=roles["htf_alignment_tfs"],
    all_tf_scores=tf_scores,
    max_holding_bars=max_bars,
    tp_sl_ratio_range=tp_sl,
    selection_reason=f"最強シグナルTF={best_tf}(score={...})",
)
```

フォールバック（シグナルなし）もconfig参照に変更。

### 3.4 _generate_signal_new() 実行順序再構成

**ファイル**: `autotrader/decision/unified/trade_bot.py` L360-882

```python
def _generate_signal_new(self, current_time, candle=None):
    # 1. 日次リセット
    self.risk_manager.reset_daily(py_time)

    # 2. リスク管理チェック（早期リターン）
    can_trade, reason = self.risk_manager.can_trade(py_time)
    if not can_trade:
        return self._hold_signal(reason)

    # 3. 初期プラン（デフォルトTF値）
    plan = self.mode_selector.select()

    # 4. 全TF評価 → tf_signals
    tf_signals = self._evaluate_all_tfs(current_time, candle, plan)

    # 5. 動的TF選択 → 全TFロール決定
    if tf_signals:
        _dynamic = self._dynamic_tf_selector.select(tf_signals)
        plan = dataclasses.replace(
            plan,
            primary_tf=_dynamic.selected_primary_tf,
            manage_tf=_dynamic.selected_manage_tf,
            dynamic_entry_tf=_dynamic.selected_entry_tf,
            max_holding_bars=_dynamic.max_holding_bars,
            tp_sl_ratio_range=_dynamic.tp_sl_ratio_range,
        )
        _regime_tf = _dynamic.selected_regime_tf
        _htf_tfs = _dynamic.selected_htf_alignment_tfs
    else:
        _regime_tf = self.config.regime_detection_tf
        _htf_tfs = self.config.htf_alignment_tfs

    # 6. レジーム検出（動的regime_tfを使用）
    regime_result = self._detect_regime(current_time, regime_tf=_regime_tf)
    self._last_regime = regime_result.regime.value

    # 7. HTF整合度（動的htf_tfsを使用）
    htf_alignment = self._get_htf_alignment(current_time, htf_tfs=_htf_tfs)

    # 8. TFルーティング（動的plan使用）
    tf_set = self.tf_router.route(plan)

    # 9. consensus_signals構築
    # 10. consensus.consolidate()
    # 11. _check_htf_trend_alignment(動的htf_tfs)
    # 12. フィルター → ConsolidatedSignal
```

### 3.5 _detect_regime() と _get_htf_alignment() のパラメータ化

```python
# 変更前
def _detect_regime(self, current_time):
    _regime_tf = self.config.regime_detection_tf  # 固定 "H1"

# 変更後
def _detect_regime(self, current_time, regime_tf: str | None = None):
    _regime_tf = regime_tf or self.config.regime_detection_tf
```

```python
# 変更前
def _get_htf_alignment(self, current_time):
    for tf in self.config.htf_alignment_tfs:  # 固定 ["H4","D1"]

# 変更後
def _get_htf_alignment(self, current_time, htf_tfs: list[str] | None = None):
    _tfs = htf_tfs or self.config.htf_alignment_tfs
    for tf in _tfs:
```

```python
# _check_htf_trend_alignment も同様
def _check_htf_trend_alignment(self, current_time, direction, htf_tfs=None):
    check_tfs = htf_tfs or self.config.htf_alignment_tfs
```

### 3.6 TF別SL上限

**ファイル**: `autotrader/config/tf_params_registry.py`

```python
_SL_MAX_PIPS: dict[str, float] = {
    "M1": 20.0, "M5": 30.0, "M15": 50.0, "M30": 70.0,
    "H1": 100.0, "H4": 150.0, "H8": 200.0, "D1": 300.0,
}

def get_sl_max_pips(tf: str) -> float:
    return interpolate_tf_param(_SL_MAX_PIPS, tf)
```

**ファイル**: `autotrader/decision/unified/timeframe_evaluator.py` L911

```python
# 変更前: sl_pips = max(10.0, min(sl_pips, 50.0))
# 変更後:
_sl_max = get_sl_max_pips(self.timeframe)
sl_pips = max(10.0, min(sl_pips, _sl_max))
```

### 3.7 MACDスロープ閾値config化

**ファイル**: `autotrader/decision/unified/trade_bot.py` L740

```python
# 変更前: if _macd_slope <= -2.0:
# 変更後:
if _macd_slope <= self.config.macd_slope_filter_threshold:
```

---

## Phase 4: ROLE_WEIGHTS集約

### 4.1 ConsensusConfig の重み統一

**ファイル**: `autotrader/decision/unified/mode_aware_consensus.py`

- `ROLE_WEIGHTS`クラス変数（L95-101）を削除（使われていない死コード）
- `__init__`の`self.role_weights`を`ConsensusConfig`の値で統一
- `ConsensusConfig`に`manage_weight`フィールド追加

```python
@dataclass(frozen=True)
class ConsensusConfig:
    primary_weight: float = 3.0
    entry_weight: float = 2.5
    confirm_weight: float = 2.0
    manage_weight: float = 1.5   # 新規
    other_weight: float = 1.0
    threshold: float = 4.5
    ...
```

### 4.2 trade_bot.py でconfig→ConsensusConfig伝搬

```python
_consensus_cfg = ConsensusConfig(
    primary_weight=self.config.consensus_primary_weight,
    entry_weight=self.config.consensus_entry_weight,
    confirm_weight=self.config.consensus_confirm_weight,
    manage_weight=self.config.consensus_manage_weight,
    other_weight=self.config.consensus_other_weight,
    threshold=self.config.demo_consensus_threshold
        if self.config.demo_mode
        else self.config.consensus_threshold,
)
```

### 4.3 timeframe_router.py の重みをConsensusConfigと整合

`TimeframeSet.get_weight()` (L71-88) の重み値をConsensusConfigデフォルトと一致:
- ENTRY: 2.0 → 2.5
- CONFIRM: 1.5 → 2.0
- MANAGE: 1.0 → 1.5
- OTHER: 0.5 → 1.0

---

## Phase 5: テスト + バックテスト比較検証

### ユニットテスト

| テスト | 内容 |
|--------|------|
| `test_trading_plan_factory.py`（新規） | `create_universal()`のデフォルト値、カスタム値、confirm_tfs自動導出 |
| `test_dynamic_tf_selector.py`（既存更新） | 全TFロール動的導出: entry→primary→regime→htf_alignment |
| `test_dynamic_tf_selector.py`（追加） | メジャーTFラダーのsnap_to_ladder、境界ケース（D1選択時の最上段処理） |
| `test_config.py`（既存更新） | 新フィールドのデフォルト値確認 |

### バックテスト比較

```bash
# Phase 2完了時点: デフォルトconfig → 変更前と同一結果を確認
python scripts/run_backtest.py --symbol USDJPY --years 2024

# Phase 3+4完了後: 全TFロール動的化 → 結果比較
python scripts/run_backtest.py --symbol USDJPY --years 2024
```

Phase 2完了時点で変更前と完全一致を確認（設定値の集約のみ、ロジック変更なし）。
Phase 3で全TFが動的になるため結果は変わる → 変化が合理的か検証。

### 検証ポイント

- M15を含まないTF組み合わせで取引が発生するようになること
- 各entry_tfに対するロール導出が正しいこと（ラダー表と一致）
- フォールバック（全TFスコア0）時にconfigデフォルト値が使われること

---

## 実装順序

```
Phase 1 (config拡張 + ファクトリ)          ← 最初に実施、他全てのベース
    ↓
Phase 2 (ハードコード排除)                 ← Phase 1完了後
    ↓ ※ここでバックテスト結果一致を確認
Phase 3 (全TFロール動的化 + 実行順序再構成) ← Phase 2完了後
    ↓
Phase 4 (ROLE_WEIGHTS集約)                 ← Phase 1完了後（Phase 3と並行可能）
    ↓
Phase 5 (テスト + バックテスト比較検証)      ← 全Phase完了後
```

---

## 変更ファイル一覧

| ファイル | Phase | 変更内容 |
|---------|-------|---------|
| `autotrader/decision/unified/config.py` | 1 | 15フィールド追加 |
| `autotrader/decision/unified/mode_selector.py` | 1,2 | `create_universal()`追加、`TradingModeSelector`変更 |
| `autotrader/decision/unified/dynamic_tf_selector.py` | 2,3 | `DynamicTFResult`拡張、メジャーTFラダー、`_derive_all_roles()` |
| `autotrader/decision/unified/trade_bot.py` | 2,3,4 | 実行順序再構成、全TFロール動的適用、config伝搬 |
| `autotrader/decision/unified/entry_resolver.py` | 2 | 定数削除→config参照 |
| `autotrader/decision/unified/mode_monitor.py` | 2 | 定数削除→config参照 |
| `autotrader/decision/unified/mode_aware_consensus.py` | 4 | ROLE_WEIGHTS集約、manage_weight追加 |
| `autotrader/decision/unified/timeframe_router.py` | 4 | get_weight()の値をConsensusConfig整合 |
| `autotrader/decision/unified/timeframe_evaluator.py` | 3 | SL上限をTF別に可変化 |
| `autotrader/config/tf_params_registry.py` | 3 | `_SL_MAX_PIPS`辞書、`get_sl_max_pips()`追加 |
| `autotrader/backtest/simulator.py` | 2 | `SimulatorConfig.bot_config`追加、`create_universal()`使用 |
| `autotrader/live/engine.py` | 2 | `create_universal()`使用 |
| `autotrader/decision/unified/strategies/no_trade.py` | 2 | plan参照に変更 |
| `autotrader/decision/unified/strategies/short_mid.py` | 2 | primary_tfをconfig参照 |
| `tests/` | 5 | 新規+既存テスト更新 |
