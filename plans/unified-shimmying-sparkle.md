# 通貨ペアごとの取引プリセット設定システム

## Context

現在 AutoTraderV4 は USDJPY 専用に設計されており、`TradingParams` の
デフォルト値（pip_value=100.0、spread_pips=1.5 等）がすべてハードコードされている。
複数通貨ペアへの拡張に際し、通貨ペアごとに最適な取引パラメータ
（pip値・スプレッド・SL/TP・ポジション数・リスク管理等）を
YAML プリセットとして管理し、バックテストとライブトレード双方で
自動的に適用できる仕組みを導入する。

---

## 変更ファイル一覧

| # | ファイル | 変更種別 |
|---|---------|---------|
| 1 | `config/symbol_presets.yaml` | 新規作成 |
| 2 | `src/autotrader/config/trading_params.py` | 追記（既存コード変更なし） |
| 3 | `src/autotrader/config/__init__.py` | 公開 API 追加 |
| 4 | `src/autotrader/backtest/runner.py` | `BacktestConfig.from_preset()` 追加 |
| 5 | `src/autotrader/backtest/service.py` | `BacktestServiceConfig.from_preset()` 追加 |
| 6 | `scripts/run_backtest.py` | プリセット自動適用（2 箇所修正） |
| 7 | `src/autotrader/config/config_loader.py` | ライブ設定へのプリセット適用 |
| 8 | `config/live_trading.yaml` | `symbol` キー追加 |
| 9 | `tests/unit/config/test_symbol_preset.py` | 新規テスト |

---

## Step 1: `config/symbol_presets.yaml`（新規）

通貨ペア別全パラメータを定義する YAML ファイル。
`defaults` をベースに `symbols[X]` で上書きするマージ方式。

```yaml
# defaults: 全シンボル共通フォールバック
defaults:
  pip_value: 100.0
  spread_pips: 1.5
  slippage_pips: 0.5
  default_sl_pips: 20.0
  default_tp_pips: 40.0
  min_lot: 0.01
  max_lot: 10.0
  commission_per_lot: 0.0
  max_positions: 2
  bonus_max_positions: 1
  bonus_score_threshold: 7.0
  base_risk_pct: 0.02
  max_lot_per_trade: 2.0
  max_total_exposure_lot: 5.0
  equity_floor_pct: 0.30

symbols:
  USDJPY:
    pip_value: 100.0
    spread_pips: 1.5
    slippage_pips: 0.5
    default_sl_pips: 20.0
    default_tp_pips: 40.0
    max_positions: 2
    bonus_score_threshold: 7.0
    base_risk_pct: 0.02
    max_lot_per_trade: 2.0

  EURUSD:
    pip_value: 10.0
    spread_pips: 1.0
    slippage_pips: 0.3
    default_sl_pips: 15.0
    default_tp_pips: 30.0
    max_positions: 2
    bonus_score_threshold: 7.0
    base_risk_pct: 0.02
    max_lot_per_trade: 2.0

  GBPJPY:
    pip_value: 100.0
    spread_pips: 3.0
    slippage_pips: 1.0
    default_sl_pips: 30.0
    default_tp_pips: 60.0
    max_positions: 1
    bonus_max_positions: 1
    bonus_score_threshold: 8.0
    base_risk_pct: 0.015
    max_lot_per_trade: 1.0

  EURJPY:
    pip_value: 100.0
    spread_pips: 2.0
    slippage_pips: 0.7
    default_sl_pips: 25.0
    default_tp_pips: 50.0
    max_positions: 2
    bonus_score_threshold: 7.5
    base_risk_pct: 0.02
    max_lot_per_trade: 1.5
```

---

## Step 2: `src/autotrader/config/trading_params.py`

既存の `TradingParams` / `DEFAULT_TRADING_PARAMS` には一切手を加えない。
末尾に以下を追記する。

### 追加内容

#### `SymbolPreset` dataclass

全プリセットパラメータを保持する frozen dataclass。
`to_trading_params()` で後方互換変換を提供。

