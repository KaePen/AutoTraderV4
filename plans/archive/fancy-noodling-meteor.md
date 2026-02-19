# ライブトレード統合改修: 設定注入・prod/test分離・WebUI制御

## Context

バックテストで検証済みのトレードロジック（`PositionManagerConfig` 35+フィールド、`UnifiedBotConfig`）が
ライブトレードエンジンに正しく注入されていない。3つの課題を解決する:

1. **設定注入ギャップ**: `LiveTradingEngine`が`PositionManager()`を引数なしで生成（デフォルト固定）
2. **prod/test分離**: 本番設定をYAMLで永続化。バックテスト（CLI引数）と独立管理
3. **WebUI統合**: 設定更新をライブエンジンにランタイム反映。auto-trade制御は既存実装を活用

## 変更ファイル一覧

| # | ファイル | 変更 | Phase |
|---|---------|------|-------|
| 1 | `pyproject.toml` | `pyyaml>=6.0` 依存追加 | 1 |
| 2 | `src/autotrader/config/config_loader.py` | **新規**: YAML設定ローダー | 1 |
| 3 | `config/live_trading.yaml` | **新規**: 本番用設定ファイル | 1 |
| 4 | `src/autotrader/live/config.py` | `pm_config`フィールド追加 | 2 |
| 5 | `src/autotrader/live/engine.py` | PM設定注入 + `update_pm_config/update_bot_config` | 2 |
| 6 | `src/autotrader/decision/unified/position_manager.py` | `update_config()`メソッド追加 | 2 |
| 7 | `src/autotrader/web/services/settings_service.py` | シングルトン化 + YAML読込 + エンジン連携 | 3 |
| 8 | `src/autotrader/web/routers/settings.py` | `Depends`でシングルトン注入 | 3 |
| 9 | `src/autotrader/web/main.py` | lifespan内でConfigLoader統合 | 3 |
| 10 | `src/autotrader/web/schemas/responses.py` | PM全フィールド露出 | 3 |
| 11 | `tests/unit/config/test_config_loader.py` | **新規**: ConfigLoaderテスト | 4 |
| 12 | `tests/unit/decision/unified/test_position_manager.py` | `update_config`テスト追加 | 4 |

## Phase 1: 設定基盤（ConfigLoader + YAML）

### 1-1. `pyproject.toml` — 依存追加
```
dependencies に "pyyaml>=6.0" を追加
```

### 1-2. `config/live_trading.yaml` — 本番設定ファイル（デフォルト値）
```yaml
# ライブトレード用設定（本番環境）
# バックテストはCLI引数で別管理

bot_config:
  range_day_bbw_threshold: 0.20
  range_day_score_premium: 0.55
  weak_hours_enabled: true
  weak_hours_score_premium: 0.5
  tokyo_night_swing_enabled: true
  tokyo_night_swing_premium: 0.3
  use_dynamic_lot: true
  base_risk_pct: 0.02
  max_lot_per_trade: 2.0
  max_total_exposure_lot: 5.0
  equity_floor_pct: 0.30
  slippage_buffer_pips: 2.0

pm_config:
  # 基本
  partial_close_1r_ratio: 0.3
  partial_close_2r_ratio: 0.3
  breakeven_at_1r: true
  trailing_start_r: 2.0
  trailing_atr_multiplier: 2.0
  spread_pips: 1.5
  slippage_pips: 0.5
  # BE制御
  range_day_be_disabled: true
  range_day_early_be_r: 0.3
  range_day_fast_be_enabled: true
  range_day_fast_be_minutes: 90.0
  # Stagnation
  stagnation_exit_minutes: 120.0
  stagnation_min_mfe_r: 0.15
  swing_stagnation_exit_minutes: 120.0
  swing_stagnation_min_mfe_r: 0.15
  swing_trend_stagnation_enabled: true
  swing_trend_stagnation_exit_minutes: 90.0
  swing_trend_stagnation_min_mfe_r: 0.15
  # 保険
  range_day_insurance_enabled: true
  insurance_trigger_r: 1.0
  insurance_block_high_mfe_r: 0.8
  insurance_min_holding_minutes: 15.0
  # 0.5R部分利確
  range_day_half_r_partial_enabled: true
  range_day_half_r_partial_ratio: 0.20
  range_day_half_r_trigger: 0.5
```

### 1-3. `src/autotrader/config/config_loader.py` — 新規

```python
class ConfigLoader:
    """YAML設定ファイルローダー"""

    def __init__(self, config_dir: Path | None = None)
    def load_live_config(self, filename="live_trading.yaml")
        -> tuple[UnifiedBotConfig, PositionManagerConfig]
    def save_pm_config(self, pm_config, filename=...) -> None
    def save_bot_config(self, bot_config, filename=...) -> None
```

設計ポイント:
- `yaml.safe_load` で安全な読み込み
- `dataclasses.fields()` で有効フィールドのみ抽出（typo無視）
- 保存時: YAML全体を読み→対象セクションのみ差し替え→書き戻し
- `be_enabled_modes` (tuple) はYAMLのlist↔tuple変換を処理
- ファイルなし時はデフォルト値でフォールバック（warning log）

## Phase 2: LiveEngine設定注入

