# ニュースデータ統合計画（GDELT バックテスト + RSS ライブ）

## Context

ユーザーが「プランC（本気）」を選択：

- **バックテスト**: GDELT の過去ニュース（2010-2025）を事前一括取得 → LLM バッチ分析 → CSV に保存
- **ライブ**: RSS フィードを5分ごとにポーリング → リアルタイム LLM 処理

経済指標（ForexFactory）と通貨ニュース（GDELT/RSS）を同一 LLM プロンプトに統合し、
`sentiment_score` を含む `FundamentalContext` を精度向上させる。

PR #165（ForexFactory スクレイパー修正）・PR #166（コードレビュー対応）は既にマージ済み。

---

## 現状の実装確認

### 既存ファイル（修正対象）

| ファイル | 役割 |
|---------|------|
| `src/autotrader/adapters/fundamental/schemas.py` | `EconomicEvent`, `FundamentalContext`, `EventSource` enum |
| `src/autotrader/adapters/fundamental/llm_context_generator.py` | `generate_for_symbol_year(symbol, year, events, ...)` → CSV |
| `src/autotrader/adapters/fundamental/backtest_provider.py` | `load_csv()`, `load_llm_context_csv()`, `get_context()` bisect |
| `scripts/generate_fundamental_llm.py` | LLM 生成の CLI エントリポイント |
| `src/autotrader/live/config.py` | `FundamentalConfig` |
| `src/autotrader/live/engine.py` | `_tick()`, `_run_morning_update()`, `_handle_post_event_analysis()` |
| `pyproject.toml` | 依存関係 |

### 新規作成ファイル

| ファイル | 役割 |
|---------|------|
| `src/autotrader/adapters/fundamental/news_schemas.py` | `NewsItem` dataclass, `NewsSource` enum |
| `src/autotrader/adapters/fundamental/gdelt_client.py` | GDELT DOC API v2 クライアント |
| `src/autotrader/adapters/fundamental/news_csv_writer.py` | ニュース CSV 読み書き |
| `src/autotrader/adapters/fundamental/rss_collector.py` | RSS フィード収集（ライブ用） |
| `src/autotrader/adapters/fundamental/news_llm_analyzer.py` | リアルタイム LLM 分析（ライブ用） |
| `scripts/collect_gdelt_news.py` | GDELT 一括収集 CLI |
| `tests/unit/adapters/fundamental/test_gdelt_client.py` | GDELT クライアントテスト |
| `tests/unit/adapters/fundamental/test_rss_collector.py` | RSS コレクターテスト |

---

## PR 1: バックテスト用 GDELT ニュース統合 (`feat/gdelt-news-backtest`)

### Step 1: `news_schemas.py` — NewsItem データクラス

```python
# src/autotrader/adapters/fundamental/news_schemas.py
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

class NewsSource(str, Enum):
    GDELT = "gdelt"
    RSS = "rss"

@dataclass
class NewsItem:
    news_id: str           # gdelt_<hash> または rss_<hash>
    published_at: datetime  # UTC aware
    title: str
    source_name: str       # "Reuters", "Bloomberg" 等
    source_url: str
    currencies: list[str]  # ["USD", "JPY"] ← 記事から抽出
    source_type: NewsSource
    snippet: str | None = None  # 本文冒頭200文字
```

CSV 列: `news_id, published_at, title, source_name, source_url, currencies, source_type, snippet`

### Step 2: `gdelt_client.py` — GDELT DOC API v2 クライアント

**API**: `https://api.gdeltproject.org/api/v2/doc/doc`

```python
# src/autotrader/adapters/fundamental/gdelt_client.py
class GDELTDocClient:
    BASE_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

    def fetch_news_week(
        self,
        currencies: list[str],
        start_dt: datetime,
        end_dt: datetime,
    ) -> list[NewsItem]:
        """指定期間の通貨関連ニュースを取得（最大250件/リクエスト）

        クエリ例: "USD JPY forex central bank"
        レートリミット: リクエスト間 1.5 秒待機
        """

    def fetch_news_year(
        self,
        currencies: list[str],
        year: int,
    ) -> list[NewsItem]:
        """1年分を週単位でループして取得（約52回リクエスト）"""
```

通貨キーワードマッピング:
```python
_CURRENCY_KEYWORDS = {
    "USD": ["Federal Reserve", "Fed", "dollar", "US economy"],
    "JPY": ["Bank of Japan", "BOJ", "yen", "日銀"],
    "EUR": ["ECB", "euro", "European Central Bank"],
    "GBP": ["Bank of England", "BOE", "pound", "sterling"],
    ...
}
```

### Step 3: `news_csv_writer.py` — CSV 読み書き

```python
# src/autotrader/adapters/fundamental/news_csv_writer.py
def write_news_csv(
    news_items: list[NewsItem],
    output_path: Path,
    append: bool = False,
) -> None: ...

def read_news_csv(csv_path: Path) -> list[NewsItem]: ...
```

出力先: `data/fundamental/news_YYYY.csv`

