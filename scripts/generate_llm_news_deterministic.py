#!/usr/bin/env python3
"""llm_news CSV 決定論的生成スクリプト

RSSニュースデータ（news_rss_YYYY.csv）からヘッドラインとTone値を
ヒューリスティック分析し、通貨ペア別の日次ニュースCSVを生成する。

LLM不要・完全決定論的。GDELT Tone値 + キーワード分析による
センチメントスコア・マクロバイアス・政策乖離等を算出。

使用例:
    uv run python scripts/generate_llm_news_deterministic.py \
        --symbols AUDJPY,AUDUSD,EURJPY,EURUSD,GBPJPY,GBPUSD,NZDUSD,USDCAD,USDCHF \
        --years 2020-2025
"""

from __future__ import annotations

import argparse
import csv
import math
import re
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

# ============================================================
# 通貨ペア設定
# ============================================================

_SYMBOL_CURRENCIES: dict[str, tuple[str, str]] = {
    "USDJPY": ("USD", "JPY"),
    "EURUSD": ("EUR", "USD"),
    "GBPUSD": ("GBP", "USD"),
    "AUDUSD": ("AUD", "USD"),
    "NZDUSD": ("NZD", "USD"),
    "USDCHF": ("USD", "CHF"),
    "USDCAD": ("USD", "CAD"),
    "EURJPY": ("EUR", "JPY"),
    "GBPJPY": ("GBP", "JPY"),
    "AUDJPY": ("AUD", "JPY"),
}

# ============================================================
# キーワード辞書: テーマ検出 + センチメント修正
# ============================================================

# 地政学リスクキーワード（risk_level割当）
_GEOPOLITICAL_KEYWORDS: dict[str, int] = {
    "war": 3, "missile": 3, "invasion": 3, "nuclear": 3,
    "military": 2, "sanction": 2, "conflict": 2,
    "terror": 3, "assassination": 3, "attack": 2,
    "tension": 1, "crisis": 2, "threat": 1,
    "geopolit": 1, "escalat": 2, "ceasefire": 1,
    "ukraine": 2, "russia": 1, "iran": 1, "china": 1,
    "taiwan": 2, "north korea": 2, "middle east": 1,
    "gaza": 2, "israel": 1, "houthi": 2,
}

# 金融政策キーワード（ベース通貨強気=+, 弱気=-）
_HAWKISH_KEYWORDS = [
    "rate hike", "tighten", "hawkish", "inflation",
    "tapering", "restrictive", "raise rate",
    "monetary tightening", "higher rate",
]
_DOVISH_KEYWORDS = [
    "rate cut", "easing", "dovish", "stimulus",
    "accommodat", "lower rate", "quantitative",
    "monetary easing", "pause", "hold rate",
]

# リスクセンチメントキーワード
_RISK_ON_KEYWORDS = [
    "rally", "surge", "gain", "bullish", "optimis",
    "recovery", "upbeat", "growth", "expansion",
    "risk appetite", "stock rise", "equity gain",
]
_RISK_OFF_KEYWORDS = [
    "crash", "plunge", "slump", "bearish", "pessimis",
    "recession", "downturn", "risk aversion", "safe haven",
    "sell-off", "selloff", "fear", "volatility spike",
    "flight to safety", "haven",
]

# マクロ経済テーマキーワード
_MACRO_THEMES: dict[str, str] = {
    "gdp": "GDP",
    "employment": "雇用",
    "nonfarm": "雇用統計",
    "non-farm": "雇用統計",
    "payroll": "雇用統計",
    "unemployment": "失業率",
    "cpi": "物価指標",
    "inflation": "インフレ",
    "ppi": "生産者物価",
    "retail": "小売",
    "manufacturing": "製造業",
    "pmi": "PMI",
    "trade balance": "貿易収支",
    "housing": "住宅",
    "consumer confidence": "消費者信頼感",
    "fed": "FRB",
    "fomc": "FOMC",
    "ecb": "ECB",
    "boj": "日銀",
    "boe": "BOE",
    "rba": "RBA",
    "rbnz": "RBNZ",
    "snb": "SNB",
    "boc": "BOC",
    "oil": "原油",
    "gold": "金",
    "covid": "コロナ",
    "pandemic": "パンデミック",
    "lockdown": "ロックダウン",
    "vaccine": "ワクチン",
    "tariff": "関税",
    "trade war": "貿易戦争",
    "brexit": "Brexit",
    "election": "選挙",
}

