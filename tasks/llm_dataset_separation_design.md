# LLMデータセット分離設計

## 1. 概要

現行の `LLMContextGenerator` は**月次粒度**でイベントとニュースを混在処理し、
単一CSV（`llm_context_SYMBOL_YYYY.csv`）に5カラムで出力している。

本設計では以下の3点を達成する:

1. **日次粒度**への移行（365行/年、リアルトレード時の粒度と一致）
2. **イベントCSVとニュースCSVの完全分離**（異なる影響特性に対応）
3. **データ全量使用**（先頭N件カットではなく時間的に網羅）

---

## 2. CSVスキーマ設計

### 2.1 イベントデータセット: `llm_events_SYMBOL_YYYY.csv`

経済指標の発表結果から、短期的な価格インパクトと収束予測を日次で出力する。

| カラム名 | 型 | 範囲 | 説明 |
|---------|-----|------|------|
| `date` | str (ISO8601) | `YYYY-MM-DD` | 対象日（UTC基準） |
| `event_count` | int | 0- | 当日の関連イベント総数 |
| `high_impact_count` | int | 0- | 高インパクト指標の数 |
| `net_surprise_score` | float | -1.0 ~ +1.0 | 当日の予実乖離の加重合算スコア。基軸通貨ポジティブ=+、決済通貨ポジティブ=- |
| `dominant_event_name` | str | - | 最も影響力の大きいイベント名 |
| `dominant_surprise_pct` | float | -inf ~ +inf | 最大影響イベントのサプライズ率（(実績-予測)/|予測|） |
| `expected_volatility` | float | 0.0 ~ 2.0 | LLM予測のボラティリティ倍率（1.0=通常比） |
| `price_direction_bias` | float | -1.0 ~ +1.0 | 指標結果に基づく短期的な価格方向予測（+は基軸通貨高） |
| `convergence_hours` | float | 0.5 ~ 72.0 | 指標インパクトが通常ボラに収束するまでの推定時間 |
| `trade_caution_level` | int | 0, 1, 2 | 取引注意度。0=通常、1=注意（ボラ高）、2=回避推奨（NFP等の超重要指標日） |
| `summary` | str | 最大200文字 | 当日のイベント分析要約（日本語） |

**設計意図:**
- `net_surprise_score`: 複数イベントのサプライズを通貨方向で正規化・合算。
  トレードロジックがマクロバイアスとして直接参照可能。
- `expected_volatility`: ボラティリティスパイクの事前予測。
  ポジションサイジングの縮小判断に使用。
- `convergence_hours`: サプライズの効果持続時間。
  エントリータイミング判断に使用（収束待ちか、トレンド追随か）。
- `trade_caution_level`: NFP・FOMC等の超重要日を数値化。
  ハードガード（エントリー停止）の判断材料。

**ファイル例:**
```csv
date,event_count,high_impact_count,net_surprise_score,dominant_event_name,dominant_surprise_pct,expected_volatility,price_direction_bias,convergence_hours,trade_caution_level,summary
2024-01-05,8,2,0.42,NFP,0.422,1.8,0.65,4.0,2,NFP大幅上振れ(25.6万vs18.0万予測)でドル急騰。失業率も予想一致で安定感。
2024-01-08,3,0,-0.05,Consumer Credit,-0.031,0.9,-0.10,1.5,0,軽微な指標のみ。消費者信用が僅かに下振れ。
```

### 2.2 ニュースデータセット: `llm_news_SYMBOL_YYYY.csv`

ニュース記事群から中長期的な市場センチメントと方向性バイアスを日次で出力する。

