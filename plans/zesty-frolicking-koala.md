# AutoTraderV4 アーキテクチャ振り返り — 1からやり直すなら

## 前提

AutoTraderV4は「バックテストとリアルトレードでロジック共用」という正しい原則の上に構築されており、
`core/`, `calculator/`, `constraint/`, `decision/` の責務分離は全体として優れている。
以下は「完成した今の知見があった上で、最初からやるなら」という視点での指摘。

---

# Part 1: 構造・設計レベルの問題（12項目）

## 1. God Config 問題 — UnifiedBotConfig の271+フィールド

**現状の問題:**
- `UnifiedBotConfig` が271+フィールドのモノリスで、シグナル生成・リスク管理・フィルター・資金管理・レガシー設定が全て混在
- PR #401 でさらに `off_hours_high_align_block`, `off_hours_high_align_threshold`, `trend_sl_max_pips` 等5フィールドが追加され、肥大化が加速中
- `PositionSizerConfig` (60+), `PositionManagerConfig` (20+), `ConsensusConfig`, `EvaluatorConfig` 等20個の Config クラスが独立して存在し、どれが優先かが不明確
- CLI引数（75+オプション）との手動同期が必要（PR #401 で4引数追加）

**やり直すなら:**
```
SignalConfig        — シグナル生成・コンセンサス関連のみ
RiskConfig          — SL/TP・資金管理・ポジションサイジング
FilterConfig        — レジームフィルター・時間帯フィルター
SymbolConfig        — 通貨ペア固有パラメータ（現symbol_presets.yaml統合）
```
4つの責務別Configに分割。CLI引数はConfigフィールドから自動生成（`dataclasses.fields()` + argparse）。

**ファイル:** `autotrader/decision/unified/config.py` (271フィールドの巨大dataclass)

---

## 2. 設定の二重管理 — symbol_presets.yaml / live_trading.yaml / コードデフォルト

**現状の問題（PR #398 で一部改善済み）:**
- PR #398 で `spread_pips`, `base_risk_pct`, `max_lot_per_trade`, `max_positions` 等のリスク系パラメータが `symbol_presets.yaml` に一元化された（SSOT化）
- ただし `live_trading.yaml` にはまだ `consensus_threshold`, `penalties`, `bot_config` 等のシグナル系パラメータが残存
- **依然として二重管理**: symbol_presets.yaml（リスク系）+ live_trading.yaml（シグナル系）+ コードデフォルト（UnifiedBotConfig）
- **EURJPY の bca_min_edge=0.65 を通貨ペア別に適用できない**（UnifiedBotConfig が通貨ペア非依存のため）

**やり直すなら:**
- **設定の完全な単一ソース**: `symbol_presets.yaml` のみ。live_trading.yaml を完全廃止
- **通貨ペア別オーバーライド**: `SymbolConfig` が通貨ペア固有パラメータを全て保持（シグナル系含む）
- **コードデフォルト値は一切持たない**: YAML未定義 = エラー（暗黙のデフォルトを排除）

```yaml
# symbol_presets.yaml（唯一の設定ファイル）
defaults:
  signal:
    consensus_threshold: 9.0
    bca_min_edge: 0.55
  risk:
    base_risk_pct: 0.025
    max_lot_per_trade: 2.5

symbols:
  USDJPY:
    signal:
      bca_min_edge: 0.55
    risk:
      spread_pips: 1.5
  EURJPY:
    signal:
      bca_min_edge: 0.65  # EURJPY固有
    risk:
      spread_pips: 2.0
```

**ファイル:** `config/symbol_presets.yaml`, `config/live_trading.yaml`, `autotrader/config/config_loader.py`

---

## 3. Signal型の乱立 — 8種類のシグナル型

**現状の問題:**
- `core.entities.Signal` (最終), `TimeframeSignal` (3種類!), `ConsolidatedSignal`, `MultiModeSignal`, `ModeSignal`, `SignalResult`, `SignalEvent`
- 同名 `TimeframeSignal` が `mode_aware_consensus.py` と `timeframe_evaluator.py` に別々に定義
- 同じ「スコア」が `confidence`, `buy_score`, `sell_score`, `scores`, `htf_alignment`, `penalty_total` 等に分散
- 計算パイプラインでどのスコアが何に足されているか追跡困難

**やり直すなら:**
```python
# 3つだけ
@dataclass(frozen=True)
class TFScore:
    """1つの時間足の評価結果"""
    timeframe: str
    direction: SignalType
    strength: float  # -1.0 ~ 1.0
    indicators: dict[str, float]

@dataclass(frozen=True)
class TradeDecision:
    """最終的なトレード判断（エントリー/エグジット/保留）"""
    action: Action  # ENTER_LONG / ENTER_SHORT / EXIT / HOLD
    confidence: float
    sl_price: float
    tp_price: float
    tf_scores: list[TFScore]
    reason: str

@dataclass(frozen=True)
class Trade:
    """約定済みトレード記録"""
    # 現行のTrade型と同等
```

中間シグナル型は全て `TFScore` に統一。パイプラインは `list[TFScore] → TradeDecision → Trade`。

**ファイル:** `autotrader/core/entities.py`, `autotrader/decision/unified/` 内の各モジュール

---

## 4. スコア計算の散在 — 同じ指標が4ルートで計算

**現状の問題:**
- RSI強度計算が `TimeframeEvaluator._score_rsi` と `IndicatorStrengthCalculator._normalize_rsi` で微妙に異なる閾値で重複
  ```python
  # TimeframeEvaluator: if rsi < 30: score = -1.0
  # StrengthCalculator: if rsi < self.config.rsi_oversold: strength = -(1 - (rsi / 30.0))
  # → 同じRSIなのに正規化方法が違う
  ```
