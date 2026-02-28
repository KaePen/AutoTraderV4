# RSS リアルトレード統合計画

## Context

バックテスト用のニュースLLM分析パイプライン（Map-Reduce、適応的バッチリトライ等）は完成済みだが、
リアルトレード側にはRSSフィードからのニュース取得・分析が未接続。

既存コンポーネント:
- `RSSCollector` - RSS非同期ポーリング（NewsItem出力）✅ 実装済み
- `NewsLLMAnalyzer` - リアルタイムLLMセンチメント分析（TTLキャッシュ付き）✅ 実装済み
- `FundamentalConfig` - `use_rss_news`, `rss_poll_interval_minutes` 等のフラグ ✅ 定義済み
- `FundamentalMemoryService` - コンテキスト生成ハブ ✅ 実装済み（ニュース未統合）
- `LiveTradingEngine._init_fundamental()` - RSS初期化なし ⚠️ 未接続
- `_start_fundamental_tasks()` / `_stop_fundamental_tasks()` - 定義済みだが **未呼び出し** ⚠️ バグ

**目的**: 既存コンポーネントを配線し、RSS→LLM分析→FundamentalContextへのセンチメント反映を実現する。
同時に、ファンダメンタル収集タスクの起動/停止の配線漏れも修正する。

## 実装ステップ

### Step 1: ファンダメンタルタスク起動/停止の配線修正（バグ修正）

**ファイル**: `autotrader/live/engine.py`

**問題**: `_start_fundamental_tasks()` (L2282) と `_stop_fundamental_tasks()` (L2290) が
定義されているが `start()` / `stop()` から呼ばれていない。

**修正**:
- `start()` (L431) の `_main_loop` 起動前に `await self._start_fundamental_tasks()` を追加
- `stop()` (L464) の冒頭に `await self._stop_fundamental_tasks()` を追加

### Step 2: __init__ に RSS/ニュースアナライザー属性を追加

**ファイル**: `autotrader/live/engine.py` (L135-140付近)

```python
# 既存
self._fundamental_memory = None
self._fundamental_collector = None
self._morning_update_done_date: datetime | None = None
# 追加
self._rss_collector = None
self._news_analyzer = None
self._news_buffer: dict[str, list] = {}
```

### Step 3: _init_fundamental に RSS/ニュース初期化を追加

**ファイル**: `autotrader/live/engine.py` (L2229-2280)

`_init_fundamental()` の try ブロック末尾に追加:

```python
# RSSニュース収集・分析（オプション）
if cfg.use_rss_news:
    from autotrader.adapters.fundamental.rss_collector import (
        RSSCollector,
    )
    from autotrader.adapters.fundamental.news_llm_analyzer import (
        NewsLLMAnalyzer,
    )
    # シンボルから通貨コードを抽出 (例: "USDJPY" → ["USD", "JPY"])
    currencies = [
        self._active_symbol[:3],
        self._active_symbol[3:6],
    ]
    self._rss_collector = RSSCollector(
        currencies=currencies,
        poll_interval=cfg.rss_poll_interval_minutes * 60,
    )
    self._news_analyzer = NewsLLMAnalyzer(
        sentiment_ttl_hours=cfg.rss_sentiment_ttl_hours,
    )
    logger.info("[Fundamental] RSSニュース機能初期化完了")
```

### Step 4: _start_fundamental_tasks に RSS 起動を追加

**ファイル**: `autotrader/live/engine.py` (L2282-2288)

```python
async def _start_fundamental_tasks(self) -> None:
    """ファンダメンタル収集タスクを起動"""
    if self._fundamental_collector:
        await self._fundamental_collector.start()
        logger.info("[Fundamental] 収集タスク起動")
    # RSS 追加
    if self._rss_collector:
        await self._rss_collector.start(
            callback=self._on_rss_news
        )
        logger.info("[Fundamental] RSSポーリング起動")
```

### Step 5: _on_rss_news コールバック追加

**ファイル**: `autotrader/live/engine.py`

`_stop_fundamental_tasks` の後に追加:

```python
async def _on_rss_news(self, news_item) -> None:
    """RSSニュース受信コールバック

    受信したNewsItemをシンボル別バッファに蓄積する。
    バッファは _tick() 内でLLM分析に使用後クリアされる。

    Args:
        news_item: 受信したNewsItem
    """
    # 対象通貨に該当するシンボルのバッファに追加
    symbol = self._active_symbol
    base = symbol[:3].upper()
    quote = symbol[3:6].upper()
    if base in news_item.currencies or quote in news_item.currencies:
        if symbol not in self._news_buffer:
            self._news_buffer[symbol] = []
        self._news_buffer[symbol].append(news_item)
        # バッファ上限（メモリリーク防止）
        _MAX_BUFFER = 100
        if len(self._news_buffer[symbol]) > _MAX_BUFFER:
            self._news_buffer[symbol] = (
                self._news_buffer[symbol][-_MAX_BUFFER:]
            )
```