| カラム名 | 型 | 範囲 | 説明 |
|---------|-----|------|------|
| `date` | str (ISO8601) | `YYYY-MM-DD` | 対象日（UTC基準） |
| `article_count` | int | 0- | 当日の分析対象記事数 |
| `sentiment_score` | float | -1.0 ~ +1.0 | 総合ニュースセンチメント。+は強気/基軸通貨に有利 |
| `sentiment_confidence` | float | 0.0 ~ 1.0 | センチメント判定の確信度（記事数・一貫性に基づく） |
| `macro_bias_score` | float | -1.0 ~ +1.0 | マクロ経済全体のバイアス。金融政策・経済成長の方向性 |
| `policy_divergence_score` | float | -1.0 ~ +1.0 | 基軸通貨と決済通貨の金融政策乖離度。+は基軸通貨の引締め優位 |
| `risk_appetite_score` | float | -1.0 ~ +1.0 | リスク選好度。+はリスクオン（高金利通貨に有利）、-はリスクオフ |
| `geopolitical_risk_level` | int | 0, 1, 2, 3 | 地政学リスク度。0=平穏、1=注意、2=緊張、3=危機的 |
| `dominant_theme` | str | 最大100文字 | 当日のニュースの支配的テーマ（日本語） |
| `summary` | str | 最大200文字 | ニュース分析の要約（日本語） |
| `session_detail` | str | JSON | セッション別内訳（後述） |

**`session_detail` の構造:**
セッション（東京・ロンドン・NY）ごとの記事数とセンチメントを保持する。
```json
{
  "tokyo": {"count": 5, "sentiment": 0.2},
  "london": {"count": 12, "sentiment": -0.1},
  "ny": {"count": 8, "sentiment": 0.3}
}
```

**設計意図:**
- `sentiment_score`: リアルトレードの `NewsLLMAnalyzer.analyze()` と
  同一の意味を持つスコア。バックテスト/リアルの一貫性を保証。
- `policy_divergence_score`: 通貨ペア固有の金融政策差。
  中長期トレンドの方向性判断に使用。
- `risk_appetite_score`: リスクオン/オフの尺度。
  クロス円（AUDJPY等）やリスク通貨の方向性判断に使用。
- `session_detail`: 時間帯ごとのセンチメント差異を保持。
  東京時間ポジティブ→ロンドンでネガティブ転換等のパターンを検出可能。
- `sentiment_confidence`: 記事数が少ない日や記事間で方向が矛盾する場合は
  低くなり、トレードロジック側でウェイト調整に使える。

**ファイル例:**
```csv
date,article_count,sentiment_score,sentiment_confidence,macro_bias_score,policy_divergence_score,risk_appetite_score,geopolitical_risk_level,dominant_theme,summary,session_detail
2024-01-15,25,0.35,0.72,0.40,0.55,-0.10,1,FRB利下げ観測後退,米CPI上振れ観測でドル買い優勢。日銀政策正常化観測もあり円安は限定的。,"{""tokyo"":{""count"":5,""sentiment"":0.2},""london"":{""count"":12,""sentiment"":0.4},""ny"":{""count"":8,""sentiment"":0.3}}"
```

---

## 3. LLMプロンプト設計

### 3.1 イベント分析プロンプト

**入力データ:**
当日の全発表済み経済イベント（`events_YYYY.csv` から対象シンボル関連通貨のみ抽出）

**プロンプト:**
```
あなたはFXトレードの経済指標アナリストです。
以下の経済指標発表結果に基づき、{SYMBOL}への短期的インパクトを分析してください。

## 分析対象
- シンボル: {SYMBOL} ({BASE}/{QUOTE})
- 分析日: {YYYY年MM月DD日}

## 当日の発表済み経済指標（{N}件）
{各イベントの一覧:
  - HH:MM [高/中/低インパクト] {通貨} {指標名}: 実績={actual} 予測={forecast} 前回={previous} サプライズ={surprise_pct}}

## 分析指示
1. 各指標のサプライズ方向と大きさを評価
2. {BASE}と{QUOTE}への相対的な影響を判断
3. 複数指標の相互関係（矛盾・補強）を考慮
4. インパクトの持続時間を推定（即時収束型 vs 持続型）

## 出力形式（JSONのみで回答）
{
  "net_surprise_score": <-1.0~+1.0: 加重サプライズ合計。+は{BASE}高方向>,
  "dominant_event_name": "<最大影響イベント名>",
  "dominant_surprise_pct": <最大影響イベントのサプライズ率>,
  "expected_volatility": <0.0~2.0: 通常比ボラティリティ倍率>,
  "price_direction_bias": <-1.0~+1.0: 短期価格方向。+は{SYMBOL}上昇>,
  "convergence_hours": <0.5~72.0: インパクト収束までの推定時間>,
  "trade_caution_level": <0/1/2: 0=通常, 1=注意, 2=回避推奨>,
  "summary": "<分析要約（日本語、200文字以内）>"
}
```