- ATR正規化が `tf_params_registry.py`, `TimeframeEvaluator`, `PositionSizer` の3箇所に存在
- SL/TP計算が `TimeframeEvaluator`, `TradingPlan`, `PositionSizer`, `PositionManager` の4箇所に存在
- 4つの上位層（StrategyPool, TradeBot._evaluate_tfs_parallel, DirectionalEdgeAssessor, FundamentalAssessor）がTimeframeEvaluatorの結果を異なるロジックで再加工

**やり直すなら:**
- **指標スコアリング**: `calculator/scoring/` に集約。1指標 = 1関数。TimeframeEvaluator は呼ぶだけ
- **SL/TP計算**: `calculator/risk/` に1つだけ。PositionManager/Sizer はそこを参照
- **ATR正規化**: `calculator/technical/atr.py` に正規化関数を追加、他は全てそこを参照

---

## 5. LiveTradingEngine のモノリス化 — 2,920行の単一ファイル

**現状の問題:**
- 接続管理、データ取得、シグナル生成、注文実行、DB同期、WebSocket発行が全て1クラス
- asyncio内で同期DB I/O をブロッキング実行（イベントループが止まる）
  ```python
  # engine.py line 1590 付近
  with get_session(db_url) as db:
      repo.close(...)  # blocking I/O → asyncioがここで停止
  ```
- テスト不可能（モック化する接点がない）

**やり直すなら:**
```
live/
├── connection.py        # MT5接続管理・ハートビート（200行）
├── data_feed.py         # リアルタイムデータ取得・キャッシュ（300行）
├── signal_service.py    # UnifiedTradeBot呼び出し・シグナル生成（200行）
├── order_service.py     # 注文実行・約定管理（400行）
├── position_sync.py     # MT5⇔DB ポジション同期（300行）
├── event_publisher.py   # WebSocket/EventBus イベント発行（200行）
└── engine.py            # オーケストレーター（上記を組み合わせ、200行）
```

DB I/O は全て `asyncio.to_thread()` または async ORM (SQLAlchemy 2.0 async) で非同期化。

**ファイル:** `autotrader/live/engine.py` (2,920行)

---

## 6. バックテスト/ライブ間の DataProvider 不統一

**現状の問題:**
- バックテスト: `DataLoader` → CSV → `CandleArrays` → `PrecomputeEngine` (Parquet)
- ライブ: `MT5DataProvider` → `set_market_data()` で UnifiedTradeBot に直接注入
- 同じ `UnifiedTradeBot` を使うが、データの入り口が全く異なる
- `PrecomputeEngine` のキャッシュはライブで活用不可
- `core/interfaces/data_provider.py` にABCがあるが、バックテスト側は無視して直接CSV読み込み

**やり直すなら:**
```python
class DataProvider(ABC):
    """統一データ提供インターフェース"""
    @abstractmethod
    def get_candles(self, symbol: str, tf: str, count: int) -> list[Candle]: ...
    @abstractmethod
    def get_indicators(self, symbol: str, tf: str) -> IndicatorSet: ...

class CSVDataProvider(DataProvider):     # バックテスト用
class MT5DataProvider(DataProvider):     # ライブ用
class CachedDataProvider(DataProvider):  # Parquetキャッシュラッパー（decorator）
```

`UnifiedTradeBot` は `DataProvider` だけを知る。データの出所は関知しない。

**ファイル:** `autotrader/core/interfaces/data_provider.py`, `autotrader/backtest/data_loader.py`

---

## 7. CLI引数の爆発 — run_backtest.py の75+オプション

**現状の問題:**
- `run_backtest.py` が2,036+行で75+の引数を手動定義
- PR #401 でさらに `--off-hours-high-align-block`, `--off-hours-high-align-threshold`, `--trend-sl-max-pips`, `--trend-sl-max-enable` の4引数が追加。肥大化が継続中
- 新パラメータ追加時に「Config定義 → CLI引数追加 → CLI→Config変換 → YAML反映」の4箇所修正が必要
- `--range-stag-trend-minutes`, `--fast-be-threshold` 等の微細なフラグが無秩序に追加
- 5つの実行モード（diagnose, debug_signal, fast, quick, default）が if-elif で分岐

**やり直すなら:**
- **Config-driven CLI**: `SignalConfig` 等のフィールドから argparse を自動生成
- **YAMLファイルベース実行**: `uv run backtest --config experiments/usdjpy_stag90.yaml`
- **差分オーバーライドのみCLI**: `uv run backtest --override signal.consensus_threshold=10.0`
- **サブコマンド**: `uv run backtest run`, `uv run backtest diagnose`, `uv run backtest debug-signal`

```python
# 自動生成の仕組み
def config_to_argparse(config_cls: type) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    for f in dataclasses.fields(config_cls):
        parser.add_argument(f"--{f.name.replace('_', '-')}", type=f.type, default=f.default)
    return parser
```

**ファイル:** `scripts/run_backtest.py` (2,036行)

---

## 8. PositionManager の二重存在

**現状の問題:**
- バックテスト: `TradeSimulator` がポジション管理（スプレッド適用、SL/TP判定、P&L計算）
- ライブ: `PositionManager` がポジション管理（トレーリングストップ、分割決済、BE判定）
- 両方が「ポジションの状態管理」を行うが、ロジックが異なる
- バックテストで発見できないライブ固有バグが発生するリスク
- year_runner.py で prev_positions / current_positions 比較が O(n^2)

