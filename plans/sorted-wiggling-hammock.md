# Plan: DeterministicEventAnalyzer のリアルトレード統合 & LLMイベント生成コード削除

## Context

PR #315 で surprise_score/direction_bias をコード計算に統一し、
PR #316 で `DeterministicEventAnalyzer` を作成済み。
全12通貨ペア×16年分の llm_events CSV も再生成完了。

**現状の問題**: バックテストでは `BacktestFundamentalProvider` が CSV から
`EventLLMRecord` を読み込み、`_synthesize_event_llm_context()` で
FundamentalContext（direction_bias, surprise_score, convergence_progress,
volatility_multiplier 等）を合成してトレードロジックに渡している。
しかしリアルトレードの `live/engine.py` は:
1. `fundamental_ctx` を取得しているが `generate_signal()` に渡していない
2. `FundamentalMemoryService.get_context_for_llm()` は DB の macro/post_event/sentiment
   スコアのみ返し、Phase 2 フィールド（direction_bias, surprise_score 等）は未設定

**ゴール**:
- リアルトレードでも MT5 から取得した経済イベントを `DeterministicEventAnalyzer` で
  リアルタイム分析し、バックテストと同等の FundamentalContext を生成する
- 不要になった LLM イベント生成コードを削除する

## 変更一覧

### 1. `EventLLMRecord` と `compute_influence` を共有モジュールへ移動

**対象**: `autotrader/adapters/fundamental/schemas.py`（移動先）、
`autotrader/adapters/fundamental/backtest_provider.py`（移動元）

`EventLLMRecord`, `compute_influence()`, `_IMPACT_WEIGHT`, `_INFLUENCE_THRESHOLD`,
`_MAX_LOOKBACK_HOURS` を `backtest_provider.py` → `schemas.py` へ移動。
backtest_provider.py からは import で参照するように変更。
live 側でもこれらを使うため。

### 2. `DeterministicEventAnalyzer` にリアルタイム単一イベント分析メソッド追加

**対象**: `autotrader/adapters/fundamental/deterministic_event_analyzer.py`

```python
def analyze_single_event(
    self, symbol: str, event: EconomicEvent,
) -> EventLLMRecord:
    """単一イベントをリアルタイム分析し EventLLMRecord を返す"""
```

既存の `_analyze_event()` は CSV 行（dict）を返すが、リアルタイムでは
`EventLLMRecord` が必要。内部で `_analyze_event()` を呼び、dict → EventLLMRecord
に変換するラッパー。

### 3. `DeterministicEventAnalyzer` の継承元を `LLMGeneratorBase` に変更

**対象**: `autotrader/adapters/fundamental/deterministic_event_analyzer.py`

`LLMEventGenerator` が削除されるため、以下を吸収:
- 定数: `_HOLIDAY_RE`, `EVENT_CSV_COLUMNS`, `_INVERSE_INDICATORS`, `_IMPACT_SCALE`, `_HOLIDAY_PARAMS`
- メソッド: `generate_for_symbol_year()`, `_filter_events()`, `_get_indicator_direction()`,
  `_compute_surprise_score()`, `_compute_direction_bias()`, `_holiday_result()`,
  `_low_impact_result()`, `_read_existing_rows()`

### 4. イベント合成ロジックを `FundamentalMemoryService` に追加

**対象**: `autotrader/adapters/fundamental/memory.py`

`BacktestFundamentalProvider._synthesize_event_llm_context()` のロジックを
`FundamentalMemoryService` にポートする。

新しいメソッド/属性:
```python
class FundamentalMemoryService:
    def __init__(self, ..., analyzer=None):
        self._analyzer = analyzer  # DeterministicEventAnalyzer
        self._event_records: dict[str, list[EventLLMRecord]] = {}

    def _analyze_released_events(
        self, symbol: str, events: list[EconomicEvent], now: datetime
    ) -> None:
        """発表済みイベントを分析し _event_records に蓄積"""

    def _synthesize_event_context(
        self, symbol: str, now: datetime,
        upcoming_dicts: list[dict], high_impact_soon: bool,
    ) -> FundamentalContext:
        """蓄積済み EventLLMRecord からコンテキストを合成"""
```