### 2-1. `src/autotrader/live/config.py`
```python
# 追加
from autotrader.decision.unified.position_manager import (
    PositionManagerConfig,
)

@dataclass(frozen=True)
class LiveTradingConfig:
    # ...既存フィールド...
    pm_config: PositionManagerConfig = field(
        default_factory=PositionManagerConfig
    )
```

### 2-2. `src/autotrader/live/engine.py`
```python
# __init__: L70
self._pm = PositionManager(config.pm_config)  # 引数なし → config注入

# 新規メソッド
def update_pm_config(self, new_config: PositionManagerConfig) -> None:
    """PM設定をランタイム差し替え"""
    self._pm.update_config(new_config)

def update_bot_config(self, new_config: UnifiedBotConfig) -> None:
    """Bot設定をランタイム差し替え（Bot+Sizer再構築）"""
    self._bot = UnifiedTradeBot(new_config)
    self._sizer = PositionSizer(new_config)
```

### 2-3. `src/autotrader/decision/unified/position_manager.py`
```python
# PositionManagerクラスに追加
def update_config(self, new_config: PositionManagerConfig) -> None:
    """設定を差し替え（参照スワップ）

    管理中ポジション・内部状態は維持。
    """
    self.config = new_config
```

安全性: `self.config` は通常の属性代入。`PositionManager`自体は非frozenクラス。
asyncio単一タスクのため`evaluate()`中の差し替えは発生しない。ロック不要。

## Phase 3: WebUI統合

### 3-1. `src/autotrader/web/services/settings_service.py` — シングルトン化

```python
_instance: SettingsService | None = None

def get_settings_service() -> SettingsService:
    global _instance
    if _instance is None:
        _instance = SettingsService(get_settings())
    return _instance

class SettingsService:
    def __init__(self, settings, config_loader=None):
        self._config_loader = config_loader or ConfigLoader()
        self._engine = None
        # YAMLから初期設定読み込み
        self._bot_config, self._pm_config = (
            self._config_loader.load_live_config()
        )

    def set_engine(self, engine) -> None:
        """lifespan内でエンジン参照を設定"""
        self._engine = engine

    def update_settings(self, request) -> SettingsResponse:
        """設定更新 → エンジン反映 + YAML永続化"""
        # frozen dataclassは asdict → update → 再構築
        # self._engine.update_pm_config(new_pm_config)
        # self._config_loader.save_pm_config(new_pm_config)
```

### 3-2. `src/autotrader/web/routers/settings.py`
```python
# 毎回 SettingsService(settings) 生成 → Depends 注入に変更
@router.get("/settings")
async def get_settings(
    service: SettingsService = Depends(get_settings_service),
):
```

### 3-3. `src/autotrader/web/main.py` — lifespan改修
```python
# ConfigLoaderで設定読み込み
loader = ConfigLoader()
bot_config, pm_config = loader.load_live_config()

# LiveTradingConfig構築（YAML + 環境変数MT5設定をマージ）
live_config = LiveTradingConfig(
    bot_config=bot_config,
    pm_config=pm_config,
    mt5_config=mt5_config,  # 環境変数から
)

engine = LiveTradingEngine(live_config)
app.state.live_engine = engine

# SettingsServiceにエンジン参照を設定
svc = get_settings_service()
svc.set_engine(engine)
```

### 3-4. `src/autotrader/web/schemas/responses.py`
- `PositionManagementConfigResponse` を6フィールド → 全PM設定フィールドに拡張

## Phase 4: テスト

### `tests/unit/config/test_config_loader.py` (新規)
- YAMLファイルなし → デフォルト値
- 正常なYAML読み込み → Config構築
- 不明キーが無視される
- save_pm_config → YAML永続化 → 再読み込みで一致

### `tests/unit/decision/unified/test_position_manager.py` (追記)
- `update_config` で設定差し替え後の evaluate 動作確認
- 既存ポジション状態が維持される

## Auto-trade制御（既存実装の確認結果）

現状 `engine.py:197` で:
```python
if self._enable_auto_trade:
    await self._execute_entry(signal)
await self._manage_positions()  # 常時実行
```

**設計は正しい**: エントリーのみがauto_tradeでゲートされ、既存ポジション管理は常時動作。
WebUIの `POST /api/v1/trading/auto-trade?enable=true/false` が `engine.enable_auto_trade` セッターを呼ぶ。
→ 追加実装不要。コメント明確化のみ。

## prod/test分離の実現

| 環境 | 設定ソース | 変更方法 |
|------|-----------|---------|
| 本番（ライブ） | `config/live_trading.yaml` | WebUI設定画面 → YAML自動保存 |
| 検証（バックテスト） | CLI引数 (`scripts/run_backtest.py`) | コマンドライン指定 |

- 両者は完全に独立。バックテストのパラメータ変更がライブに影響しない
- バックテストで良い結果が出たら、WebUIの設定画面またはYAML直接編集で本番に反映

## 検証方法

1. `pytest tests/unit/config/test_config_loader.py -v` — ConfigLoaderテスト
2. `pytest tests/unit/decision/unified/test_position_manager.py -v` — PM update_configテスト
3. `pytest tests/ -x --override-ini="addopts="` — 全体回帰テスト
4. `python scripts/run_backtest.py --years 2023 --help` — バックテストCLI引数に影響なし確認