**やり直すなら:**
- `PositionManager` を唯一のポジション管理とし、バックテスト/ライブ両方で使用
- `TradeSimulator` は「価格シミュレーション」のみに限定（ティック生成、スプレッド適用）
- ポジションの状態変更（トレーリング、BE、分割決済）は常に `PositionManager` 経由

```
TradeSimulator: 「この価格でSLに到達したか？」→ Yes/No
PositionManager: 「SLをどこに動かすか？BEにするか？分割決済するか？」
```

**ファイル:** `autotrader/backtest/simulator.py`, `autotrader/decision/unified/position_manager.py`

---

## 9. decision/unified/ の責務過多 — 33ファイル

**現状の問題:**
- `decision/unified/` に33ファイルが密集
- シグナル生成、ポジション管理、戦略選択、ファンダメンタル評価、適応チューニングが同一パッケージ
- レガシーの `decision/signal_generator.py`, `decision/confidence_calculator.py` 等が残存（unified/ と重複）

**やり直すなら:**
```
decision/
├── signal/              # シグナル生成パイプライン
│   ├── evaluator.py     # TF評価（現TimeframeEvaluator）
│   ├── consensus.py     # コンセンサス計算
│   └── consolidator.py  # シグナル統合
├── strategy/            # 戦略選択
│   ├── pool.py
│   ├── scalp.py
│   ├── swing.py
│   └── selector.py      # モード選択
├── position/            # ポジション管理
│   ├── manager.py       # トレーリング・BE・分割決済
│   └── sizer.py         # ロット計算
├── edge/                # エッジ評価
│   ├── directional.py   # BCA
│   └── fundamental.py   # ファンダメンタル
└── trade_bot.py         # オーケストレーター（上記を組み合わせ）
```

`decision/signal_generator.py` 等のレガシーは廃止。

---

## 10. テスト戦略の欠如

**現状の問題:**
- tests/unit/ に85ファイルあるが、`decision/unified/` と `live/` のカバレッジが不足
- バックテスト結果の再現性テスト（同一入力→同一出力）がない
- 「Config変更→パフォーマンス変化」の回帰テストがない
- PR #402 でSTAGNATION診断データがCSV出力に追加されたのは良い改善（exit_reason_detail, stag_minutes_used, stag_mfe_r_used）。ただし自動テストではなく手動分析用

**やり直すなら:**
- **ゴールデンテスト**: 固定データセット（1ヶ月分）で全トレードを記録、コード変更時に差分検出
- **プロパティベーステスト**: hypothesis でランダムなCandle配列を生成、シグナルの不変条件を検証
- **Config回帰テスト**: 主要パラメータの変更前後でメトリクスを比較、閾値超過でCI fail

```python
def test_backtest_reproducibility():
    result = run_backtest("USDJPY", "2024-01", "2024-01", config=DEFAULT_CONFIG)
    assert result.trades == load_golden("usdjpy_2024_01.json")
    assert abs(result.profit - 150_000) < 1_000  # ±1K tolerance
```

---

## 11. WebSocket/イベントバスの脆弱性

**現状の問題:**
- `ConnectionManager` がプロセス内メモリで接続管理→マルチワーカー不可
- WebSocket切断時のクリーンアップが不完全→長時間運用でメモリリーク
- `EventBus.publish_nowait()` でイベント消失の検知なし

**やり直すなら:**
- Redis Pub/Sub or SSE (Server-Sent Events) でイベント配信
- WebSocket接続はステートレス（再接続時に最新状態をプッシュ）
- イベントの永続化（最低限、直近N件をメモリに保持）

---

## 12. async/sync 混在

**現状の問題:**
- `LiveTradingEngine` は async だが、MT5 API は同期ブロッキング
- DB I/O が `with get_session()` で同期実行→asyncioイベントループをブロック
- `adapters/mt5/connection.py` が async def だが中身は同期呼び出し

**やり直すなら:**
- MT5 API呼び出しは全て `asyncio.to_thread()` でラップ
- DB は SQLAlchemy 2.0 AsyncSession を使用
- async/syncの境界を明確に: adaptersレイヤーが唯一のsync→async変換ポイント

---

# Part 2: コード品質・設計パターンの問題（10項目）

## 13. ロギング戦略の分裂 — loguru vs logging の混在

**現状の問題:**
- **42ファイル**で `logging.getLogger(__name__)` を使用（core, backtest, web, mt5）
- **17ファイル**で `from loguru import logger` を使用（adapters/fundamental/）
- 同一プロジェクト内で2つのロギングフレームワークが共存
- ログ設定が一元管理されていない（レベル・フォーマットがファイルごとに異なる）
- 構造化ログ（JSON出力）が未実装→メトリクス分析・ログ検索が困難

**やり直すなら:**
- **単一ライブラリ**: logging (stdlib) に統一。loguru は全廃止
- **構造化ログ**: `structlog` または `python-json-logger` でJSON出力
- **一元設定**: `config/logging.yaml` で全モジュールのレベルを一括管理
- **コンテキスト付きログ**: `symbol`, `timeframe`, `trade_id` を自動付与

```python
# 全ファイル統一
import logging
logger = logging.getLogger(__name__)

# 構造化出力
logger.info("signal_generated", extra={"symbol": "USDJPY", "score": 7.5, "direction": "BUY"})
```

---

## 14. EventBus の型安全性の欠如

**現状の問題:**
- 全イベントが `dict[str, Any]` で型情報が完全消失
  ```python
  # core/event_bus.py:15-18
  EventHandler = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]
  ```
- ハンドラー内のキーtypoが実行時まで検出不可
- イベント登録がlamda/クロージャだと `unsubscribe()` で解除不可（オブジェクト同一性に依存）
- `except RuntimeError: pass` で例外を握り潰し（テスト時のイベントループ不在対策だが危険）