# 通貨別中央銀行マッピング
_CURRENCY_BANK: dict[str, list[str]] = {
    "USD": ["fed", "fomc", "powell", "federal reserve"],
    "EUR": ["ecb", "lagarde", "european central"],
    "JPY": ["boj", "ueda", "kuroda", "bank of japan"],
    "GBP": ["boe", "bailey", "bank of england"],
    "AUD": ["rba", "bullock", "lowe",
            "reserve bank of australia"],
    "NZD": ["rbnz", "orr", "reserve bank of new zealand"],
    "CHF": ["snb", "jordan", "swiss national bank"],
    "CAD": ["boc", "macklem", "bank of canada"],
}


# ============================================================
# 分析ロジック
# ============================================================


def _extract_tone(snippet: str) -> float | None:
    """GDELTのTone値を抽出

    Args:
        snippet: スニペットフィールド

    Returns:
        float | None: Tone値
    """
    if not snippet:
        return None
    if snippet.startswith("Tone:"):
        try:
            val = snippet.split(":")[1].strip()
            # 複数値がある場合は最初のものだけ
            val = val.split(",")[0].split(" ")[0]
            return float(val)
        except (ValueError, IndexError):
            return None
    return None


def _count_keyword_matches(
    text: str,
    keywords: list[str],
) -> int:
    """テキスト中のキーワードマッチ数

    Args:
        text: 検索対象テキスト
        keywords: キーワードリスト

    Returns:
        int: マッチ数
    """
    text_lower = text.lower()
    return sum(1 for kw in keywords if kw in text_lower)


def _detect_geopolitical_risk(
    titles: list[str],
) -> int:
    """記事タイトルから地政学リスクレベルを検出

    Args:
        titles: 記事タイトルリスト

    Returns:
        int: 0-3のリスクレベル
    """
    max_risk = 0
    risk_count = 0
    combined = " ".join(titles).lower()

    for keyword, level in _GEOPOLITICAL_KEYWORDS.items():
        if keyword in combined:
            max_risk = max(max_risk, level)
            risk_count += 1

    # 複数の地政学キーワードがある場合はリスク上昇
    if risk_count >= 3:
        max_risk = min(3, max_risk + 1)

    return max_risk


def _detect_dominant_theme(
    titles: list[str],
) -> str:
    """記事タイトルから支配的テーマを検出

    Args:
        titles: 記事タイトルリスト

    Returns:
        str: テーマ名（日本語100文字以内）
    """
    combined = " ".join(titles).lower()
    theme_counts: dict[str, int] = defaultdict(int)

    for keyword, theme in _MACRO_THEMES.items():
        count = combined.count(keyword)
        if count > 0:
            theme_counts[theme] += count

    if not theme_counts:
        return "一般市場動向"

    # 最頻テーマを取得
    sorted_themes = sorted(
        theme_counts.items(),
        key=lambda x: -x[1],
    )
    top_themes = sorted_themes[:3]
    return "・".join(t[0] for t in top_themes)


def _compute_policy_divergence(
    titles: list[str],
    base: str,
    quote: str,
) -> float:
    """金融政策乖離スコアを算出

    base通貨の引締め > quote通貨の引締めなら正、
    逆なら負。

    Args:
        titles: 記事タイトルリスト
        base: 基軸通貨
        quote: 決済通貨

    Returns:
        float: -1.0 ~ +1.0
    """
    combined = " ".join(titles).lower()

    base_hawkish = 0
    base_dovish = 0
    quote_hawkish = 0
    quote_dovish = 0

    # 通貨別中央銀行キーワードに近接する
    # タカ派/ハト派キーワードを検出
    base_banks = _CURRENCY_BANK.get(base, [])
    quote_banks = _CURRENCY_BANK.get(quote, [])

    for title in titles:
        tl = title.lower()
        has_base = any(b in tl for b in base_banks)
        has_quote = any(b in tl for b in quote_banks)

        h = _count_keyword_matches(tl, _HAWKISH_KEYWORDS)
        d = _count_keyword_matches(tl, _DOVISH_KEYWORDS)

        if has_base:
            base_hawkish += h
            base_dovish += d
        if has_quote:
            quote_hawkish += h
            quote_dovish += d

    base_net = base_hawkish - base_dovish
    quote_net = quote_hawkish - quote_dovish
    divergence = base_net - quote_net

    # -1.0 ~ +1.0 にクランプ
    if divergence == 0:
        return 0.0
    return max(-1.0, min(1.0, divergence * 0.2))