**イベントなしの日の処理:**
LLM呼び出しをスキップし、以下のデフォルト値を使用する。
```python
{
    "net_surprise_score": 0.0,
    "dominant_event_name": "",
    "dominant_surprise_pct": 0.0,
    "expected_volatility": 1.0,
    "price_direction_bias": 0.0,
    "convergence_hours": 0.0,
    "trade_caution_level": 0,
    "summary": "関連経済指標の発表なし",
}
```

### 3.2 ニュース分析プロンプト

**入力データ:**
当日の全ニュース記事（`news_rss_YYYY.csv` および `news/news_YYYY.csv` から
対象シンボル関連通貨でフィルタ、セッション別にグループ化）

**プロンプト:**
```
あなたはFXトレードのニュースアナリストです。
以下のニュース記事群に基づき、{SYMBOL}に対する市場センチメントを分析してください。

## 分析対象
- シンボル: {SYMBOL} ({BASE}/{QUOTE})
- 分析日: {YYYY年MM月DD日}

## 東京セッション（UTC 00:00-08:00）のニュース（{N}件）
{各記事:
  - HH:MM | {ソース名} | {タイトル}
    本文抜粋: {content[:300]}...}

## ロンドンセッション（UTC 08:00-14:00）のニュース（{N}件）
{同上}

## NYセッション（UTC 14:00-24:00）のニュース（{N}件）
{同上}

## 分析指示
1. 各セッションのセンチメント傾向を個別に評価
2. {BASE}と{QUOTE}に関するニュースの方向性を区別
3. 金融政策に関する言及（利上げ/利下げ観測等）を特に重視
4. 地政学リスク要因の有無と影響度を評価
5. リスク選好/回避の傾向を判断
6. センチメントの一貫性に基づき確信度を設定

## 出力形式（JSONのみで回答）
{
  "sentiment_score": <-1.0~+1.0: 総合センチメント。+は{SYMBOL}強気>,
  "sentiment_confidence": <0.0~1.0: 確信度>,
  "macro_bias_score": <-1.0~+1.0: マクロ経済バイアス>,
  "policy_divergence_score": <-1.0~+1.0: 金融政策乖離。+は{BASE}引締め優位>,
  "risk_appetite_score": <-1.0~+1.0: リスク選好度。+はリスクオン>,
  "geopolitical_risk_level": <0/1/2/3: 地政学リスク度>,
  "dominant_theme": "<支配的テーマ（日本語、100文字以内）>",
  "summary": "<分析要約（日本語、200文字以内）>",
  "session_sentiment": {
    "tokyo": <-1.0~+1.0>,
    "london": <-1.0~+1.0>,
    "ny": <-1.0~+1.0>
  }
}
```

### 3.3 チャンク分割戦略

ニュースデータは日によって記事数が大きく変動する（1日0件~数百件）。
LLMのコンテキストウィンドウ制限（qwen3:14b で 4096 トークン、実質入力3000トークン程度）
に対処する。

**戦略: セッション内圧縮 + 重要度フィルタ**

```
1日の全記事
  ↓ フィルタ1: 対象シンボル関連通貨のみ抽出
  ↓ フィルタ2: FX専門ソース（FX_RSS_SOURCES）を優先
  ↓ セッション別グループ化（東京/ロンドン/NY）
  ↓ 各セッション:
      ├─ FX専門ソース記事: 全件含める（本文300文字まで）
      └─ 一般ソース記事: ソースごとに最大1件、見出しのみ
  ↓ トークン見積もり
  ↓ 合計 > 2500トークン の場合:
      ├─ 一般ソース記事を除外
      └─ FX専門ソース記事の本文を150文字に短縮
  ↓ それでも > 2500トークン の場合:
      ├─ セッションごとに最大10件に制限
      └─ 見出しのみに統一
```