**やり直すなら:**
```python
# 型付きイベント
class TradeOpenedEvent(TypedDict):
    trade_id: str
    symbol: str
    direction: str
    volume: float

# 型安全なEventBus
class TypedEventBus:
    def subscribe(self, event_type: type[T], handler: Callable[[T], Awaitable[None]]) -> str:
        """戻り値: subscription_id（解除用）"""
    def unsubscribe(self, subscription_id: str) -> None: ...
    def publish(self, event: T) -> None: ...
```

---

## 15. BotState / RiskManager の Mutable State 問題

**現状の問題:**
- `BotState` (dataclass, **frozen=False**) が equity, consecutive_losses 等を直接更新
  ```python
  # trade_bot.py:65-116
  @dataclass
  class BotState:
      equity: float = 1_000_000.0
      def update_pnl(self, pnl: float) -> None:
          self.equity += pnl  # ← mutable
  ```
- `RiskManager` も `self._daily_pnl += pnl` で直接更新
- マルチスレッド環境（バックテスト並列実行）で race condition のリスク
- 状態変更の追跡が困難（いつ・誰が equity を変更したか不明）

**やり直すなら:**
- **イミュータブル状態 + イベント**: 状態変更は新しいスナップショットを返す
  ```python
  @dataclass(frozen=True)
  class BotState:
      equity: float
      def with_pnl(self, pnl: float) -> BotState:
          return replace(self, equity=self.equity + pnl)
  ```
- または **Event Sourcing**: 全状態変更をイベントとして記録、再生可能に

---

## 16. インターフェースの空振り — 定義だけで未実装

**現状の問題:**
- `core/interfaces/` に6つのABCを定義しているが、多くが実装されていない:

  | インターフェース | 実装数 | 状況 |
  |-------------|------|------|
  | `IndicatorCalculator` | 0 | 空振り。calculator/で直接実装 |
  | `FeatureCalculator` | 0 | 空振り。features/で直接実装 |
  | `ConstraintCheckerInterface` | 0 | 空振り。HardGuard/SoftGuardは独自API |
  | `SignalGeneratorInterface` | ~3 | 実装はあるが契約が守られていない |
  | `DataProvider` | 1 (MT5のみ) | バックテスト側は無視 |

- `constraint/filters/adx_filter.py` の `ADXFilter` は `Guard` ABCを実装せず独自メソッド `is_strong_trend()` を定義
- インターフェースの存在意義がない（型安全性もDIも提供できていない）

**やり直すなら:**
- **Protocol (構造的サブタイピング)** に統一。ABCを廃止
- 実装側が明示的に継承しなくても、メソッドシグネチャが一致すれば型チェック通過
- 使わないインターフェースは削除（YAGNI原則）
  ```python
  class DataProvider(Protocol):
      def get_candles(self, symbol: str, tf: str, count: int) -> list[Candle]: ...
  ```

---

## 17. Strategy パターンのコード重複

**現状の問題:**
- Scalp, ShortMid, Swing の3戦略で `_get_regime_fit_factor()` が完全に同一のコード（各~30行）
- `DEFAULT_CONFIG` が各戦略ファイルに独立定義（DRY違反）
- 新戦略追加時に `StrategyPool.__init__` にハードコードで追加が必要
  ```python
  # strategy_pool.py:46-50 — 拡張に閉じていない
  self._strategies = [ScalpStrategy(), ShortMidStrategy(), SwingStrategy(), NoTradeStrategy()]
  ```

**やり直すなら:**
- **テンプレートメソッドパターン**: 共通ロジックを `BaseStrategy` に移動
- **戦略レジストリ**: デコレータで自動登録
  ```python
  @register_strategy("scalp")
  class ScalpStrategy(BaseStrategy):
      ...

  # StrategyPool は registry から動的に取得
  pool = StrategyPool(strategies=get_registered_strategies())
  ```

---

## 18. Repository パターンの不完全実装

**現状の問題:**
- `TradeRepository.create()` が ORM Model (`TradeRecord`) を直接返却
  ```python
  # repositories.py:16-62
  def create(self, symbol: str, ...) -> TradeRecord:  # ← ORM Model直接
      trade = TradeRecord(trade_id=str(uuid4()), ...)
      self.session.add(trade)
      return trade
  ```
- `TradeRecord`（永続化の詳細）がドメインレイヤーに漏洩
- `adapters/database/models.py` の変更が `core/entities.py` を通さずに上位レイヤーに伝播
- Repositoryパターンの本来の目的（永続化詳細の隠蔽）が達成されていない

**やり直すなら:**
```python
class TradeRepository:
    def create(self, trade: Trade) -> Trade:  # Trade = domain entity
        record = self._to_record(trade)
        self.session.add(record)
        return self._to_entity(record)

    def _to_record(self, trade: Trade) -> TradeRecord: ...
    def _to_entity(self, record: TradeRecord) -> Trade: ...
```

---

## 19. DI (依存性注入) の不在

**現状の問題:**
- 全てのモジュールが具体実装を直接importし、直接インスタンス化
  ```python
  # backtest/engine.py:25-30
  from autotrader.calculator.precompute import PrecomputeEngine  # 具体クラス
  from autotrader.constraint.hard_guard import HardGuard          # 具体クラス
  from autotrader.decision.signal_generator import SignalGenerator  # 具体クラス
  ```
- テスト時にモック注入が困難
- 別の実装に差し替える際にimport先を全て変更する必要がある
- `event_bus = EventBus()` がモジュールレベルsingleton→テスト間で状態が干渉