def _compute_risk_appetite(
    titles: list[str],
    avg_tone: float,
) -> float:
    """リスク選好スコアを算出

    Args:
        titles: 記事タイトルリスト
        avg_tone: 平均Tone値

    Returns:
        float: -1.0 ~ +1.0
    """
    combined = " ".join(titles).lower()
    risk_on = _count_keyword_matches(
        combined, _RISK_ON_KEYWORDS
    )
    risk_off = _count_keyword_matches(
        combined, _RISK_OFF_KEYWORDS
    )

    keyword_score = (risk_on - risk_off) * 0.15
    tone_score = avg_tone * 0.05  # Tone値を弱く反映

    return max(-1.0, min(1.0, keyword_score + tone_score))


def _generate_summary(
    article_count: int,
    avg_tone: float,
    theme: str,
    geo_risk: int,
    base: str,
    quote: str,
    target_date: date,
) -> str:
    """日次サマリーを生成

    Args:
        article_count: 記事数
        avg_tone: 平均Tone
        theme: 支配的テーマ
        geo_risk: 地政学リスクレベル
        base: 基軸通貨
        quote: 決済通貨
        target_date: 対象日

    Returns:
        str: サマリー（200文字以内）
    """
    if article_count == 0:
        return "関連ニュースなし"

    parts: list[str] = []

    # 日付コンテキスト
    wd = target_date.weekday()
    if wd >= 5:
        parts.append("週末")

    # トーン概要
    if avg_tone > 2.0:
        parts.append("楽観的なセンチメント優勢")
    elif avg_tone > 0.5:
        parts.append("やや楽観的")
    elif avg_tone < -3.0:
        parts.append("強い悲観的センチメント")
    elif avg_tone < -1.0:
        parts.append("やや悲観的")
    else:
        parts.append("中立的なセンチメント")

    # テーマ
    if theme != "一般市場動向":
        parts.append(f"主要テーマ: {theme}")

    # 地政学リスク
    if geo_risk >= 3:
        parts.append("地政学リスク高")
    elif geo_risk >= 2:
        parts.append("地政学的緊張")

    # 記事数
    parts.append(f"{base}/{quote}関連{article_count}件")

    summary = "。".join(parts)
    return summary[:200]


