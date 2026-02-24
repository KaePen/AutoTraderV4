# ニュース記事本文スクレイピング

## Context

フィルタ済みCSV（`news_rss_YYYY.csv`）にはFX専門7ソースのタイトル・URLが含まれるが、
記事本文は未取得。現在のLLM分析はタイトルのみで実行されている。
本文を取得することで、LLM分析の精度向上とリアルトレードでの情報量増加を実現する。

**要件:**
- カスタムドメイン別パーサー（7サイト固有のHTML構造に対応）
- バックテスト: CSV URLから事前一括取得
- リアルトレード: RSS取得時にリアルタイムで本文取得
- 既存CSV・パイプラインとの後方互換性

## 変更対象ファイル

| ファイル | 変更 | 内容 |
|---------|------|------|
| `autotrader/adapters/fundamental/article_scraper.py` | **新規** | パーサーフレームワーク + 7ドメインパーサー + ArticleFetcher |
| `autotrader/adapters/fundamental/news_schemas.py` | 修正 | `NewsItem` に `content` フィールド追加 |
| `autotrader/adapters/fundamental/news_csv_writer.py` | 修正 | CSV列に `content` 追加（後方互換） |
| `autotrader/adapters/fundamental/rss_collector.py` | 修正 | リアルタイム本文取得の統合 |
| `autotrader/adapters/fundamental/news_llm_analyzer.py` | 修正 | プロンプトに本文抜粋を追加 |
| `autotrader/adapters/fundamental/llm_context_generator.py` | 修正 | バッチLLMプロンプトに本文追加 |
| `scripts/scrape_news_content.py` | **新規** | 一括スクレイピングCLI |
| `tests/unit/adapters/fundamental/test_article_scraper.py` | **新規** | パーサー・フェッチャーのテスト |
| `tests/unit/adapters/fundamental/test_news_csv_writer.py` | 修正 | content列の後方互換テスト |

## Step 1: データモデル変更

### `news_schemas.py` — `NewsItem` に `content` フィールド追加

```python
@dataclass
class NewsItem:
    # ... 既存フィールド ...
    snippet: str | None = None
    content: str | None = None  # 記事本文（プレーンテキスト、最大5000文字）
```

### `news_csv_writer.py` — CSV列追加（後方互換）

- `_NEWS_CSV_COLUMNS` 末尾に `"content"` 追加
- `write_news_csv`: `"content": item.content or ""` 追加
- `_parse_row`: `content = row.get("content") or None` 追加
- `filter_news_csv`: 旧CSV（content列なし）読み込み時に空文字で補完

## Step 2: `article_scraper.py` 新規作成

### クラス構成

```
ScrapeResult (dataclass)     — 結果: content, status, error_msg
ArticleParser (ABC)          — 抽象基底: domain, needs_tls_fingerprint, extract_content
  +-- FXStreetParser         — fxstreet.com
  +-- ForexLiveParser        — forexlive.com（curl-cffi必須: Cloudflare）
  +-- InvestingComParser     — investing.com（curl-cffi必須: ボット検知）
  +-- CNBCParser             — cnbc.com
  +-- DailyFXParser          — dailyfx.com
  +-- BBCParser              — bbc.com
  +-- MarketWatchParser      — marketwatch.com（curl-cffi必須）
ParserRegistry               — ドメイン→パーサーのマッピング
ArticleFetcher               — HTTP取得エンジン（レート制御・エラーハンドリング）
_extract_domain(url)         — URLからドメイン抽出ヘルパー
_clean_text(raw, max_chars)  — テキスト正規化・切り詰め
```

### HTTPクライアント選択ロジック

```
parser.needs_tls_fingerprint == True  → curl-cffi (Session impersonate="chrome110")
parser.needs_tls_fingerprint == False → httpx.get()
```

既存パターン: `forex_factory.py` の curl-cffi セッション管理に準拠

### レート制御

- **ドメイン単位** で `time.monotonic()` ベースの間隔制御（デフォルト2秒）
- `_last_request: dict[str, float]` でドメインごとの最終リクエスト時刻を管理
- 既存パターン: `gdelt_client.py` の `_wait_rate_limit()` と同一方式

### 各パーサーの設計方針

| ドメイン | HTTP | 本文セレクタ | 除去対象 | TLS必須 |
|---------|------|------------|---------|---------|
| fxstreet.com | httpx | `div.fxs_article_body` | `.fxs_ad, script, style, aside` | No |
| forexlive.com | curl-cffi | `article .article-body, .post-content` | `script, style, .ad-container, aside` | Yes |
| investing.com | curl-cffi | `div.article_WYSIWYG__O0uhw, div[data-test='article-body']` | `script, style, .ad-slot, .disclaimer` | Yes |
| cnbc.com | httpx | `div.ArticleBody-articleBody` | `script, style, .InlineVideo, aside` | No |
| dailyfx.com | httpx | `div.dfx-article__content` | `script, style, .dfx-ad, aside` | No |
| bbc.com | httpx | `[data-component='text-block'] p, article p` | (段落直接抽出) | No |
| marketwatch.com | curl-cffi | `div.article__body, div[itemprop='articleBody']` | `script, style, .advertisement, aside` | Yes |

**注意**: CSSセレクタは実装時にライブページで検証・調整が必要。各パーサーに複数フォールバックセレクタを設定。

### コンテンツ保存仕様

- プレーンテキストのみ（HTMLタグ除去済み）
- 最大5,000文字（単語境界で切り詰め、末尾 `...`）
- 段落間は `\n` で区切り
- `_clean_text()` で連続空白行の正規化

## Step 3: `scripts/scrape_news_content.py` 新規作成

### CLI インターフェース