**やり直すなら:**
- **コンストラクタ注入**: 依存はすべて __init__ の引数で渡す
  ```python
  class BacktestEngine:
      def __init__(self, data_provider: DataProvider, trade_bot: UnifiedTradeBot, ...): ...
  ```
- **Composition Root**: アプリのエントリポイント1箇所でのみインスタンス生成
- singletonは避ける。テストごとに新しいインスタンスを生成

---

## 20. テスタビリティを阻害する隠れた依存

**現状の問題:**

| 問題 | ファイル | 影響 |
|------|---------|------|
| `datetime.now()` 直接呼び出し | 14ファイル（backtest/state.py, executor.py, adaptive/trade_record.py 等） | テスト時に時刻固定不可 |
| グローバルsingleton `event_bus` | core/event_bus.py:91 | テスト間で状態干渉 |
| ファイルシステム直接依存 | calculator/precompute.py（キャッシュパス） | テスト時に固定パス参照 |
| ハードコード定数 | strategies/base.py:390 `SLIPPAGE_PIPS = 0.5` | テスト時に変更不可 |
| Protocol vs ABC 混在 | backtest/engine.py (Protocol) vs core/interfaces/ (ABC) | 型チェック戦略が不統一 |

**やり直すなら:**
- **Clock抽象**: `datetime.now()` → `Clock.now()` で注入可能に
  ```python
  class Clock(Protocol):
      def now(self) -> datetime: ...
  class SystemClock: ...      # 本番用
  class FixedClock: ...       # テスト用
  ```
- **ファイルシステム抽象**: キャッシュI/OをProtocolで抽象化
- **定数はConfig経由**: ハードコード定数は全てConfigフィールドに移動

---

## 21. パイプラインパターンの不在 — シグナル処理フロー

**現状の問題:**
- シグナル生成が `UnifiedTradeBot` 内で直接呼び出しチェーン:
  ```
  TimeframeRouter → TimeframeEvaluator → ModeAwareScoreConsensus
    → StrategySelector → DirectionalEdgeAssessor → FundamentalAssessor
  ```
- 各ステップがハードコードで接続、順序変更・ステップ追加/削除が困難
- ミドルウェア的なフィルタ（ログ、計測、キャッシュ）を挿入できない

**やり直すなら:**
```python
class SignalPipeline:
    def __init__(self, steps: list[PipelineStep]):
        self._steps = steps

    def execute(self, context: PipelineContext) -> TradeDecision:
        for step in self._steps:
            context = step.process(context)
            if context.should_abort:
                break
        return context.to_decision()

# 構築
pipeline = SignalPipeline([
    TimeframeEvalStep(),
    ConsensusStep(),
    EdgeAssessmentStep(),
    FundamentalStep(),  # 簡単にON/OFF可能
])
```

---

## 22. ポジション状態管理のState Machine不在

**現状の問題:**
- ポジション状態遷移が暗黙的（OPEN → SL更新 → 部分決済 → 全決済）
- 不正な遷移（決済済みポジションへのSL更新等）を防ぐメカニズムなし
- `ManagementAction` は結果を表すが、遷移の妥当性を検証しない

**やり直すなら:**
```python
class PositionState(Enum):
    PENDING = "pending"
    OPEN = "open"
    TRAILING = "trailing"
    PARTIAL_CLOSED = "partial_closed"
    CLOSED = "closed"

VALID_TRANSITIONS = {
    PENDING: {OPEN, CLOSED},
    OPEN: {TRAILING, PARTIAL_CLOSED, CLOSED},
    TRAILING: {PARTIAL_CLOSED, CLOSED},
    PARTIAL_CLOSED: {CLOSED},
    CLOSED: set(),  # 終端状態
}
```

---

# Part 3: パフォーマンスの問題（4項目）

## 23. PrecomputeEngine のチャンク処理が非効率

**現状の問題:**
- `precompute_chunked()` で月単位に分割し、各チャンクで独立に指標計算
- 6年データ → 72チャンク → pandas-ta が72回フル実行
- **推定オーバーヘッド**: 360秒（全体一括なら5秒）
- キャッシュ無効化戦略がない（設定ハッシュのみ、データセット変更で陳腐化）

**やり直すなら:**
- 全指標を1回で一括計算（チャンクは出力時のみ分割）
- キャッシュキーにデータセットハッシュ（先頭/末尾N行のダイジェスト）を含める
- pandas-ta → ta-lib に置換（10倍高速、ただしインストール複雑）

---

## 24. DataFrame変換の多段階

**現状の問題:**
- CSV → DataFrame → numpy arrays（CandleArrays.from_dataframe で複製）→ Candle dataclass → dict
- 毎バーで `arrays.get_candle(i, symbol, timeframe)` がオブジェクト生成
- Polars の `load_with_polars()` メソッドが存在するが、メインループでは pandas のみ使用

**やり直すなら:**
- **Polars lazy evaluation**: 必要な列だけ遅延評価で取得
- **オブジェクト生成の回避**: ホットパスではnumpy配列のまま処理、Candle生成は出力時のみ
- **ゼロコピー変換**: Arrow フォーマットで Parquet → Polars → numpy を変換なしで共有

---

## 25. 並列実行のリソース管理不在

**現状の問題:**
- 年単位（ProcessPoolExecutor, max=5）+ 時間足別（ProcessPoolExecutor）+ 月次バッチの3層並列
- 各層が独立してプロセスプールを作成、リソース共有戦略がない
- Pickle による IPC オーバーヘッド（CandleEvent ~1-5MB/イベント）
- 小規模テスト（1年のみ）では並列化の初期化コストが逆効果