### Step 6: _tick() にニュースセンチメントブレンドを追加

**ファイル**: `autotrader/live/engine.py` (L573-587)

fundamental_ctx 取得後、シグナル生成前に挿入:

```python
# [NEWS] ニュースセンチメントをブレンド
if (
    fundamental_ctx is not None
    and self._news_analyzer is not None
):
    news_items = self._news_buffer.get(
        self._active_symbol, []
    )
    if news_items:
        sentiment = await self._news_analyzer.analyze(
            news_items, self._active_symbol
        )
        fundamental_ctx = self._blend_news_sentiment(
            fundamental_ctx, sentiment
        )
        # 分析済みバッファをクリア
        self._news_buffer[self._active_symbol] = []
    else:
        # バッファ空でもキャッシュから取得
        sentiment = (
            self._news_analyzer.get_current_sentiment(
                self._active_symbol
            )
        )
        if sentiment != 0.0:
            fundamental_ctx = self._blend_news_sentiment(
                fundamental_ctx, sentiment
            )
```

### Step 7: _blend_news_sentiment ヘルパーメソッド追加

**ファイル**: `autotrader/live/engine.py`

`_on_rss_news` の後に追加（バックテストの `_merge_news_into_context` と同じ重み）:

```python
@staticmethod
def _blend_news_sentiment(
    ctx,
    sentiment: float,
    weight: float = 0.15,
):
    """ニュースセンチメントを FundamentalContext にブレンド

    バックテストの BacktestFundamentalProvider._merge_news_into_context()
    と同じ重み（0.15）で direction_bias にブレンドする。

    Args:
        ctx: FundamentalContext
        sentiment: センチメントスコア (-1.0~+1.0)
        weight: ブレンド重み（デフォルト0.15）

    Returns:
        FundamentalContext: ブレンド済みコンテキスト
    """
    from dataclasses import replace
    blended_bias = (
        ctx.direction_bias * (1.0 - weight)
        + sentiment * weight
    )
    return replace(
        ctx,
        direction_bias=blended_bias,
        sentiment_score=sentiment,
    )
```

### Step 8: _stop_fundamental_tasks に RSS 停止を追加

**ファイル**: `autotrader/live/engine.py` (L2290-2293)

```python
async def _stop_fundamental_tasks(self) -> None:
    """ファンダメンタル収集タスクを停止"""
    if self._fundamental_collector:
        await self._fundamental_collector.stop()
    # RSS 追加
    if self._rss_collector:
        await self._rss_collector.stop()
    self._news_buffer.clear()
```

### Step 9: テスト作成

**ファイル**: `tests/unit/live/test_engine_rss_integration.py`

テストケース:
1. `test_blend_news_sentiment_default_weight` - weight=0.15で direction_bias がブレンドされる
2. `test_blend_news_sentiment_zero` - sentiment=0.0 でコンテキスト不変
3. `test_blend_news_sentiment_updates_score` - sentiment_score フィールドが更新される
4. `test_on_rss_news_buffers_matching_currency` - 該当通貨のニュースがバッファに蓄積
5. `test_on_rss_news_ignores_unrelated` - 無関係通貨のニュースは無視
6. `test_on_rss_news_buffer_limit` - 100件上限
7. `test_init_rss_disabled` - use_rss_news=False で _rss_collector=None
8. `test_init_rss_enabled` - use_rss_news=True で初期化される（モック）

## 変更ファイル一覧

| ファイル | 変更内容 |
|---------|---------|
| `autotrader/live/engine.py` | RSS初期化、コールバック、ブレンド、起動/停止配線修正 |
| `tests/unit/live/test_engine_rss_integration.py` | 新規テスト（8ケース） |

## 既存の再利用コンポーネント

| コンポーネント | ファイル | 用途 |
|-------------|---------|------|
| `RSSCollector` | `autotrader/adapters/fundamental/rss_collector.py` | RSS非同期ポーリング |
| `NewsLLMAnalyzer` | `autotrader/adapters/fundamental/news_llm_analyzer.py` | LLMセンチメント分析 |
| `NewsItem` | `autotrader/adapters/fundamental/news_schemas.py` | ニュースデータ型 |
| `FundamentalContext` | `autotrader/adapters/fundamental/schemas.py` | ファンダメンタルコンテキスト |
| `FundamentalConfig` | `autotrader/live/config.py` | 設定フラグ（定義済み） |

## 検証方法

1. `pytest tests/unit/live/test_engine_rss_integration.py -v` でテスト確認
2. `pytest tests/ -x` で既存テスト回帰なし確認
3. `use_rss_news=False`（デフォルト）で既存動作に影響なし確認
4. `_start_fundamental_tasks` / `_stop_fundamental_tasks` の呼び出し確認（grep）
