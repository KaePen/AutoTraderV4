# バックテストモジュール統合リファクタリング計画

## 概要

WebUI用とCLI用のバックテストモジュールを統一し、複数時間足判断とトレード制御を専用モジュールに切り出すことで、コード管理を簡略化します。

## 現状の問題点

1. **設定クラスの重複**: `BacktestConfig`が`runner.py`と`engine.py`に存在
2. **runner.pyの肥大化**: 1006行で責務過多（設定、実行、結果フォーマット）
3. **実行ループの重複**: `_run_year`と`_run_unified_year`で類似ロジック
4. **グローバル状態管理**: `_backtest_state`辞書でキャンセル状態を管理
5. **フィルターロジックの混在**: `trade_bot.py`内にADXフィルター、HTFトレンド整合性ロジックが埋め込み

## リファクタリング計画

### Phase 1: 設定クラス統一 (config.py新設)

**目的**: 重複する設定クラスを統合

**作成ファイル**: `src/autotrader/backtest/config.py`

```python
@dataclass
class UnifiedBacktestConfig:
    """統一バックテスト設定"""
    symbol: str
    start_year: int
    end_year: int
    initial_balance: float = 1_000_000
    leverage: float = 25.0
    position_size_pct: float = 0.02
    use_short_timeframe: bool = True  # M5ベース
    preset: PresetType = PresetType.BALANCED
    consensus: ConsensusType = ConsensusType.MAJORITY

@dataclass
class BacktestResult:
    """統一バックテスト結果"""
    trades: list[Trade]
    monthly_results: list[MonthlyResult]
    metrics: BacktestMetrics
```

**変更対象**:
- `runner.py`: 内部の`BacktestConfig`を削除、新`UnifiedBacktestConfig`をインポート
- `engine.py`: 内部の`BacktestConfig`を削除
- `service.py`: `BacktestServiceConfig`を`UnifiedBacktestConfig`に統合

### Phase 2: インジケータ計算の抽出 (indicators.py新設)

**目的**: インジケータ計算ロジックを独立モジュールに

**作成ファイル**: `src/autotrader/backtest/indicators.py`

```python
class IndicatorCalculator:
    """マルチタイムフレームインジケータ計算"""

    def calculate_all_timeframes(
        self,
        data_dict: dict[str, pd.DataFrame]
    ) -> dict[str, pd.DataFrame]:
        """全時間足のインジケータを計算"""

    def calculate_single(
        self,
        df: pd.DataFrame
    ) -> pd.DataFrame:
        """単一時間足のインジケータを計算"""
```

**変更対象**:
- `runner.py`: `_prepare_data_for_unified()`からインジケータ計算部分を抽出

### Phase 3: 結果フォーマッタ作成 (formatters.py新設)

**目的**: CLI/WebUI向け出力フォーマットを分離

**作成ファイル**: `src/autotrader/backtest/formatters.py`

```python
from abc import ABC, abstractmethod

class ResultFormatter(ABC):
    """結果フォーマッタ基底クラス"""

    @abstractmethod
    def format_metrics(self, metrics: BacktestMetrics) -> str:
        pass

    @abstractmethod
    def format_monthly(self, results: list[MonthlyResult]) -> str:
        pass

class CLIFormatter(ResultFormatter):
    """CLI用フォーマッタ（カラー出力対応）"""

class JSONFormatter(ResultFormatter):
    """WebUI用JSONフォーマッタ"""
```

**変更対象**:
- `runner.py`: `_print_results()`、`_print_monthly_results()`をフォーマッタに移行
- `scripts/run_backtest.py`: `CLIFormatter`を使用

### Phase 4: シミュレーションループ統一 (engine.py強化)

**目的**: 重複する実行ループを統合、Protocol基盤のシグナル生成

**変更ファイル**: `src/autotrader/backtest/engine.py`

```python
from typing import Protocol

class SignalGenerator(Protocol):
    """シグナル生成プロトコル"""
    def generate(
        self,
        current_time: datetime,
        data_dict: dict[str, pd.DataFrame]
    ) -> Signal | None:
        ...

class BacktestEngine:
    """統一バックテストエンジン"""

    def __init__(
        self,
        config: UnifiedBacktestConfig,
        signal_generator: SignalGenerator,
        event_emitter: BacktestEventEmitter | None = None
    ):
        self.config = config
        self.signal_generator = signal_generator
        self.event_emitter = event_emitter

    def run(self, data_dict: dict[str, pd.DataFrame]) -> BacktestResult:
        """統一実行メソッド"""
```