`get_context_for_llm()` を拡張:
1. `cached_events` から発表済みイベントを取得
2. `_analyze_released_events()` で未分析イベントを `DeterministicEventAnalyzer` で分析
3. `_synthesize_event_context()` で Phase 2 フィールド付きコンテキストを返す
4. analyzer が None の場合は従来通り（後方互換）

### 5. `live/engine.py` の `_tick()` を修正

**対象**: `autotrader/live/engine.py`

```python
# 修正前 (line 593):
signal = self._bot.generate_signal(current_time)

# 修正後:
signal = self._bot.generate_signal(
    current_time,
    fundamental_ctx=fundamental_ctx,
)
```

`_init_fundamental()` で `DeterministicEventAnalyzer` を生成し
`FundamentalMemoryService` に渡す。

### 6. 削除ファイル

| ファイル | 理由 |
|---------|------|
| `autotrader/adapters/fundamental/llm_event_generator.py` | DeterministicEventAnalyzer に吸収 |
| `autotrader/adapters/fundamental/llm_context_generator.py` | legacy サブコマンドのみ使用、廃止 |
| `tests/unit/adapters/fundamental/test_llm_event_generator.py` | 削除対象のテスト |
| `tests/unit/adapters/fundamental/test_llm_context_generator.py` | 削除対象のテスト |

### 7. `scripts/generate_fundamental_llm.py` 整理

- `events` サブコマンド: `--deterministic` をデフォルト化、LLM パスを削除
- `legacy` サブコマンド: 削除
- LLM 関連の import/引数を整理

### 8. テスト更新

**新規/更新**:
- `test_deterministic_event_analyzer.py`: `analyze_single_event()` のテスト追加
- `test_memory.py`: イベント分析・合成の統合テスト追加
- `test_backtest_provider.py`: `EventLLMRecord` import パス変更の確認

## 修正対象ファイル一覧

| # | ファイル | 変更種別 |
|---|---------|---------|
| 1 | `autotrader/adapters/fundamental/schemas.py` | 編集（EventLLMRecord等追加） |
| 2 | `autotrader/adapters/fundamental/deterministic_event_analyzer.py` | 編集（継承変更+メソッド吸収+リアルタイムAPI追加） |
| 3 | `autotrader/adapters/fundamental/memory.py` | 編集（イベント分析・合成追加） |
| 4 | `autotrader/adapters/fundamental/backtest_provider.py` | 編集（import変更） |
| 5 | `autotrader/live/engine.py` | 編集（fundamental_ctx 渡し+analyzer初期化） |
| 6 | `autotrader/adapters/fundamental/__init__.py` | 編集（エクスポート更新） |
| 7 | `scripts/generate_fundamental_llm.py` | 編集（LLMパス削除+legacy削除） |
| 8 | `autotrader/adapters/fundamental/llm_event_generator.py` | **削除** |
| 9 | `autotrader/adapters/fundamental/llm_context_generator.py` | **削除** |
| 10 | `tests/unit/adapters/fundamental/test_llm_event_generator.py` | **削除** |
| 11 | `tests/unit/adapters/fundamental/test_llm_context_generator.py` | **削除** |
| 12 | `tests/unit/adapters/fundamental/test_deterministic_event_analyzer.py` | 編集（テスト追加） |
| 13 | `tests/unit/adapters/fundamental/test_memory.py` | 新規作成 |

## 再利用する既存コード

- `compute_influence()` (`backtest_provider.py:161-186`) → schemas.py へ移動して共有
- `_synthesize_event_llm_context()` (`backtest_provider.py:812-941`) → memory.py にポート
- `_IMPACT_WEIGHT`, `_INFLUENCE_THRESHOLD`, `_MAX_LOOKBACK_HOURS` → schemas.py へ移動
- `DeterministicEventAnalyzer._analyze_event()` → `analyze_single_event()` から呼び出し
- `LLMEventGenerator` の共有メソッド群 → `DeterministicEventAnalyzer` に吸収

## 検証方法

1. 既存テスト: `pytest tests/unit/adapters/fundamental/` — 全パス確認
2. backtest_provider テスト: import パス変更後も全パス確認
3. 新規テスト: memory.py のイベント合成テスト
4. 結合テスト: `DeterministicEventAnalyzer.analyze_single_event()` → `EventLLMRecord` 生成確認
5. 型チェック: `mypy autotrader/adapters/fundamental/` でエラーなし