### Step 4: `collect_gdelt_news.py` — CLI スクリプト

```bash
# 使用方法
python scripts/collect_gdelt_news.py --year 2024 --currencies USD,JPY,EUR,GBP
python scripts/collect_gdelt_news.py --years 2010-2025 --currencies USD,JPY
python scripts/collect_gdelt_news.py --year 2024 --output data/fundamental/
```

出力: `data/fundamental/news_2024.csv`（約5,000〜15,000件/年）

### Step 5: `schemas.py` — EventSource 拡張

```python
# 追加
class EventSource(str, Enum):
    MT5 = "mt5"
    FOREX_FACTORY = "forex_factory"
    GDELT = "gdelt"      # ← 追加
    RSS = "rss"          # ← 追加
```

### Step 6: `llm_context_generator.py` — news_items パラメータ追加

`generate_for_symbol_year()` のシグネチャ拡張（後方互換）:

```python
def generate_for_symbol_year(
    symbol: str,
    year: int,
    events: list[EconomicEvent],
    output_dir: Path,
    overwrite: bool = False,
    news_items: list[NewsItem] | None = None,  # ← 追加
) -> Path:
```

プロンプト拡張（ニュース有の場合）:

```
[指標データ]
... 既存のイベントデータ ...

[ニュース見出し（上位20件）]
2024-01-05T13:30:00Z | Reuters | Fed holds rates, signals caution on cuts
2024-01-05T10:00:00Z | Bloomberg | BOJ considering end to negative rates
...
```

### Step 7: `backtest_provider.py` — load_news_csv() 追加

```python
def load_news_csv(self, csv_path: str | Path) -> None:
    """ニュース CSV を読み込み self._news_items に格納（published_at でソート）"""

def get_context(self, current_time: datetime, symbol: str) -> FundamentalContext:
    # 既存: LLM CSV から macro_bias_score 取得（bisect）
    # 変更なし（LLM 生成時にニュースを統合済みのため）
```

### Step 8: `generate_fundamental_llm.py` — `--news-dir` フラグ追加

```bash
python scripts/generate_fundamental_llm.py \
    --symbol USDJPY --year 2024 \
    --news-dir data/fundamental/  # ← 追加
```

### テスト

| テストファイル | 内容 |
|------------|------|
| `tests/unit/adapters/fundamental/test_gdelt_client.py` | モックレスポンスでの NewsItem 変換、通貨キーワード抽出 |
| `tests/unit/adapters/fundamental/test_news_csv_writer.py` | 書き込み/読み込みラウンドトリップ |

---

## PR 2: ライブトレード用 RSS リアルタイム統合 (`feat/rss-live-sentiment`)

### Step 1: `rss_collector.py` — RSS フィード収集

```python
# src/autotrader/adapters/fundamental/rss_collector.py
RSS_FEEDS: dict[str, list[str]] = {
    "general": [
        "https://feeds.reuters.com/reuters/businessNews",
        "https://www.ft.com/rss/home/us",
    ],
    "USD": ["https://feeds.feedburner.com/forex/usd"],
    "JPY": ["https://www.japan-guide.com/rss/economy.xml"],
    ...
}

class RSSCollector:
    def __init__(self, currencies: list[str], poll_interval: int = 300):
        """
        Args:
            currencies: 対象通貨リスト
            poll_interval: ポーリング間隔（秒）。デフォルト300秒=5分
        """

    async def start(self, callback: Callable[[NewsItem], Awaitable[None]]) -> None:
        """非同期ポーリング開始。新着ニュースを callback に渡す"""

    async def stop(self) -> None: ...
```

依存: `feedparser>=6.0.11`

### Step 2: `news_llm_analyzer.py` — リアルタイム LLM 分析

```python
# src/autotrader/adapters/fundamental/news_llm_analyzer.py
class NewsLLMAnalyzer:
    def __init__(
        self,
        model: str = "qwen3:14b",
        sentiment_ttl_hours: int = 4,
    ):
        """
        Args:
            sentiment_ttl_hours: センチメントスコアの有効期間
        """

    async def analyze(
        self,
        news_items: list[NewsItem],
        symbol: str,
    ) -> float:
        """ニュース群からセンチメントスコアを算出 (-1.0〜+1.0)

        結果は self._cache[symbol] に TTL 付きでキャッシュ
        """

    def get_current_sentiment(self, symbol: str) -> float:
        """キャッシュから有効なセンチメントスコアを返す（TTL 切れは 0.0）"""
```

### Step 3: `live/config.py` — FundamentalConfig 拡張

```python
@dataclass
class FundamentalConfig:
    enabled: bool = False
    use_mt5_calendar: bool = True
    fetch_interval_minutes: int = 60
    event_guard_minutes: int = 30
    # ↓ 追加
    use_rss_news: bool = False
    rss_poll_interval_minutes: int = 5
    rss_sentiment_ttl_hours: int = 4
```

### Step 4: `live/engine.py` — RSS 初期化とコールバック追加