**記事数が0件の日の処理:**
```python
{
    "sentiment_score": 0.0,
    "sentiment_confidence": 0.0,
    "macro_bias_score": 0.0,
    "policy_divergence_score": 0.0,
    "risk_appetite_score": 0.0,
    "geopolitical_risk_level": 0,
    "dominant_theme": "",
    "summary": "関連ニュースなし",
    "session_sentiment": {"tokyo": 0.0, "london": 0.0, "ny": 0.0},
}
```

**マルチチャンク呼び出し（将来拡張）:**
現時点では単一プロンプトで処理する。記事数が極端に多い場合は圧縮でカバーする。
将来的にコンテキスト長の大きいモデルへの移行時には、チャンク分割→結果マージの
パイプラインに拡張可能な設計とする。

---

## 4. 生成パイプライン設計

### 4.1 ジェネレータークラスの分割方針

現行の `LLMContextGenerator` を以下の3クラスに分割する。

```
adapters/fundamental/
  llm_context_generator.py  ← 削除予定（段階的に廃止）
  llm_event_generator.py    ← 新規: イベント分析ジェネレーター
  llm_news_generator.py     ← 新規: ニュース分析ジェネレーター
  llm_generator_base.py     ← 新規: 共通基底クラス
```

#### `LLMGeneratorBase`（共通基底）
```
責務:
  - Ollamaクライアント管理（接続・リトライ・レスポンスパース）
  - JSON抽出ロジック（直接パース → コードブロック → ブレース抽出）
  - スコアクリッピング（-1.0 ~ +1.0）
  - CSV書き込みユーティリティ
  - 日次ループ制御

メソッド:
  - _call_ollama(prompt: str) -> dict
  - _parse_response(content: str) -> dict
  - _clip(val, lo, hi) -> float
  - _write_csv(rows, columns, output_path)
```

#### `LLMEventGenerator`（イベント分析）
```
責務:
  - events_YYYY.csv からシンボル関連イベントを日次抽出
  - 日ごとにLLMプロンプトを構築・実行
  - 結果を llm_events_SYMBOL_YYYY.csv に出力

メソッド:
  - generate_for_symbol_year(symbol, year, events, output_dir, overwrite) -> Path
  - _group_by_date(events, year) -> dict[date, list[EconomicEvent]]
  - _analyze_date(symbol, base, quote, date, events) -> dict
  - _build_event_prompt(symbol, base, quote, date, events) -> str
  - _build_default_event_result() -> dict
```

#### `LLMNewsGenerator`（ニュース分析）
```
責務:
  - news_YYYY.csv / news_rss_YYYY.csv からシンボル関連ニュースを日次抽出
  - セッション別にグループ化
  - チャンク圧縮を適用
  - 日ごとにLLMプロンプトを構築・実行
  - 結果を llm_news_SYMBOL_YYYY.csv に出力

メソッド:
  - generate_for_symbol_year(symbol, year, news_items, output_dir, overwrite) -> Path
  - _group_by_date(news_items, year) -> dict[date, list[NewsItem]]
  - _split_by_session(news_items) -> dict[str, list[NewsItem]]
  - _compress_for_prompt(session_items, max_tokens) -> str
  - _analyze_date(symbol, base, quote, date, news_items) -> dict
  - _build_news_prompt(symbol, base, quote, date, session_groups) -> str
  - _build_default_news_result() -> dict
```

### 4.2 日次処理の流れ