**アダプタ作成**:

```python
class LegacyGeneratorAdapter:
    """既存SignalGeneratorをProtocolに適合"""

class UnifiedBotAdapter:
    """UnifiedTradeBotをProtocolに適合"""
```

**変更対象**:
- `runner.py`: `_run_year`と`_run_unified_year`を`BacktestEngine.run()`に統合

### Phase 5: フィルターロジック抽出

**目的**: トレード制御フィルターを専用モジュールに切り出し

**作成ファイル**: `src/autotrader/constraint/filters/trend_filter.py`

```python
class TrendFilter:
    """HTFトレンド整合性フィルター"""

    def __init__(self, htf_timeframes: list[str] = ["H4", "D1"]):
        self.htf_timeframes = htf_timeframes

    def is_aligned(
        self,
        signal_direction: str,
        htf_data: dict[str, pd.DataFrame]
    ) -> bool:
        """上位時間足とのトレンド整合性を確認"""

class ADXFilter:
    """ADXベースのトレンド強度フィルター"""

    def __init__(self, threshold: float = 25.0):
        self.threshold = threshold

    def is_strong_trend(self, adx_value: float) -> bool:
        """トレンド強度を確認"""
```

**変更対象**:
- `decision/unified/trade_bot.py`: フィルターロジックを`TrendFilter`、`ADXFilter`に委譲

### Phase 6: 状態管理クラス化

**目的**: グローバル状態をクラスベース管理に変更

**変更ファイル**: `src/autotrader/backtest/runner.py`

```python
class BacktestStateManager:
    """バックテスト状態管理"""

    def __init__(self):
        self._states: dict[str, BacktestState] = {}

    def create(self, backtest_id: str) -> BacktestState:
        """新規状態作成"""

    def get(self, backtest_id: str) -> BacktestState | None:
        """状態取得"""

    def cancel(self, backtest_id: str) -> bool:
        """キャンセル要求"""

    def cleanup(self, backtest_id: str) -> None:
        """状態クリーンアップ"""
```

## 実装順序と依存関係

```
Phase 1 (config.py)
    ↓
Phase 2 (indicators.py) ← Phase 1に依存
    ↓
Phase 3 (formatters.py) ← Phase 1に依存
    ↓
Phase 4 (engine.py統合) ← Phase 1, 2に依存
    ↓
Phase 5 (filters抽出) ← 独立して実行可能
    ↓
Phase 6 (状態管理) ← Phase 4完了後
```

## 最終的なモジュール構成

```
src/autotrader/
├── backtest/
│   ├── __init__.py
│   ├── config.py          # [新規] 統一設定・結果クラス
│   ├── engine.py          # [強化] 統一実行エンジン
│   ├── indicators.py      # [新規] インジケータ計算
│   ├── formatters.py      # [新規] 結果フォーマッタ
│   ├── runner.py          # [軽量化] オーケストレーションのみ
│   ├── service.py         # [簡略化] 外部インターフェース
│   ├── simulator.py       # [維持] トレード実行
│   └── events.py          # [維持] イベント発行
├── constraint/
│   └── filters/
│       ├── __init__.py
│       ├── trend_filter.py  # [新規] HTFトレンドフィルター
│       └── adx_filter.py    # [新規] ADXフィルター
└── decision/
    └── unified/
        ├── trade_bot.py     # [軽量化] フィルターを外部委譲
        └── signal_consolidator.py  # [維持]
```

## 期待される効果

1. **コード重複削減**: 設定クラス統一で約200行削減
2. **保守性向上**: 単一責任原則に基づくモジュール分割
3. **テスト容易性**: Protocol基盤で各コンポーネントをモック可能
4. **拡張性**: 新しいシグナル生成器やフィルターを容易に追加可能
5. **WebUI/CLI統一**: 同一エンジンを共有、出力フォーマットのみ分岐

## リスクと対策

| リスク | 対策 |
|--------|------|
| 既存テストの破壊 | 各Phase後にテスト実行、段階的移行 |
| パフォーマンス劣化 | Protocol使用箇所のベンチマーク |
| 後方互換性 | 既存インターフェースを一時的に維持 |

## 作業見積もり

- Phase 1: 設定統一 - 中規模
- Phase 2: インジケータ抽出 - 小規模
- Phase 3: フォーマッタ作成 - 小規模
- Phase 4: エンジン統合 - 大規模（最重要）
- Phase 5: フィルター抽出 - 中規模
- Phase 6: 状態管理 - 小規模