```python
class LiveEngine:
    async def _init_rss_news(self) -> None:
        """FundamentalConfig.use_rss_news=True の場合に RSSCollector を起動"""
        if not self._config.fundamental.use_rss_news:
            return
        self._rss_collector = RSSCollector(
            currencies=self._get_trading_currencies(),
            poll_interval=self._config.fundamental.rss_poll_interval_minutes * 60,
        )
        self._news_analyzer = NewsLLMAnalyzer(
            sentiment_ttl_hours=self._config.fundamental.rss_sentiment_ttl_hours,
        )
        await self._rss_collector.start(callback=self._on_news_item_received)

    async def _on_news_item_received(self, news_item: NewsItem) -> None:
        """新着ニュースを LLM で分析してキャッシュ更新"""
        currencies = news_item.currencies
        for symbol in self._get_symbols_for_currencies(currencies):
            await self._news_analyzer.analyze([news_item], symbol)
```

`_tick()` の `FundamentalContext` 生成時に `sentiment_score` を統合:

```python
# _tick() 内
sentiment = (
    self._news_analyzer.get_current_sentiment(symbol)
    if self._news_analyzer
    else 0.0
)
context = FundamentalContext(
    ...,
    sentiment_score=sentiment,  # RSS センチメントを反映
)
```

### Step 5: `pyproject.toml` — feedparser 追加

```toml
[project.optional-dependencies]
live = [
    "feedparser>=6.0.11",
]
```

### テスト

| テストファイル | 内容 |
|------------|------|
| `tests/unit/adapters/fundamental/test_rss_collector.py` | モック RSS フィードでの NewsItem 生成、重複排除 |
| `tests/unit/adapters/fundamental/test_news_llm_analyzer.py` | TTL キャッシュ、センチメント返却ロジック |

---

## ワークフロー

### PR 1: GDELT バックテスト統合

```bash
BRANCH="feat/gdelt-news-backtest"
WORKTREE="/d/Projects/AutoTraderV4/tmp/feat_gdelt-news-backtest"

git -C /d/Projects/AutoTraderV4 pull origin main
git -C /d/Projects/AutoTraderV4 branch "$BRANCH"
git -C /d/Projects/AutoTraderV4 worktree add "$WORKTREE" "$BRANCH"

# 新規ファイル作成・既存ファイル修正

git -C "$WORKTREE" add src/ scripts/ tests/ pyproject.toml
git -C "$WORKTREE" commit -m "feat: GDELTニュース統合（バックテスト用事前収集・LLM分析）"
git -C "$WORKTREE" push -u origin "$BRANCH"
"C:/Program Files/GitHub CLI/gh.exe" pr create --repo KaePen/AutoTraderV4 --base main \
  --title "feat: GDELTニュース統合（バックテスト用）" \
  --body "..."
git -C /d/Projects/AutoTraderV4 worktree remove "$WORKTREE" --force
git -C /d/Projects/AutoTraderV4 branch -d "$BRANCH"
```

### PR 2: RSS ライブ統合

```bash
BRANCH="feat/rss-live-sentiment"
WORKTREE="/d/Projects/AutoTraderV4/tmp/feat_rss-live-sentiment"
# PR 1 マージ後に実施（依存関係のため）
```

---

## 検証手順

### PR 1 検証

```bash
# 1. ユニットテスト
python -m pytest tests/unit/adapters/fundamental/test_gdelt_client.py -v
python -m pytest tests/unit/adapters/fundamental/test_news_csv_writer.py -v

# 2. GDELT 収集試行（1週間分）
python scripts/collect_gdelt_news.py --year 2024 --currencies USD,JPY

# 3. news_2024.csv の確認
# → data/fundamental/news_2024.csv が生成される
# → news_id, published_at, currencies 列が正しい

# 4. LLM 生成（ニュースあり）
python scripts/generate_fundamental_llm.py \
    --symbol USDJPY --year 2024 \
    --news-dir data/fundamental/

# 5. 生成 CSV で sentiment_score が 0.0 以外の月があることを確認
```

### PR 2 検証

```bash
# 1. ユニットテスト
python -m pytest tests/unit/adapters/fundamental/test_rss_collector.py -v

# 2. ライブ設定でエンジン起動（dry-run）
# config/live_trading.yaml に fundamental.use_rss_news: true を設定
# python scripts/run_live.py --dry-run でエラーなし確認

# 3. 全テスト
python -m pytest tests/ -x -q
```

---

## データフロー図

```
[バックテスト]
ForexFactory → events_YYYY.csv ──┐
GDELT API    → news_YYYY.csv ────┼→ generate_fundamental_llm.py
                                  │   (Ollama qwen3:14b, 月次12回/年)
                                  └→ llm_context_YYYY.csv
                                         ↓
                              BacktestFundamentalProvider.get_context()
                              (bisect O(log n), sentiment_score 含む)

[ライブ]
RSS フィード (5分ポーリング) → RSSCollector → NewsLLMAnalyzer
                                              (TTL 4h キャッシュ)
                                                    ↓
                              LiveEngine._tick() → FundamentalContext
                              (sentiment_score リアルタイム反映)
```