```
generate_for_symbol_year("USDJPY", 2024)
  │
  ├─ 1. 入力データの通貨フィルタ
  │     events: currency in ("USD", "JPY") のみ
  │     news: currencies にUSDまたはJPYを含むもの
  │
  ├─ 2. 日付リスト生成（1/1 ~ 12/31 の全日）
  │
  └─ 3. 各日に対して:
        │
        ├─ 当日のイベント/ニュースを抽出
        │
        ├─ データ有無で分岐
        │   ├─ データなし → デフォルト値をそのまま使用（LLM呼び出しスキップ）
        │   └─ データあり → プロンプト構築 → LLM呼び出し → パース
        │
        ├─ 結果行をリストに追加
        │
        └─ (次の日へ)

  ├─ 4. 全行をCSV書き込み
  └─ 完了ログ
```

### 4.3 時間帯を考慮した記事グループ化

ニュース記事のセッション分割ロジック:

```python
# セッション定義（UTC基準）
SESSION_RANGES = {
    "tokyo":  (0, 8),    # 00:00-07:59 UTC（日本時間 09:00-16:59）
    "london": (8, 14),   # 08:00-13:59 UTC（ロンドン時間 08:00-13:59 夏時間）
    "ny":     (14, 24),  # 14:00-23:59 UTC（NY時間 09:00-18:59 夏時間）
}

def _split_by_session(
    news_items: list[NewsItem],
) -> dict[str, list[NewsItem]]:
    result = {"tokyo": [], "london": [], "ny": []}
    for item in news_items:
        hour = item.published_at.hour
        if 0 <= hour < 8:
            result["tokyo"].append(item)
        elif 8 <= hour < 14:
            result["london"].append(item)
        else:
            result["ny"].append(item)
    return result
```

### 4.4 生成スクリプトの変更

`scripts/generate_fundamental_llm.py` を拡張して2つのサブコマンドを持つ形にする。

```
# イベントCSV生成
python scripts/generate_fundamental_llm.py events \
    --symbol USDJPY --years 2020-2024

# ニュースCSV生成
python scripts/generate_fundamental_llm.py news \
    --symbol USDJPY --years 2020-2024 \
    --news-dir data/fundamental/news \
    --rss-dir data/fundamental

# 両方生成（デフォルト）
python scripts/generate_fundamental_llm.py all \
    --symbol USDJPY --years 2020-2024
```

既存の `--year` / `--years` オプションは維持する。

### 4.5 処理量の見積もり

| 項目 | 値 |
|------|-----|
| 1年あたりの日数 | 365 |
| イベント: 1日あたりの関連イベント数（USDJPY） | 5-20件 |
| ニュース: 1日あたりの関連記事数（USDJPY） | 0-100件（RSSフィルタ後は0-30件程度） |
| LLM呼び出し回数/年/シンボル | 最大730回（365日 x 2タイプ） |
| 現行（月次）のLLM呼び出し回数/年/シンボル | 12回 |
| 想定処理時間（qwen3:14b, GPU） | 1呼び出しあたり2-5秒 → 1年分で約30-60分 |

**高速化オプション:**
- イベント/ニュースが0件の日はLLM呼び出しをスキップ（30-50%削減）
- 低インパクトイベントのみの日はルールベースでデフォルト値を使用（さらに20%削減）
- `--parallel-days` オプションで日単位の並列化（将来）

---

## 5. トレードロジック統合設計

### 5.1 FundamentalContext スキーマの変更案

現行の `FundamentalContext` は5つのスコアフィールドを持つ。
新設計ではイベント由来とニュース由来のスコアを明確に分離する。