def analyze_day(
    symbol: str,
    base: str,
    quote: str,
    target_date: date,
    articles: list[dict],
) -> dict:
    """1日分のニュースを分析

    Args:
        symbol: 通貨ペア
        base: 基軸通貨
        quote: 決済通貨
        target_date: 対象日
        articles: 記事リスト（dict: title, snippet等）

    Returns:
        dict: 分析結果
    """
    if not articles:
        return {
            "date": target_date.isoformat(),
            "article_count": 0,
            "sentiment_score": 0.0,
            "sentiment_confidence": 0.0,
            "macro_bias_score": 0.0,
            "policy_divergence_score": 0.0,
            "risk_appetite_score": 0.0,
            "geopolitical_risk_level": 0,
            "dominant_theme": "",
            "summary": "関連ニュースなし",
        }

    titles = [a.get("title", "") for a in articles]
    n = len(articles)

    # Tone値の集計
    tones: list[float] = []
    for a in articles:
        tone = _extract_tone(a.get("snippet", ""))
        if tone is not None:
            tones.append(tone)

    avg_tone = sum(tones) / len(tones) if tones else 0.0

    # ── センチメントスコア ──
    # GDELT Tone → センチメントへの変換
    # Tone range: typically -10 to +10
    # sentiment range: -1.0 to +1.0
    raw_sentiment = avg_tone / 10.0

    # キーワード補正
    combined = " ".join(titles).lower()
    risk_on = _count_keyword_matches(
        combined, _RISK_ON_KEYWORDS
    )
    risk_off = _count_keyword_matches(
        combined, _RISK_OFF_KEYWORDS
    )
    kw_adj = (risk_on - risk_off) * 0.05

    # base通貨のタカ派度で補正
    # （base通貨がタカ派的→ペア上昇→正のセンチメント）
    base_banks = _CURRENCY_BANK.get(base, [])
    quote_banks = _CURRENCY_BANK.get(quote, [])
    base_hawk = 0
    quote_hawk = 0
    for t in titles:
        tl = t.lower()
        if any(b in tl for b in base_banks):
            base_hawk += _count_keyword_matches(
                tl, _HAWKISH_KEYWORDS
            )
            base_hawk -= _count_keyword_matches(
                tl, _DOVISH_KEYWORDS
            )
        if any(b in tl for b in quote_banks):
            quote_hawk += _count_keyword_matches(
                tl, _HAWKISH_KEYWORDS
            )
            quote_hawk -= _count_keyword_matches(
                tl, _DOVISH_KEYWORDS
            )
    policy_adj = (base_hawk - quote_hawk) * 0.05

    sentiment = max(
        -1.0,
        min(1.0, raw_sentiment + kw_adj + policy_adj),
    )

    # ── 確信度 ──
    # 記事数とTone分散で確信度を算出
    if len(tones) >= 2:
        tone_std = (
            sum((t - avg_tone) ** 2 for t in tones)
            / len(tones)
        ) ** 0.5
        # 低分散 → 高確信度
        consistency = max(0.0, 1.0 - tone_std / 5.0)
    else:
        consistency = 0.3

    # 記事数が多いほど確信度上昇
    count_factor = min(1.0, n / 20.0)
    confidence = max(
        0.0,
        min(1.0, consistency * 0.6 + count_factor * 0.4),
    )

    # ── マクロバイアス ──
    # Tone値ベースのマクロ経済見通し
    macro = max(-1.0, min(1.0, avg_tone / 8.0))

    # ── 政策乖離 ──
    policy_div = _compute_policy_divergence(
        titles, base, quote
    )

    # ── リスク選好 ──
    risk_appetite = _compute_risk_appetite(titles, avg_tone)

    # ── 地政学リスク ──
    geo_risk = _detect_geopolitical_risk(titles)

    # ── テーマ ──
    theme = _detect_dominant_theme(titles)

    # ── サマリー ──
    summary = _generate_summary(
        n, avg_tone, theme, geo_risk,
        base, quote, target_date,
    )

    return {
        "date": target_date.isoformat(),
        "article_count": n,
        "sentiment_score": round(sentiment, 1),
        "sentiment_confidence": round(confidence, 1),
        "macro_bias_score": round(macro, 1),
        "policy_divergence_score": round(policy_div, 1),
        "risk_appetite_score": round(risk_appetite, 1),
        "geopolitical_risk_level": geo_risk,
        "dominant_theme": theme[:100],
        "summary": summary[:200],
    }


# ============================================================
# ニュース読み込み・フィルタリング
# ============================================================


