# LLM統合 + ファンダメンタル記憶システム 実装計画

## Context

UNIVERSALモード導入によって全タイムフレーム（M1〜D1）を同時評価するようになり、
動的TF選択の非決定性からエントリータイミングの不安定さが増加している。
この問題に対してLLMを利用した文脈判断を追加し、チャートから取得できない
ファンダメンタル情報（経済指標・ニュース）を加味することで安定化を図る。

**具体的な目的**:
1. LLMによるエントリーフィルター強化（既存VetoにFundamental情報を追加）
2. MT5経済カレンダー + ForexFactoryスクレイピングでニュース・指標データ取得
3. LLMが生成した「方向性の記憶」をDBに蓄積（マクロバイアス・指標後バイアス・センチメント）
4. 毎朝の市場観更新 + 重要指標前後の自動ポジション管理

---

## 全体アーキテクチャ

```
【データ収集層】Phase 1
  MT5EconomicCalendarClient  → asyncio.to_thread（同期API→非同期ラップ）
  ForexFactoryClient         → httpx + BeautifulSoup4（1日1〜2回のみ）
  EconomicEventNormalizer    → 重複排除・シンボルマッピング
              ↓ 正規化
【DBストレージ】Phase 1-2
  economic_events テーブル   → 経済イベント（予定・実績）
  news_sentiment テーブル    → ニュースセンチメント
  market_memory テーブル     → 方向性の記憶（TTL付き）
              ↓
【ファンダメンタルメモリ】Phase 2
  FundamentalMemoryService
  ├── get_context_for_llm()  → FundamentalContext（軽量、毎tick読み取り）
  ├── write_macro_bias()     → MACRO_BIAS（TTL=7日）
  ├── write_post_event_bias()→ POST_EVENT_BIAS（TTL=3日）
  └── get_upcoming_events()  → Veto判定用
              ↓ コンテキスト注入
【LLM拡張層】Phase 3
  OllamaClient（既存拡張）
  ├── check_veto(..., fundamental_context=...)  ← オプション引数追加
  ├── analyze_market_outlook_async() [NEW]      ← 毎朝の市場観分析
  └── analyze_post_event_async() [NEW]          ← 指標後バイアス分析
              ↓ 判断介入
【トレードエンジン統合】Phase 4
  LiveTradingEngine（既存拡張）
  ├── _tick(): fundamental_ctx取得 + 重要指標30分前は強制スキップ
  ├── _run_morning_update() [NEW]: 毎朝LLM市場観更新
  └── _handle_post_event_analysis() [NEW]: 指標後バイアス保存
```

---

## 実装フェーズ

### Phase 1: ファンダメンタルデータ収集層

**新規作成ファイル**:

| ファイル | 役割 | 行数目安 |
|---------|------|---------|
| `src/autotrader/adapters/fundamental/__init__.py` | エクスポート定義 | 20行 |
| `src/autotrader/adapters/fundamental/schemas.py` | `EconomicEvent`, `FundamentalContext` dataclass | 80行 |
| `src/autotrader/adapters/fundamental/mt5_calendar.py` | MT5経済カレンダークライアント | 150行 |
| `src/autotrader/adapters/fundamental/forex_factory.py` | ForexFactoryスクレイパー（60秒レート制限） | 200行 |
| `src/autotrader/adapters/fundamental/normalizer.py` | 重複排除・シンボルマッピング | 100行 |
| `src/autotrader/adapters/fundamental/collector.py` | 収集スケジューラ（エンジンと独立asyncioタスク） | 150行 |

**変更ファイル**:

| ファイル | 変更内容 |
|---------|---------|
| `src/autotrader/adapters/database/models.py` | `EconomicEventRecord`, `NewsSentimentRecord` テーブル追加（+60行） |
| `src/autotrader/adapters/database/repositories.py` | `EconomicEventRepository` クラス追加（+80行） |

**DBテーブル設計**:
```python
# economic_events テーブル
id, event_id(UUID), event_time, currency, symbol, event_name,
impact(high/medium/low), actual, forecast, previous, source(MT5/forex_factory),
fetched_at
# Index: (event_time, currency)

# news_sentiment テーブル
id, symbol, sentiment_score(-1.0〜+1.0), headline, source, recorded_at
# Index: (symbol, recorded_at)
```

**キー設計決定**:
- MT5カレンダー（`calendar_event_get()` + `calendar_value_get()`）を優先ソース
- ForexFactoryはMT5カレンダーが空の場合のフォールバック
- 収集失敗はエンジンに影響しない（独立タスクで例外をキャッチ・ログのみ）

**新規依存**: `beautifulsoup4>=4.12.0`, `lxml>=5.0.0`

---

### Phase 2: ファンダメンタルメモリDB

**新規作成ファイル**:

| ファイル | 役割 |
|---------|------|
| `src/autotrader/adapters/fundamental/memory.py` | `FundamentalMemoryService`（200行以内） |

**変更ファイル**:

| ファイル | 変更内容 |
|---------|---------|
| `src/autotrader/adapters/database/models.py` | `MarketMemoryRecord` テーブル追加（+50行） |
| `src/autotrader/adapters/database/repositories.py` | `MarketMemoryRepository` クラス追加（+80行） |