```python
@dataclass(frozen=True)
class FundamentalContext:
    """ファンダメンタルコンテキスト（新設計）

    イベント系（短期インパクト）:
        event_surprise_score: 当日の予実乖離スコア
        event_direction_bias: イベント由来の短期価格方向バイアス
        event_volatility: イベント由来のボラティリティ倍率
        event_convergence_hours: インパクト収束までの推定時間
        event_caution_level: 取引注意度

    ニュース系（中長期バイアス）:
        news_sentiment_score: ニュースセンチメント
        news_sentiment_confidence: センチメント確信度
        news_macro_bias: マクロ経済バイアス
        news_policy_divergence: 金融政策乖離度
        news_risk_appetite: リスク選好度
        news_geopolitical_risk: 地政学リスク度

    共通:
        upcoming_events: 直近の予定イベント
        has_high_impact_within_30min: 30分以内の高インパクト指標
    """

    # イベント系
    event_surprise_score: float = 0.0
    event_direction_bias: float = 0.0
    event_volatility: float = 1.0
    event_convergence_hours: float = 0.0
    event_caution_level: int = 0

    # ニュース系
    news_sentiment_score: float = 0.0
    news_sentiment_confidence: float = 0.0
    news_macro_bias: float = 0.0
    news_policy_divergence: float = 0.0
    news_risk_appetite: float = 0.0
    news_geopolitical_risk: int = 0

    # 共通（既存維持）
    upcoming_events: list[dict] = field(default_factory=list)
    has_high_impact_within_30min: bool = False
```

**後方互換性:**
既存の `macro_bias_score`, `post_event_bias_score`, `sentiment_score` は
プロパティとしてマッピングを提供し、段階的に移行する。

```python
@property
def macro_bias_score(self) -> float:
    """後方互換: ニュースマクロバイアスに対応"""
    return self.news_macro_bias

@property
def post_event_bias_score(self) -> float:
    """後方互換: イベント方向バイアスに対応"""
    return self.event_direction_bias

@property
def sentiment_score(self) -> float:
    """後方互換: ニュースセンチメントに対応"""
    return self.news_sentiment_score

@property
def macro_bias_summary(self) -> str:
    """後方互換"""
    return ""

@property
def post_event_summary(self) -> str:
    """後方互換"""
    return ""
```

### 5.2 BacktestFundamentalProvider の変更案

```python
class BacktestFundamentalProvider:
    """バックテスト用ファンダメンタルプロバイダー（新設計）

    日次粒度のLLM事前生成CSVを読み込み、
    バックテスト時刻に合わせてFundamentalContextを提供する。
    """

    def __init__(self, event_guard_minutes: int = 30):
        # 既存のイベントストレージ
        self._events: list[EconomicEvent] = []
        self._events_sorted_ts: list[float] = []
        self._normalizer = EconomicEventNormalizer()

        # 新: イベントLLMコンテキスト（日次）
        # symbol → (date_ts一覧, コンテキスト一覧)
        self._event_llm_ts: dict[str, list[float]] = {}
        self._event_llm_data: dict[str, list[dict]] = {}

        # 新: ニュースLLMコンテキスト（日次）
        self._news_llm_ts: dict[str, list[float]] = {}
        self._news_llm_data: dict[str, list[dict]] = {}

        # 旧: 月次LLMコンテキスト（後方互換）
        self._llm_ts: dict[str, list[float]] = {}
        self._llm_data: dict[str, list[dict]] = {}

    def load_event_llm_csv(
        self, csv_path: str | Path, symbol: str
    ) -> int:
        """日次イベントLLM CSVを読み込み
        llm_events_SYMBOL_YYYY.csv を読み込む。
        """
        ...

    def load_news_llm_csv(
        self, csv_path: str | Path, symbol: str
    ) -> int:
        """日次ニュースLLM CSVを読み込み
        llm_news_SYMBOL_YYYY.csv を読み込む。
        """
        ...

    def get_context(
        self, current_time: datetime, symbol: str
    ) -> FundamentalContext:
        """指定時刻のファンダメンタルコンテキストを取得

        日次LLMデータがあればそちらを優先。
        なければ旧月次LLMデータにフォールバック。
        どちらもなければイベントベースの計算結果を使用。
        """
        # 1. 日次イベントLLMコンテキスト取得（bisect）
        event_ctx = self._get_daily_event_context(
            current_time, symbol
        )
        # 2. 日次ニュースLLMコンテキスト取得（bisect）
        news_ctx = self._get_daily_news_context(
            current_time, symbol
        )
        # 3. 生イベントから upcoming / high_impact_soon を計算
        #    （既存ロジック維持）
        ...
        # 4. FundamentalContext を構築して返却
        return FundamentalContext(
            event_surprise_score=event_ctx["net_surprise_score"],
            event_direction_bias=event_ctx["price_direction_bias"],
            event_volatility=event_ctx["expected_volatility"],
            event_convergence_hours=event_ctx["convergence_hours"],
            event_caution_level=event_ctx["trade_caution_level"],
            news_sentiment_score=news_ctx["sentiment_score"],
            news_sentiment_confidence=news_ctx["sentiment_confidence"],
            news_macro_bias=news_ctx["macro_bias_score"],
            news_policy_divergence=news_ctx["policy_divergence_score"],
            news_risk_appetite=news_ctx["risk_appetite_score"],
            news_geopolitical_risk=news_ctx["geopolitical_risk_level"],
            upcoming_events=upcoming_dicts,
            has_high_impact_within_30min=high_impact_soon,
        )
```

