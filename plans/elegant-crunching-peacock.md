# バックテスト CLI 引数完全化リファクタリング

## Context

バックテストスクリプト (`scripts/run_backtest.py`) には57個のCLI引数があるが、
`PositionManagerConfig`, `UnifiedBotConfig`, `SimulatorConfig` にはCLIから調整できないパラメータが25個存在する。
うち実際にバックテスト実行パスで**アクティブに使用されている**パラメータは16個。
これらを全てCLI引数で調整可能にし、最適化実験の自由度を向上させる。

※ `min_adx`, `require_htf_trend`, `enable_position_manager` はレガシー/未使用のため対象外。

## 変更ファイル

1. `scripts/run_backtest.py` - CLI引数追加 + 接続コード
2. `autotrader/backtest/service.py` - `BacktestServiceConfig` フィールド追加 + `create_backtest_config` 修正
3. `autotrader/backtest/runner.py` - `BacktestConfig` フィールド追加 + `run_unified` 修正

## 追加する CLI 引数（16個）

### PositionManagerConfig 系（11個）

| CLI引数 | Configフィールド | デフォルト | 説明 |
|---------|----------------|-----------|------|
| `--partial-1r-ratio` | `partial_close_1r_ratio` | 0.3 | 1R到達時の部分決済比率 |
| `--partial-2r-ratio` | `partial_close_2r_ratio` | 0.3 | 2R到達時の部分決済比率 |
| `--no-breakeven-1r` | `breakeven_at_1r` | True | 1R建値移動を無効化 |
| `--trailing-start-r` | `trailing_start_r` | 2.0 | トレーリング開始R値 |
| `--trailing-atr-mult` | `trailing_atr_multiplier` | 2.0 | ATRトレーリング倍率 |
| `--early-be-r` | `early_breakeven_r` | 0.5 | 早期BE移動のR閾値 |
| `--no-early-be` | `early_breakeven_enabled` | True | 早期BE移動を無効化 |
| `--signal-rev-ratio` | `signal_rev_close_ratio` | 0.5 | シグナル反転決済比率 |
| `--half-r-ratio` | `range_day_half_r_partial_ratio` | 0.20 | 0.5R部分利確比率 |
| `--half-r-trigger` | `range_day_half_r_trigger` | 0.5 | 0.5R部分利確トリガーR値 |
| `--no-time-exit` | `time_exit_enabled` | True | 時間決済を無効化 |

### UnifiedBotConfig 系（3個）

| CLI引数 | Configフィールド | デフォルト | 説明 |
|---------|----------------|-----------|------|
| `--bonus-max-positions` | `bonus_max_positions` | 0 | 高品質シグナル追加枠数 |
| `--bonus-score-threshold` | `bonus_score_threshold` | 7.0 | bonus発動コンセンサス閾値 |
| `--no-position-sizing` | `enable_position_sizing` | True | ポジションサイジング無効化 |

### SimulatorConfig 系（2個）

| CLI引数 | Configフィールド | デフォルト | 説明 |
|---------|----------------|-----------|------|
| `--commission` | `commission_per_lot` | preset値 | ロット当たり手数料（上書き） |
| `--session-spread` | `use_session_spread` | False | セッション別スプレッド有効化 |

## 実装手順

### Step 1: BacktestConfig / BacktestServiceConfig のフィールド追加

**`autotrader/backtest/runner.py` - BacktestConfig:**
```python
# 既存フィールドの後に追加
commission_per_lot: float = field(
    default_factory=lambda: DEFAULT_TRADING_PARAMS.commission_per_lot
)
use_session_spread: bool = False
```

**`autotrader/backtest/service.py` - BacktestServiceConfig:**
```python
# 既存フィールドの後に追加
commission_per_lot: float = field(
    default_factory=lambda: DEFAULT_TRADING_PARAMS.commission_per_lot
)
use_session_spread: bool = False
bonus_max_positions: int = 0
bonus_score_threshold: float = 7.0
pip_value: float = field(
    default_factory=lambda: DEFAULT_TRADING_PARAMS.pip_value
)
```

### Step 2: create_backtest_config の修正

**`autotrader/backtest/service.py`:**
```python
def create_backtest_config(config: BacktestServiceConfig) -> BacktestConfig:
    return BacktestConfig(
        symbol=config.symbol,
        timeframe=config.timeframe,
        initial_balance=config.initial_balance,
        volume=config.volume,
        max_positions=config.max_positions,
        spread_pips=config.spread_pips,
        slippage_pips=config.slippage_pips,
        # 追加フィールド
        commission_per_lot=config.commission_per_lot,
        use_session_spread=config.use_session_spread,
        bonus_max_positions=config.bonus_max_positions,
        bonus_score_threshold=config.bonus_score_threshold,
        pip_value=config.pip_value,
    )
```

### Step 3: run_unified の SimulatorConfig 構築修正

**`autotrader/backtest/runner.py` - run_unified内:**
```python
sim_config = SimulatorConfig(
    ...
    commission_per_lot=self.config.commission_per_lot,  # 追加
    use_session_spread=self.config.use_session_spread,  # 追加
    ...
)
```

### Step 4: parse_args に16個の引数追加

**`scripts/run_backtest.py`** の `parse_args()` にグループ化して追加:
- ポジション管理（PM）グループ: 11引数
- ボット設定グループ: 3引数
- シミュレーター設定グループ: 2引数

### Step 5: run_single_backtest の接続コード修正

`bot_config` 構築に3個追加:
```python
bot_config = UnifiedBotConfig(
    ...
    bonus_max_positions=args.bonus_max_positions,
    bonus_score_threshold=args.bonus_score_threshold,
    enable_position_sizing=not args.no_position_sizing,
)
```

`pm_config` 構築に11個追加:
```python
pm_config = PositionManagerConfig(
    ...
    partial_close_1r_ratio=args.partial_1r_ratio,
    partial_close_2r_ratio=args.partial_2r_ratio,
    breakeven_at_1r=not args.no_breakeven_1r,
    trailing_start_r=args.trailing_start_r,
    trailing_atr_multiplier=args.trailing_atr_mult,
    early_breakeven_r=args.early_be_r,
    early_breakeven_enabled=not args.no_early_be,
    signal_rev_close_ratio=args.signal_rev_ratio,
    range_day_half_r_partial_ratio=args.half_r_ratio,
    range_day_half_r_trigger=args.half_r_trigger,
    time_exit_enabled=not args.no_time_exit,
)
```

`BacktestServiceConfig` 構築に2個追加:
```python
config = BacktestServiceConfig(
    ...
    commission_per_lot=...,  # --commission or preset
    use_session_spread=args.session_spread,
    bonus_max_positions=args.bonus_max_positions,
    bonus_score_threshold=args.bonus_score_threshold,
    pip_value=_preset.pip_value,
)
```

## 検証方法

1. `python scripts/run_backtest.py --help` で全引数が表示されること
2. `python scripts/run_backtest.py --years 2024 --partial-1r-ratio 0.5 --trailing-start-r 1.5` でパラメータが反映されること
3. `python -m pytest tests/ -x -q` で既存テストが全PASS
4. デフォルト値でのバックテスト結果が変更前と同一であること（回帰確認）