```bash
# 基本使用法
python scripts/scrape_news_content.py --years 2020-2025

# レジューム（content既取得のURLをスキップ）
python scripts/scrape_news_content.py --years 2020-2025 --resume

# レートリミット・タイムアウト調整
python scripts/scrape_news_content.py --year 2024 --rate-limit 3.0 --timeout 20
```

### 引数

| 引数 | デフォルト | 説明 |
|------|----------|------|
| `--year` / `--years` | 必須 | 対象年 |
| `--input-dir` | `data/fundamental` | 入力ディレクトリ |
| `--input-prefix` | `news_rss` | 入力ファイル名プレフィックス |
| `--rate-limit` | `2.0` | ドメインあたりのリクエスト間隔（秒） |
| `--timeout` | `15.0` | 1リクエストあたりのタイムアウト（秒） |
| `--resume` | False | content既取得のURLをスキップ |
| `--overwrite` | False | 既存出力ファイルを上書き |
| `--dry-run` | False | 処理件数のみ確認 |

### 処理フロー

1. `news_rss_YYYY.csv` を `read_news_csv()` で読み込み
2. 各アイテムについて `ArticleFetcher.fetch()` で本文取得
3. `--resume` 時: `item.content` が既にある場合はスキップ
4. 500件ごとに中間保存（クラッシュ対策）
5. 完了後 `write_news_csv()` で同ファイルに上書き保存
6. 処理統計をログ出力（成功/失敗/スキップ件数）

## Step 4: リアルタイム統合 (`rss_collector.py`)

### 変更箇所

- `__init__` に `article_fetcher: ArticleFetcher | None = None` 引数追加
- `_enrich_content(items)` メソッド追加: 新着アイテムの本文を非同期取得
- `_poll_loop` 内でコールバック前に `_enrich_content` を呼び出し

### 設計方針

- **オプショナル**: `article_fetcher=None` なら従来通りタイトルのみ
- **ベストエフォート**: 取得失敗してもアイテムは配信（title + snippet は確保）
- **非ブロッキング**: `asyncio.wait_for(run_in_executor(...), timeout=10)` で個別タイムアウト
- 失敗は `logger.debug` レベル（ポーリングループを止めない）

## Step 5: LLMプロンプト更新

### `news_llm_analyzer.py`（リアルタイム）

`_build_prompt()` に記事本文セクションを追加:
- contentがある上位5記事を各500文字まで抜粋
- 合計3,000文字上限
- セクション名: `## 記事本文（抜粋）`

### `llm_context_generator.py`（バッチ）

`_format_news()` を拡張:
- 各ニュースの下に `要約: {content[:200]}...` を追記（contentがある場合のみ）
- セクションヘッダーを `ニュース見出しと記事抜粋` に変更

### トークン予算

- qwen3:14b コンテキスト: 32Kトークン
- リアルタイム: 見出し10件(~400tok) + 本文5件×500字(~2,500tok) ≈ 3,000tok
- バッチ: 見出し20件(~800tok) + 本文200字×20(~2,000tok) ≈ 3,000tok
- 十分に予算内

## Step 6: テスト

### `test_article_scraper.py`（新規）

| テストクラス | テスト内容 |
|------------|----------|
| `TestCleanText` | テキスト正規化、最大文字数切り詰め、空入力 |
| `TestParserRegistry` | register/get、未登録ドメインはNone |
| `TestFXStreetParser` | HTML構造からコンテンツ抽出、広告除去 |
| `TestForexLiveParser` | 同上パターン |
| `TestCNBCParser` | 同上パターン |
| `TestDailyFXParser` | 同上パターン |
| `TestBBCParser` | 段落ベースの抽出 |
| `TestInvestingComParser` | 同上パターン |
| `TestMarketWatchParser` | 同上パターン |
| `TestArticleFetcher` | レートリミット、HTTPエラー、タイムアウト、TLS選択 |
| `TestDomainExtraction` | 通常URL、www除去、不正URL |

### `test_news_csv_writer.py`（追加）

- content付きラウンドトリップ
- 旧CSV（content列なし）の読み込みでcontent=None

### テストでのHTTPモック

- `unittest.mock.patch` + `MagicMock` で httpx/curl-cffi をモック
- 各パーサーテストは代表的なHTMLフィクスチャを使用
- 既存パターン: `test_rss_collector.py`, `test_gdelt_client.py` と同一

## Step 7: 検証

```bash
# 1. ユニットテスト
python -m pytest tests/unit/adapters/fundamental/test_article_scraper.py -v
python -m pytest tests/unit/adapters/fundamental/test_news_csv_writer.py -v

# 2. パーサー検証（実際のサイトに対して少数テスト）
python -c "
from autotrader.adapters.fundamental.article_scraper import ArticleFetcher
f = ArticleFetcher()
r = f.fetch('https://www.fxstreet.com/news/...', 'fxstreet.com')
print(r.status, len(r.content or ''))
"

# 3. バッチスクレイピング（dry-run → 実行）
python scripts/scrape_news_content.py --year 2024 --dry-run
python scripts/scrape_news_content.py --year 2024

# 4. LLMコンテキスト再生成（本文付き）
python scripts/generate_fundamental_llm.py \
    --symbol USDJPY --year 2024 \
    --news-dir data/fundamental --news-prefix news_rss
```

## 実装順序

```
Phase 1: news_schemas.py + news_csv_writer.py（データモデル）
Phase 2: article_scraper.py（パーサーフレームワーク + 7パーサー）
Phase 3: 並列実行
  ├── scripts/scrape_news_content.py（バッチスクリプト）
  ├── rss_collector.py（リアルタイム統合）
  ├── news_llm_analyzer.py + llm_context_generator.py（LLMプロンプト）
  └── テスト全般
Phase 4: ライブページでCSSセレクタ検証・調整
```