```python
@dataclass(frozen=True)
class SymbolPreset:
    symbol: str = "USDJPY"
    pip_value: float = 100.0
    spread_pips: float = 1.5
    slippage_pips: float = 0.5
    default_sl_pips: float = 20.0
    default_tp_pips: float = 40.0
    min_lot: float = 0.01
    max_lot: float = 10.0
    commission_per_lot: float = 0.0
    max_positions: int = 2
    bonus_max_positions: int = 1
    bonus_score_threshold: float = 7.0
    base_risk_pct: float = 0.02
    max_lot_per_trade: float = 2.0
    max_total_exposure_lot: float = 5.0
    equity_floor_pct: float = 0.30

    def to_trading_params(self) -> TradingParams:
        ...
```

#### モジュールレベルキャッシュと関数

```python
_DEFAULT_PRESET_PATH = Path(__file__).resolve().parents[4] / "config" / "symbol_presets.yaml"
_preset_cache: dict[str, SymbolPreset] = {}
_presets_loaded: bool = False

def _load_presets(path: Path | None = None) -> None: ...
def get_preset(symbol: str, path: Path | None = None) -> SymbolPreset: ...
def reload_presets(path: Path | None = None) -> None: ...
```

- `_load_presets`: YAML の `defaults` + `symbols[X]` をマージして `_preset_cache` に格納
- `get_preset`: キャッシュヒットで即返し、未定義シンボルは `USDJPY` デフォルト相当を返す
- `reload_presets`: テスト/設定変更後のキャッシュクリア用

---

## Step 3: `src/autotrader/config/__init__.py`

`SymbolPreset`, `get_preset`, `reload_presets` を既存 import ブロックに追記してエクスポート。

---

## Step 4: `src/autotrader/backtest/runner.py`

`BacktestConfig` クラスに `from_preset()` クラスメソッドを追加。
既存コンストラクタは変更しない（後方互換性維持）。

```python
@classmethod
def from_preset(
    cls,
    symbol: str,
    preset_path: Path | None = None,
    **overrides: Any,
) -> "BacktestConfig":
    """シンボルプリセットから BacktestConfig を生成

    プリセット値をデフォルトとして使用し、
    overrides で任意フィールドを上書きできる。
    """
    preset = get_preset(symbol, preset_path)
    kwargs: dict[str, Any] = {
        "symbol": symbol,
        "spread_pips": preset.spread_pips,
        "slippage_pips": preset.slippage_pips,
        "pip_value": preset.pip_value,
        "max_positions": preset.max_positions,
        "bonus_max_positions": preset.bonus_max_positions,
        "bonus_score_threshold": preset.bonus_score_threshold,
    }
    kwargs.update(overrides)
    return cls(**kwargs)
```

---

## Step 5: `src/autotrader/backtest/service.py`

`BacktestServiceConfig` に同様の `from_preset()` クラスメソッドを追加。

```python
@classmethod
def from_preset(
    cls,
    symbol: str,
    preset_path: Path | None = None,
    **overrides: Any,
) -> "BacktestServiceConfig":
    preset = get_preset(symbol, preset_path)
    kwargs: dict[str, Any] = {
        "symbol": symbol,
        "spread_pips": preset.spread_pips,
        "slippage_pips": preset.slippage_pips,
    }
    kwargs.update(overrides)
    return cls(**kwargs)
```

---

## Step 6: `scripts/run_backtest.py`

2 箇所で `DEFAULT_TRADING_PARAMS` を直接参照している部分を
プリセットに置き換える。

### 修正箇所 1（L802付近 - 通常バックテスト）

```python
# 変更前
config = BacktestServiceConfig(
    ...
    # spread_pips / slippage_pips はデフォルト値（DEFAULT_TRADING_PARAMS）
)

# 変更後
from autotrader.config.trading_params import get_preset as _get_preset
_preset = _get_preset(args.symbol)
config = BacktestServiceConfig(
    ...
    spread_pips=_preset.spread_pips,
    slippage_pips=_preset.slippage_pips,
)
# --spread/--slippage の明示指定は引き続き上書き可能（既存コード維持）
```