### 5.3 トレードボットでの使用方法

`BacktestRunner._run_unified_year` でのファンダメンタル統合箇所:

```python
# 現行: 高インパクト指標直前のスキップのみ
if _fctx.has_high_impact_within_30min:
    continue

# 新設計: 段階的な使用
# (A) ハードガード: イベント注意度による完全スキップ
if _fctx.has_high_impact_within_30min:
    continue
if _fctx.event_caution_level >= 2:
    continue  # NFP等の超重要指標日

# (B) ソフトガード: ボラティリティによるロット調整
#     SoftGuardConfig / PositionSizer に渡す
volatility_multiplier = _fctx.event_volatility

# (C) エントリーフィルタ: 方向性一致チェック
#     シグナル方向とファンダメンタルバイアスの一致度
fundamental_alignment = _compute_alignment(
    signal_direction,
    _fctx.event_direction_bias,
    _fctx.news_macro_bias,
)

# (D) 信頼度調整: ニュースセンチメントを加味
adjusted_confidence = base_confidence * (
    1.0 + _fctx.news_sentiment_score
    * _fctx.news_sentiment_confidence
    * 0.1  # 最大10%の調整
)
```

**PositionManager での使用:**
```python
# 地政学リスク高→トレーリングSLを引き締め
if _fctx.news_geopolitical_risk >= 2:
    trailing_sl_distance *= 0.8  # 20%タイトに

# イベント収束待ち→エグジット判断
if _fctx.event_convergence_hours > 0:
    # インパクト収束前に含み益があれば部分決済を検討
    ...
```

---

## 6. リアルトレードとの一貫性

### 6.1 粒度の一致

| 項目 | バックテスト（事前生成） | リアルトレード |
|------|----------------------|--------------|
| イベントデータ | `llm_events_SYMBOL_YYYY.csv`（日次） | `FundamentalMemoryService.write_post_event_bias()`（イベント発生時） |
| ニュースデータ | `llm_news_SYMBOL_YYYY.csv`（日次） | `NewsLLMAnalyzer.analyze()`（RSSポーリング時） |
| FundamentalContext | `BacktestFundamentalProvider.get_context()` | `FundamentalMemoryService.get_context_for_llm()` |

**現行の問題:** バックテストは月次、リアルはイベント駆動。粒度の大きな乖離。
**解決:** 両方とも日次粒度に統一。バックテストCSVの各行は「その日のリアルトレードで
LLMが生成したであろう分析結果」を再現する。

### 6.2 スコアの意味的一致

| スコア | バックテストCSVカラム | リアルトレードでの生成元 |
|-------|---------------------|---------------------|
| `sentiment_score` | `llm_news` の `sentiment_score` | `NewsLLMAnalyzer._call_ollama_sync()` |
| `macro_bias` | `llm_news` の `macro_bias_score` | `FundamentalMemoryService.write_macro_bias()` |
| `post_event_bias` | `llm_events` の `price_direction_bias` | `FundamentalMemoryService.write_post_event_bias()` |

### 6.3 リアルトレード側の改修

リアルトレード側（`LiveTradingEngine`, `FundamentalMemoryService`）は
現時点では変更しない。新しい `FundamentalContext` スキーマへの移行に合わせて
段階的に改修する。