**DBテーブル設計**:
```python
# market_memory テーブル
memory_id(UUID), symbol, memory_type(MACRO_BIAS/POST_EVENT_BIAS/SENTIMENT_SCORE),
direction_score(-1.0〜+1.0), confidence(0.0〜1.0), summary(日本語要約),
source_event, valid_until(TTL), created_at, llm_reasoning
# Index: (symbol, memory_type, valid_until)
```

**TTL設計**:

| memory_type | TTL | 更新トリガー |
|-------------|-----|------------|
| MACRO_BIAS | 7日 | 毎朝LLM市場観更新 |
| POST_EVENT_BIAS | 3日 | 重要指標発表後30分以内 |
| SENTIMENT_SCORE | 4時間 | 毎時ニュース取得後 |

**`FundamentalContext` dataclass**（`_tick()`で毎回取得する軽量オブジェクト）:
```python
@dataclass(frozen=True)
class FundamentalContext:
    macro_bias_score: float          # -1.0〜1.0
    macro_bias_summary: str
    post_event_bias_score: float
    post_event_summary: str
    sentiment_score: float
    upcoming_events: list[dict]      # [{name, minutes_until, impact}]
    has_high_impact_within_30min: bool
```

---

### Phase 3: 既存Ollamaクライアント拡張

**変更ファイル**:

| ファイル | 変更内容 |
|---------|---------|
| `src/autotrader/adapters/ollama/schemas.py` | `MarketOutlookOutput`, `PostEventAnalysisOutput`, `FundamentalVetoOutput` 追加（+80行） |
| `src/autotrader/adapters/ollama/prompts.py` | ファンダメンタルコンテキストセクション・新プロンプト追加（+100行） |
| `src/autotrader/adapters/ollama/client.py` | 新メソッド2つ追加; `check_veto`系にオプション引数追加（+100行） |

**設計上の制約（既存テスト保護）**:
- `check_veto()`/`adjust_confidence()` の既存シグネチャを変更しない
- 新引数は全て `fundamental_context: FundamentalContext | None = None` として末尾追加
- `None` の場合は既存動作と完全に同一

**新規スキーマ**:
```python
class MarketOutlookOutput(BaseModel):
    direction_score: float    # -1.0〜+1.0
    confidence: float         # 0.0〜1.0
    macro_summary: str        # 日本語50文字以内
    key_factors: list[str]
    valid_days: int           # デフォルト7
    risk_events: list[str]   # 今週の注意イベント

class PostEventAnalysisOutput(BaseModel):
    surprise_direction: Literal["BULLISH", "BEARISH", "NEUTRAL"]
    expected_duration_hours: int  # 1〜72
    bias_score: float             # -1.0〜+1.0
    analysis: str                 # 日本語
```

---

### Phase 4: トレードエンジンへの統合

**変更ファイル**:

| ファイル | 変更内容 |
|---------|---------|
| `src/autotrader/live/config.py` | `FundamentalConfig` dataclass追加; `LiveTradingConfig`にフィールド追加（+30行） |
| `src/autotrader/live/engine.py` | 新フィールド・起動処理・`_tick()`差し込み5行・新規メソッド2つ追加（+120行） |

**`FundamentalConfig`**:
```python
@dataclass(frozen=True)
class FundamentalConfig:
    enabled: bool = False             # デフォルトOFF（段階的有効化）
    use_mt5_calendar: bool = True
    use_forex_factory: bool = False   # デフォルトOFF（MT5優先）
    fetch_interval_minutes: int = 60
    morning_update_utc_hour: int = 21  # UTC21時=日本時間6時
    event_guard_minutes: int = 30      # 重要指標前の取引停止分数
```

**`_tick()`への差し込み**（最小限5行）:
```python
# [NEW] ファンダメンタルコンテキスト取得
if self._fundamental_memory:
    fundamental_ctx = self._fundamental_memory.get_context_for_llm(symbol, now)
    if fundamental_ctx.has_high_impact_within_30min:
        return  # 重要指標直前 → エントリースキップ
```

**新規メソッド**:
- `_run_morning_update()`: 毎朝UTC21時にLLM市場観更新、当日実行済みならスキップ
- `_handle_post_event_analysis()`: 指標実績値取得後にLLMで方向性分析・POST_EVENT_BIAS保存
  - 120行超の場合は `src/autotrader/live/fundamental_handler.py` として別ファイルに切り出し

---

## 実装順序（依存関係）

```
Phase 1a: DBモデル追加（economic_events, news_sentiment テーブル）
    ↓
Phase 1b: EconomicEvent スキーマ + Normalizer（単体テスト可能）
    ↓
Phase 1c: MT5CalendarClient（MT5モック使用）
    ↓
Phase 1d: ForexFactoryClient（httpxモック使用）
    ↓
Phase 1e: FundamentalDataCollector（1a〜1d全依存）
    ↓
Phase 2a: MarketMemoryRecord DBテーブル追加
    ↓
Phase 2b: FundamentalMemoryService
    ↓
Phase 3a: Ollama schemas 追加（既存テスト影響ゼロ）
    ↓
Phase 3b: Ollama prompts 追加
    ↓
Phase 3c: OllamaClient 新メソッド追加（既存シグネチャ変更なし）
    ↓
Phase 4a: FundamentalConfig 追加（LiveTradingConfigデフォルト引数）
    ↓
Phase 4b: LiveTradingEngine 統合（enabled=Falseでデフォルトはガード付き）
```