### 修正箇所 2（L1187付近 - ウォークフォワード用 SimulatorConfig）

```python
# 変更前
sim_config = SimulatorConfig(
    spread_pips=DEFAULT_TRADING_PARAMS.spread_pips,
    pip_value=DEFAULT_TRADING_PARAMS.pip_value,
    ...
)

# 変更後
_preset = _get_preset(args.symbol)
sim_config = SimulatorConfig(
    spread_pips=_preset.spread_pips,
    pip_value=_preset.pip_value,
    ...
)
```

---

## Step 7: `src/autotrader/config/config_loader.py`

`load_live_config()` に `symbol` キー対応を追加。
YAML に `symbol` キーがある場合、そのプリセットをデフォルト値として
`bot_config` と `pm_config` にマージする。
YAML で明示された値が常に優先される。

```python
def load_live_config(...):
    ...
    raw = yaml.safe_load(f) or {}

    # シンボルプリセット取得（symbol キーがあれば適用）
    symbol = raw.get("symbol", "USDJPY")
    preset = get_preset(symbol)

    # プリセット値をデフォルトとして使用（YAML 明示値で上書き）
    preset_bot_defaults = {
        "base_risk_pct": preset.base_risk_pct,
        "max_lot_per_trade": preset.max_lot_per_trade,
        "max_total_exposure_lot": preset.max_total_exposure_lot,
        "equity_floor_pct": preset.equity_floor_pct,
    }
    preset_pm_defaults = {
        "spread_pips": preset.spread_pips,
        "slippage_pips": preset.slippage_pips,
    }

    bot_data = {**preset_bot_defaults, **(raw.get("bot_config", {}) or {})}
    pm_data = {**preset_pm_defaults, **(raw.get("pm_config", {}) or {})}
    ...
```

---

## Step 8: `config/live_trading.yaml`

先頭に `symbol` キーを追加するのみ。既存の設定はすべて維持。

```yaml
# 対象通貨ペア（プリセット自動適用）
symbol: USDJPY

bot_config:
  ...（既存のまま）
```

---

## Step 9: `tests/unit/config/test_symbol_preset.py`（新規）

TDD に基づいて先にテストを書く。

| テストケース | 内容 |
|------------|------|
| `test_get_preset_usdjpy` | USDJPY プリセットの各フィールドを検証 |
| `test_get_preset_unknown_symbol` | 未定義シンボルはデフォルト値を返す |
| `test_get_preset_with_custom_yaml` | `path` 引数でテスト用 YAML を注入できる |
| `test_reload_presets` | `reload_presets()` でキャッシュがリセットされる |
| `test_to_trading_params` | `to_trading_params()` が正しい値を返す |
| `test_backtest_config_from_preset` | `BacktestConfig.from_preset()` の検証 |
| `test_backtest_config_from_preset_overrides` | `**overrides` でフィールドを上書きできる |

---

## 検証手順

```bash
# 1. テスト実行（全 PASS を確認）
python -m pytest tests/unit/config/test_symbol_preset.py -v

# 2. 既存テスト全体が壊れていないことを確認
python -m pytest tests/ -x -q

# 3. EURUSD バックテストで pip 値・スプレッドがプリセット値で動作確認
python scripts/run_backtest.py --symbol EURUSD --years 2024 --no-parallel

# 4. USDJPY は従来どおり動作することを確認
python scripts/run_backtest.py --symbol USDJPY --years 2024 --no-parallel

# 5. --spread で明示上書きが機能することを確認
python scripts/run_backtest.py --symbol EURUSD --spread 0.8 --years 2024 --no-parallel
```

---

## 後方互換性

- `TradingParams` / `DEFAULT_TRADING_PARAMS` は変更しない
- `BacktestConfig()` / `BacktestServiceConfig()` の既存コンストラクタは変更しない
- YAML に `symbol` キーがなくても `load_live_config()` は従来どおり動作する
- 未定義シンボルは USDJPY 相当のデフォルト値にフォールバックする