改修時のポイント:
1. `FundamentalMemoryService.get_context_for_llm()` の戻り値を
   新 `FundamentalContext` に合わせる
2. `NewsLLMAnalyzer` の出力を拡張
   （`sentiment_score` のみ → `sentiment_score` + `sentiment_confidence`
   + `policy_divergence_score` + `risk_appetite_score`）
3. 朝のLLM市場観更新（`_run_morning_update`）の出力を
   `news_macro_bias` / `news_policy_divergence` に分割

### 6.4 フォールバック戦略

```
FundamentalContext の取得優先順位:

1. 日次イベントLLM CSV + 日次ニュースLLM CSV（新設計）
2. 旧月次 llm_context CSV（後方互換）
3. イベントベースのルール計算（_estimate_bias_from_events）
4. ニュートラル値（FundamentalContext.neutral()）
```

これにより、新CSVがまだ生成されていない年やシンボルでも
既存のバックテストが破壊されない。

---

## 7. 実装フェーズ

### Phase 1: 基盤（新スキーマ・ジェネレーター）
1. `FundamentalContext` の新スキーマ定義 + 後方互換プロパティ
2. `LLMGeneratorBase` 共通基底クラス
3. `LLMEventGenerator` 実装
4. `LLMNewsGenerator` 実装
5. 生成スクリプト改修

### Phase 2: バックテスト統合
6. `BacktestFundamentalProvider` に日次CSV読み込みメソッド追加
7. `get_context()` のフォールバック階層実装
8. `BacktestRunner` でのファンダメンタルコンテキスト使用拡張

### Phase 3: トレードロジック統合
9. ハードガード拡張（`event_caution_level`）
10. ソフトガード拡張（ボラティリティ乗数、方向性一致）
11. PositionManager への地政学リスク反映

### Phase 4: リアルトレード一貫性
12. `FundamentalMemoryService` の新スキーマ対応
13. `NewsLLMAnalyzer` の出力拡張
14. 朝のLLM更新の出力分割

---

## 8. ファイル変更一覧

| ファイルパス | 変更種別 | 内容 |
|------------|---------|------|
| `autotrader/adapters/fundamental/schemas.py` | 変更 | FundamentalContext を拡張、後方互換プロパティ追加 |
| `autotrader/adapters/fundamental/llm_generator_base.py` | 新規 | LLM呼び出し共通基底クラス |
| `autotrader/adapters/fundamental/llm_event_generator.py` | 新規 | イベント分析ジェネレーター |
| `autotrader/adapters/fundamental/llm_news_generator.py` | 新規 | ニュース分析ジェネレーター |
| `autotrader/adapters/fundamental/backtest_provider.py` | 変更 | 日次CSV読み込み + フォールバック |
| `autotrader/adapters/fundamental/llm_context_generator.py` | 非推奨化 | 段階的に廃止（Phase 4完了後） |
| `scripts/generate_fundamental_llm.py` | 変更 | サブコマンド化（events/news/all） |
| `autotrader/backtest/runner.py` | 変更 | ファンダメンタル統合拡張 |
| `autotrader/backtest/executor.py` | 変更 | 設定項目追加 |

---

## 9. リスクと緩和策

| リスク | 影響度 | 緩和策 |
|-------|--------|-------|
| LLM呼び出し回数の大幅増加（12→730回/年/シンボル） | 中 | データなし日のスキップ、低インパクト日のルールベース処理 |
| LLMの出力品質の日次ばらつき | 中 | スコアの移動平均化はトレードロジック側で行う（CSV生データは素のまま） |
| 後方互換性の破壊 | 高 | 旧CSVフォールバック、プロパティによる旧名アクセス維持 |
| ニュースデータの品質（GDELT記事のノイズ） | 中 | FX専門ソース優先フィルタ、一般ソース記事は見出しのみ |
| コンテキストウィンドウ超過 | 低 | 段階的圧縮戦略、最終手段として見出しのみモード |