**やり直すなら:**
- **統一プロセスプール**: アプリケーションレベルで1つのプールを共有
- **閾値ベースの並列化**: データ量が少ない場合は自動的にシーケンシャル実行
- **共有メモリ**: `multiprocessing.shared_memory` でデータ転送を最小化

---

## 26. ライブエンジンの指標計算が毎ティック実行

**現状の問題:**
- TechnicalIndicatorBatch がティックごとに全指標を再計算
- pandas-ta は同期ブロッキング → asyncioイベントループが停止
- 計算結果のキャッシュなし（同じバーで複数回計算）

**やり直すなら:**
- **バー確定時のみ計算**: 新バー確定をトリガーに指標更新
- **インクリメンタル計算**: 新しいバーのみを既存結果に追加
- **非同期ラッパー**: `asyncio.to_thread()` で指標計算をオフロード

---

# 総合評価

## 現状スコアカード

| 領域 | 現状 | やり直すなら | 最大改善幅 |
|------|:----:|:----------:|:---------:|
| core/ (エンティティ) | A+ | Signal型を3つに統一 | 小 |
| calculator/ | A | スコアリングを集約 | 中 |
| constraint/ | A | Guard Protocol化 | 小 |
| **config管理** | **B-** | **単一YAML + 通貨ペア別** | **大** |
| **decision/unified/** | **B+** | **4サブパッケージ + Pipeline** | **大** |
| backtest/ | A- | DataProvider統一 | 中 |
| **live/** | **B-** | **7モジュール分割 + async化** | **大** |
| web/ | B | Redis Pub/Sub + SSE | 中 |
| **テスト** | **C** | **ゴールデン + property + DI** | **大** |
| **CLI** | **C+** | **Config-driven自動生成** | **大** |
| ロギング | C+ | stdlib統一 + 構造化 | 中 |
| エラーハンドリング | B+ | 型付きEventBus | 小 |
| 型安全性 | B+ | Any削減 + Protocol統一 | 小 |
| DI/テスタビリティ | C | コンストラクタ注入 + Clock抽象 | 大 |
| パフォーマンス | B | Polars + ta-lib + 統一プール | 中 |

## 改善効果の高さでランキング（Top 10）

| 順位 | 項目 | 効果 | 難易度 | 理由 |
|:----:|------|:----:|:------:|------|
| 1 | Config統一 (#1+#2) | S | 中 | 全ての「設定がどこにある？」を解消。通貨ペア別最適化が可能に |
| 2 | Signal型統一 (#3) | S | 高 | パイプラインの追跡性が劇的改善。バグ発見容易化 |
| 3 | LiveEngine分割 (#5) | A | 高 | テスト可能性+運用安定性。async化でティック遅延解消 |
| 4 | DI導入 (#19+#20) | A | 中 | テスタビリティの根本改善。モック注入が可能に |
| 5 | CLI自動生成 (#7) | A | 低 | 新パラメータ追加が1箇所で完結。実験管理が容易に |
| 6 | PositionManager統一 (#8) | B | 高 | バックテスト/ライブの挙動一致保証 |
| 7 | Pipeline パターン (#21) | B | 中 | シグナル処理の柔軟性。ステップのON/OFFが容易 |
| 8 | ゴールデンテスト (#10) | B | 低 | コード変更時の回帰検出。安心してリファクタリング可能 |
| 9 | ロギング統一 (#13) | B | 低 | 運用時のデバッグ効率。構造化ログでメトリクス分析 |
| 10 | DataProvider統一 (#6) | B | 中 | バックテスト/ライブの境界を明確化 |

---

# Part 4: トレード収益に直結する問題（12項目）

## 27. [致命的] SwingAnalyzer の Look-Ahead Bias — 未来データでスイングポイント判定

**確認済みの事実:**
- `swing_analyzer.py` の `lookforward=2` が右側2本の**未来バー**を参照してスイング判定
  ```python
  # precompute.py:261 で呼び出し
  swing_analyzer = SwingAnalyzer(lookback=5, lookforward=2)
  # → 全期間を一括計算、バーiのスイング判定にバーi+1, i+2を使用
  ```
- `right_max[i] = max(high[i+1], high[i+2])` → バックテスト実行時にバーiで「次の2本の高値」を知っている
- リアルトレードでは、バーi時点でバーi+1, i+2は存在しない

**トレード判断への直接影響（timeframe_evaluator.py:462-521）:**
| データ | ボーナス/ペナルティ | 影響度 |
|--------|-------------------|--------|
| `bos_signal`（BOS検出） | ±1.5 | スイングポイント基盤 |
| `choch_signal`（CHoCH検出） | ±2.0 / 逆方向-1.0 | スイングポイント基盤 |
| `structure_direction` | +0.5 / -1.0 | スイングポイント基盤 |
| `last_swing_low/high` | +1.0 | スイング価格直接参照 |

コンセンサス合計が7-12のシステムで、合計 **±5.0 のスコアが汚染** されている可能性。

**修正方法:**
```python
SwingAnalyzer(lookback=5, lookforward=0)  # 未来参照を完全排除
```
ただし lookforward=0 ではスイング検出の品質が低下するため、代替案:
- **遅延確認方式**: バーi のスイング判定をバーi+2 の時点で確定（2バー遅延）
- PrecomputeEngine内で `shift(lookforward)` を全スイング関連カラムに適用

**想定インパクト**: バックテスト成績が **-200K〜-800K 低下** する可能性（バイアス除去による現実化）。
逆に言えば、現在のバックテスト成績はこのバイアスで過大評価されている。

**ファイル:** `autotrader/calculator/market_structure/swing_analyzer.py:56,79-81`, `autotrader/calculator/precompute.py:261`

---

## 28. [重大] DivergenceFeatures にも同じ Look-Ahead Bias

**確認済みの事実:**
```python
# divergence_features.py:57-60
for i in range(n, len(series) - n):
    window = series.iloc[i - n : i + n + 1]  # i+n+1 = 未来n本を参照！
    if series.iloc[i] == window.max():
        swing_highs.iloc[i] = True
```
- `swing_lookback` がそのまま右側にも適用（左右対称ウィンドウ）
- RSIダイバージェンス検出の基盤が未来データで汚染

**修正方法:**
```python
# 左側のみのウィンドウに修正
window = series.iloc[max(0, i - n) : i + 1]
```

**ファイル:** `autotrader/calculator/features/divergence_features.py:55-81`

---

## 29. [重大] use_position_manager のバックテスト/ライブ不整合

**確認済みの事実:**
- `SymbolPreset.use_position_manager` のデフォルト = **False**
- USDJPY: `use_position_manager = False`（デフォルト踏襲）
- GBPUSD: `use_position_manager = True`（プリセット明示）
- バックテスト: PositionManager **無効** → シンプルSL/TPのみ
- ライブ: PositionManager が動作（トレーリングストップ、部分決済、BE判定）

**影響:** バックテストで検証したロジックとライブで実行されるロジックが異なる。
トレーリングストップの効果（+/-）がバックテストで評価されていない。

**修正方法:** バックテストでも `use_position_manager=True` をデフォルトにし、
バックテスト/ライブの挙動を一致させる。

---

## 30. コンセンサス重み付けの静的性 — レジーム無視

**現状:** 全時間足の重みが固定（PRIMARY=3.0, ENTRY=2.5, CONFIRM=2.0, MANAGE=1.5, OTHER=1.0）
- TREND時もRANGE時も同じ重み
- 高ボラティリティ時でも低ボラ時でも同じ重み

**やり直すなら:**
- TREND時: HTF(H4+)重みを+20%、LTF(M15以下)を-20%（ノイズ抑制）
- RANGE時: 全TF均等化（0.8〜1.2）（どのTFも同程度の信頼性）
- 高ボラ時: 上位足重みを+50%（下位足のノイズが激増するため）

**想定改善:** +20K-50K（偽シグナル削減）

**ファイル:** `autotrader/decision/unified/mode_aware_consensus.py`

---

## 31. BCA (Directional Edge) のペナルティ二重適用

**現状:** 同一シグナルに対して3段階のペナルティが独立適用:
1. `DirectionalEdgeAssessor` → opposition_ratio > 0.3 でペナルティ
2. `SoftGuard` → 確度(confidence)に基づく減額
3. `PositionSizer` → confidence < 1.0 で 0.3〜1.0倍に減額

**問題:** 弱いシグナルが `0.7 × 0.5 × 0.65 = 0.23倍` まで縮小される可能性。
本来0.5ロットのトレードが0.12ロットに → 利益を大幅に損なう。

**やり直すなら:**
- ペナルティを単一パイプラインに統合
- `final_lot = base_lot × min(bca_factor, soft_guard_factor)` で最も厳しい1つだけ適用
- または `confidence = directional_edge` として BCA値を直接使用

**想定改善:** +10K-30K（ロットサイジング正規化）

---

## 32. DD制御と連敗調整の過度な重複

**現状:** DD閾値（0.8%/1.5%）と連敗調整が同時発動で最小0.35倍まで低下
```
DD調整: 0.7（DD 1.5%以上）
連敗調整: 0.5（3連敗以上）
合計: 0.7 × 0.5 = 0.35倍
```

**問題:** 利益+3,000Kの状態でDD 2% = 60K損失に過ぎない。過剰防御で回復機会を逸失。

**やり直すなら:**
- `max(dd_adjust, loss_adjust)` で片方のみ適用（二重減額の排除）
- 累積利益に応じてDD閾値を動的拡大（+3,000K以上なら DD 3.0% まで許容）

**想定改善:** +30K-80K

---

## 33. レジーム検出のHIGH_VOL/TREND判定矛盾

**現状（判定優先順序）:**
1. HIGH_VOL: ATR > 1.5 かつ ADX < 25
2. TREND: ADX >= 20 かつ MA整列
3. LOW_VOL: ATR < 0.7
4. RANGE: その他

**問題:** ADX=22, ATR=1.6 の場合 → HIGH_VOLに分類（TRENDではない）
しかし ADX=22 はトレンドを示唆しており、「高ボラ + トレンド」という重要な局面をRANGE扱いの代替として扱ってしまう。

**やり直すなら:**
- 判定優先順位を TREND > RANGE > HIGH_VOL > LOW_VOL に変更
- TREND判定を最優先にし、「トレンド中の高ボラ」をTRENDに統合
- 通貨ペア別閾値: USDJPY(ADX 18, ATR 1.2) vs EURJPY(ADX 22, ATR 1.4)

**想定改善:** +50K-150K

**ファイル:** `autotrader/calculator/features/regime_detector.py`

---

## 34. 固定スリッページモデルの非現実性

**現状:**
```python
# simulator.py:1028-1031
self._slippage_price = config.slippage_pips * 0.01  # 固定
```
- エントリー/エグジット共に固定スリッページ
- ニュース発表時のスプレッド拡大（+5-10pips）が未モデル化
- 高ボラ時のスリッページ増加が未反映
- TP到達時のスリッページが考慮されていない（エントリーのみ）

**やり直すなら:**
- **ボラティリティ連動スリッページ**: `slippage = base + atr_factor × ATR`
- **セッション別スプレッド**: 東京1.2, ロンドン0.8, NY重複0.8, ニュース時+5-10
- **TP側スリッページ**: TP距離からも-0.5〜-1pips を控除

**インパクト:** バックテスト成績の現実性が向上。成績は -50K〜-150K 低下するが、
リアルトレードとの乖離が縮小し、パラメータ最適化の信頼性が向上。

**ファイル:** `autotrader/backtest/simulator.py`

---

## 35. ウォークフォワード検証の不在 — 過適合リスク

**現状:**
- `backtest/walk_forward.py` にフレームワークの外枠はあるが、実質未実装
- 全期間（2020-2025）一括でパラメータ最適化 → 過適合の典型パターン
- 最適化パラメータ10+ vs データ量245K本 → 自由度は高いが OOS検証なし
- Phase 3 regression bisect で -44%劣化が発生した事例は、過適合の兆候

**やり直すなら:**
- **ローリングウォークフォワード**: 3年IS → 1年OOS → 1年スライド
  ```
  IS: 2020-2022 → OOS: 2023 → 検証
  IS: 2021-2023 → OOS: 2024 → 検証
  IS: 2022-2024 → OOS: 2025 → 検証
  ```
- **Sharpe比 t検定**: OOS期間のSharpe比が統計的にゼロと異なるか検証
- **パラメータ安定性テスト**: 最適値±10%で性能がどう変わるか検証（急峻な最適点は過適合の兆候）

---

## 36. 通貨ストレングス指標の不在

**現状:** 単一通貨ペアベースの分析のみ。関連通貨の強弱を考慮していない。

**問題:** USDJPY買いシグナルが出ても、同時にEURJPY/GBPJPYが全て下落中なら
JPY全体が強く、USDJPYの買いは逆行リスクが高い。この情報が無視されている。

**やり直すなら:**
- 主要6ペア（USDJPY, EURJPY, GBPJPY, EURUSD, GBPUSD, EURGBP）のRSI/MA方向を集計
- USD/JPY/EUR/GBP それぞれの「通貨強弱スコア」を算出
- エントリー時に「通貨強弱と一致する方向のみ許可」のフィルタ追加

**想定改善:** +50K-200K（逆行トレードの排除）

---

## 37. 適応型トレーリングストップの不在

**現状:** トレーリングストップのパラメータが固定。勝率やPFの変動に応じた動的調整なし。

**やり直すなら:**
- 勝率 > 65% かつ PF > 2.0: trailing SL = 0.5R に緩和（利益の伸ばし重視）
- 勝率 < 50% かつ PF < 1.2: trailing SL = 0.3R に強化（損失の最小化重視）
- 過去30トレードのローリング統計で毎日更新

**想定改善:** +50K-150K

---

## 38. レジーム適応型タイムフレームルーティングの不在

**現状:** TimeframeRouter の PRIMARY/ENTRY/CONFIRM/MANAGE が全レジーム固定

**やり直すなら:**
- TREND時: primary=H4→H8, entry=M30→H1（上位足シフト、ノイズ抑制）
- RANGE時: primary=H4→H4, entry=M30→M15（下位足の細かいエントリー）
- HIGH_VOL時: primary=H4のみ、entry=H1（安定した足のみ使用）

**想定改善:** +30K-100K

---

## 改善の総合インパクト推定

### 「バックテストの現実性向上」カテゴリ（成績は下がるが信頼性が上がる）

| 項目 | 成績変化 | 信頼性向上 |
|------|---------|-----------|
| #27 SwingAnalyzer look-ahead修正 | -200K〜-800K | 極めて高い |
| #28 Divergence look-ahead修正 | -50K〜-200K | 高い |
| #34 動的スリッページモデル | -50K〜-150K | 高い |
| #35 ウォークフォワード検証 | ±0（検証手法） | 極めて高い |

### 「トレード品質改善」カテゴリ（修正で成績が上がる）

| 項目 | 想定改善 | 実装難度 |
|------|---------|---------|
| #29 PM バックテスト/ライブ統一 | ±不明 | 低 |
| #30 レジーム適応型重み付け | +20K-50K | 中 |
| #31 ペナルティ二重適用排除 | +10K-30K | 中 |
| #32 DD/連敗調整の単一化 | +30K-80K | 低 |
| #33 レジーム判定優先順位修正 | +50K-150K | 中 |
| #36 通貨ストレングス指標 | +50K-200K | 高 |
| #37 適応型トレーリングストップ | +50K-150K | 中 |
| #38 レジーム適応型ルーティング | +30K-100K | 高 |

### 最優先アクション

1. **#27 SwingAnalyzer look-ahead修正** — 現在のバックテスト成績が虚構である可能性。真っ先に修正して「本当の実力」を把握すべき
2. **#28 Divergence look-ahead修正** — 同上
3. **#35 ウォークフォワード検証** — 過適合の程度を定量化
4. **#33 レジーム判定修正** — 実装が簡単で効果が大きい
5. **#32 DD調整の過度排除** — 実装が簡単で効果が大きい

---

## 良い点（変更不要）

- `core/entities.py` のPydantic frozen=True設計 — A+
- `calculator/` の指標計算分離 — A
- `constraint/` のHard/SoftGuard二重防御 — A
- 「バックテスト/ライブでロジック共用」の原則 — 正しく守られている
- Parquetキャッシュ戦略 — 堅実
- カスタム例外階層 — 9カテゴリで体系的
- TYPE_CHECKING による循環依存回避 — 適切
- アーキテクチャ層の依存方向 — 逆依存なし（A+）