---

## テスト計画

### 新規テストファイル

| ファイル | テスト内容 |
|---------|---------|
| `tests/unit/adapters/fundamental/test_mt5_calendar.py` | MT5 API モック使用 |
| `tests/unit/adapters/fundamental/test_forex_factory.py` | HTMLフィクスチャファイル使用 |
| `tests/unit/adapters/fundamental/test_normalizer.py` | 重複排除・フィルタリング |
| `tests/unit/adapters/fundamental/test_memory.py` | SQLiteインメモリDB使用 |
| `tests/unit/adapters/database/test_economic_event_repo.py` | SQLiteインメモリDB使用 |

### 既存テスト保護
- DBテーブル追加は `Base.metadata.create_all()` で自動対応（既存テーブル影響ゼロ）
- `FundamentalConfig.enabled = False`（デフォルト）でガード
- 全フェーズ完了後 `pytest tests/` で414テスト全PASS確認

### E2Eテスト
- `FundamentalConfig(enabled=True)` でライブエンジンを起動し、収集→記憶→Veto判定のフロー確認
- MT5モック使用（実際の接続不要）

---

---

## Phase 5: バックテストへのファンダメンタル統合

バックテストでは「その時点の経済イベント」をシミュレートし、ライブと同じロジックで
Veto判定・指標前後スキップが動作することを確認する。

**バックテスト用データソース**:
- MT5の `calendar_value_get(from_date, to_date)` は過去データも取得可能 → 事前にCSV化してバックテストデータに含める
- 過去イベントデータを `data/fundamental/events_YYYY.csv` として保存

**新規作成ファイル**:

| ファイル | 役割 |
|---------|------|
| `src/autotrader/adapters/fundamental/backtest_provider.py` | CSVから経済イベントを読み込み、バックテスト時刻に合わせてフィルタリングする `BacktestFundamentalProvider` |
| `src/autotrader/adapters/fundamental/memory_simulator.py` | バックテスト用の `FundamentalMemorySimulator`（DBなし、インメモリ動作） |

**変更ファイル**:

| ファイル | 変更内容 |
|---------|---------|
| `src/autotrader/backtest/runner.py` | `fundamental_config` が有効な場合に `BacktestFundamentalProvider` を初期化; 各ローソク足処理時に `upcoming_events` を参照してスキップ判定 |
| `src/autotrader/backtest/executor.py` | `ExecutorConfig` に `fundamental_config: FundamentalConfig` 追加（デフォルトOFF） |
| `src/autotrader/backtest/adapters/cli.py` | `--fundamental` / `--fundamental-csv` 引数追加 |

**バックテスト用フロー**:
```
BacktestRunner.run_unified()
    ↓
BacktestFundamentalProvider.load_csv(events_csv_path)
    ↓
各ローソク足処理ループ内:
    provider.get_context(current_time) → FundamentalContext
    if has_high_impact_within_30min → signal = HOLD（スキップ）
    else → 通常のシグナル生成（LLM Vetoにfundamental_ctx追加）
```

**LLMのバックテスト動作**:
- バックテスト中のOllama呼び出しは時間がかかるため、デフォルトOFF
- `--fundamental-llm` フラグで有効化（非推奨・低速）
- 推奨: 事前に `analyze_market_outlook` の結果を日次CSVとして生成・保存し、バックテスト時はそのキャッシュを参照する `CachedMarketOutlookProvider` を使用

---

## 検証方法

1. **Phase 1完了後**: `pytest tests/unit/adapters/fundamental/ -v` で収集層テスト全PASS
2. **Phase 2完了後**: DBにmarket_memoryレコードが正しいTTLで保存されることを確認
3. **Phase 3完了後**: `fundamental_context=None`を渡したcheck_vetoが既存と同一動作することを確認
4. **Phase 4完了後**:
   - `FundamentalConfig(enabled=True, event_guard_minutes=30)` でエンジン起動
   - 重要指標30分前に `_tick()` が自動スキップすることをログで確認
   - 毎朝のmarket_memory更新をDBで確認
5. **Phase 5完了後**:
   - `python -m autotrader.backtest --universal --fundamental --fundamental-csv data/fundamental/events_2024.csv`
   - 重要指標前後でエントリーがスキップされることをバックテストログで確認
   - 指標なしの通常時のシグナル生成と比較してロジック変化がないことを確認
6. **全体**: `pytest tests/ -v` で414+新規テスト全PASS確認

## スコープ外（後続フェーズ）

- Webダッシュボードへのファンダメンタル情報パネル表示
- LLMによる動的スコア重み調整（TimeframeEvaluatorへの介入）
- COTレポート（機関投資家ポジション）データの統合