def load_rss_news(
    rss_path: Path,
) -> list[dict]:
    """RSSニュースCSVを読み込み

    Args:
        rss_path: CSVパス

    Returns:
        list[dict]: 記事リスト
    """
    items: list[dict] = []
    with open(rss_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            items.append(row)
    return items


def filter_by_currency(
    items: list[dict],
    currencies: set[str],
) -> list[dict]:
    """通貨でフィルタ

    Args:
        items: 記事リスト
        currencies: 対象通貨セット

    Returns:
        list[dict]: フィルタ済み
    """
    result: list[dict] = []
    for item in items:
        curr_str = item.get("currencies", "[]")
        if any(c in curr_str for c in currencies):
            result.append(item)
    return result


def group_by_date(
    items: list[dict],
    year: int,
) -> dict[date, list[dict]]:
    """日付でグループ化

    Args:
        items: フィルタ済み記事
        year: 対象年

    Returns:
        dict[date, list[dict]]: 日別記事
    """
    groups: dict[date, list[dict]] = defaultdict(list)
    for item in items:
        pub = item.get("published_at", "")
        if not pub:
            continue
        try:
            # ISO 8601 format
            d = date.fromisoformat(pub[:10])
            if d.year == year:
                groups[d].append(item)
        except ValueError:
            continue
    return dict(groups)


def generate_date_range(year: int) -> list[date]:
    """年内の全日付リストを生成

    Args:
        year: 対象年

    Returns:
        list[date]: 日付リスト
    """
    start = date(year, 1, 1)
    end = date(year, 12, 31)
    days: list[date] = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days


# ============================================================
# メイン処理
# ============================================================

_CSV_COLUMNS = [
    "date",
    "article_count",
    "sentiment_score",
    "sentiment_confidence",
    "macro_bias_score",
    "policy_divergence_score",
    "risk_appetite_score",
    "geopolitical_risk_level",
    "dominant_theme",
    "summary",
]


def process_symbol_year(
    symbol: str,
    year: int,
    rss_dir: Path,
    gdelt_dir: Path | None,
    output_dir: Path,
    overwrite: bool = False,
) -> Path | None:
    """シンボル・年のllm_news CSVを生成

    Args:
        symbol: 通貨ペアシンボル
        year: 対象年
        rss_dir: RSSニュースCSVディレクトリ
        gdelt_dir: GDELTニュースディレクトリ（オプション）
        output_dir: 出力ディレクトリ
        overwrite: 上書き

    Returns:
        Path | None: 生成したCSVパス（スキップ時None）
    """
    output_path = output_dir / f"llm_news_{symbol}_{year}.csv"
    if output_path.exists() and not overwrite:
        return None

    base, quote = _SYMBOL_CURRENCIES.get(
        symbol, (symbol[:3], symbol[3:6])
    )
    currencies = {base, quote}

    # ニュース読み込み
    all_items: list[dict] = []

    # RSS
    rss_path = rss_dir / f"news_rss_{year}.csv"
    if rss_path.exists():
        items = load_rss_news(rss_path)
        all_items.extend(items)

    # GDELT（オプション）
    if gdelt_dir:
        gdelt_path = gdelt_dir / f"news_{year}.csv"
        if gdelt_path.exists():
            items = load_rss_news(gdelt_path)
            all_items.extend(items)

    # 通貨フィルタ
    relevant = filter_by_currency(all_items, currencies)
    daily = group_by_date(relevant, year)
    date_range = generate_date_range(year)

    print(
        f"  {symbol}/{year}: "
        f"全{len(all_items):,}件→{len(relevant):,}件 "
        f"({len(daily)}日に記事あり)"
    )

    # 日次分析
    rows: list[dict] = []
    for target_date in date_range:
        day_articles = daily.get(target_date, [])
        result = analyze_day(
            symbol, base, quote,
            target_date, day_articles,
        )
        rows.append(result)

    # CSV出力
    output_dir.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            # session_detailは除外（カラムに含まれない）
            filtered = {
                k: v for k, v in row.items()
                if k in _CSV_COLUMNS
            }
            writer.writerow(filtered)

    return output_path


def main() -> None:
    """メイン処理"""
    parser = argparse.ArgumentParser(
        description="llm_news CSV 決定論的生成",
    )
    parser.add_argument(
        "--symbols",
        type=str,
        required=True,
        help="対象通貨ペア（カンマ区切り）",
    )
    parser.add_argument(
        "--years",
        type=str,
        required=True,
        help="年範囲（例: 2020-2025）",
    )
    parser.add_argument(
        "--rss-dir",
        type=str,
        default="data/fundamental",
        help="RSSニュースCSVディレクトリ",
    )
    parser.add_argument(
        "--gdelt-dir",
        type=str,
        default=None,
        help="GDELTニュースディレクトリ（オプション）",
    )
    parser.add_argument(
        "--output-base",
        type=str,
        default="data",
        help="出力ベースディレクトリ",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="既存ファイルを上書き",
    )
    args = parser.parse_args()

    symbols = [
        s.strip().upper()
        for s in args.symbols.split(",")
    ]

    parts = args.years.split("-")
    year_start = int(parts[0])
    year_end = int(parts[1]) if len(parts) > 1 else year_start
    years = list(range(year_start, year_end + 1))

    rss_dir = Path(args.rss_dir)
    gdelt_dir = Path(args.gdelt_dir) if args.gdelt_dir else None
    output_base = Path(args.output_base)

    print("=== llm_news 決定論的生成 ===")
    print(f"対象: {', '.join(symbols)}")
    print(f"年範囲: {year_start}-{year_end}")
    print()

    total_created = 0
    total_skipped = 0

    for symbol in symbols:
        output_dir = output_base / symbol / "llm_news" / "csv"
        for year in years:
            result = process_symbol_year(
                symbol, year,
                rss_dir, gdelt_dir,
                output_dir,
                args.overwrite,
            )
            if result:
                total_created += 1
            else:
                total_skipped += 1
                print(f"  {symbol}/{year}: スキップ（既存）")

    print()
    print(
        f"=== 完了: {total_created}件生成, "
        f"{total_skipped}件スキップ ==="
    )


if __name__ == "__main__":
    main()
