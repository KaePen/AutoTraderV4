"""ファンダメンタルコンテキスト事前生成スクリプト

サブコマンドで日次イベント / 日次ニュース を切り替え可能。
イベント生成は決定論的ヒューリスティック（LLM不要・高速）。

使用方法:
  # 日次イベントCSV生成（決定論的・Ollama不要）
  python scripts/generate_fundamental_llm.py events \
      --symbol USDJPY --years 2020-2024

  # 日次ニュースCSV生成（news_rss_*.csv のみ使用）
  python scripts/generate_fundamental_llm.py news \
      --symbol USDJPY --years 2020-2024

  # GDELT生データも併用する場合（重複排除あり）
  python scripts/generate_fundamental_llm.py news \
      --symbol USDJPY --years 2020-2024 \
      --news-dir data/fundamental/news

  # 両方生成
  python scripts/generate_fundamental_llm.py all \
      --symbol USDJPY --years 2020-2024

出力先:
  events: data/fundamental/llm_events_SYMBOL_YYYY.csv
  news:   data/fundamental/llm_news_SYMBOL_YYYY.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

# プロジェクトルートをパスに追加
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

try:
    import ollama as _ollama_check  # noqa: F401
except ImportError:
    _ollama_check = None  # type: ignore[assignment]

import logging

logger = logging.getLogger(__name__)

from autotrader.adapters.fundamental.news_csv_writer import (
    read_news_csv,
)
from autotrader.adapters.fundamental.news_schemas import (
    NewsItem,
)
from autotrader.adapters.fundamental.schemas import (
    EconomicEvent,
    EventSource,
    ImpactLevel,
)
from autotrader.config.llm_settings import OllamaSettings

# デフォルト入力ディレクトリ
_DEFAULT_INPUT_DIR = "data/fundamental"
# GDELT生データはnews_rss_*.csvに統合済みのため不要
_DEFAULT_NEWS_DIR = None


def parse_args() -> argparse.Namespace:
    """サブコマンド対応引数パーサー

    Returns:
        argparse.Namespace: パース済み引数
    """
    parser = argparse.ArgumentParser(
        description=(
            "ファンダメンタルコンテキスト事前生成"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # 共通引数を定義する親パーサー
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--symbol",
        nargs="+",
        default=["USDJPY"],
        help="対象シンボル（複数指定可）",
    )
    year_group = parent.add_mutually_exclusive_group(
        required=True
    )
    year_group.add_argument(
        "--year",
        type=int,
        nargs="+",
        metavar="YEAR",
        help="年指定（スペース区切り）: 2024 / 2020 2021",
    )
    year_group.add_argument(
        "--years",
        type=str,
        help="年範囲指定（例: 2010-2025）",
    )
    parent.add_argument(
        "--input-dir",
        default=_DEFAULT_INPUT_DIR,
        help="入力CSVディレクトリ",
    )
    parent.add_argument(
        "--output-dir",
        default=_DEFAULT_INPUT_DIR,
        help="出力CSVディレクトリ",
    )
    parent.add_argument(
        "--model",
        default="erwan2/DeepSeek-R1-Distill-Qwen-14B",
        help="使用するOllamaモデル名（news用）",
    )
    parent.add_argument(
        "--host",
        default="http://localhost:11434",
        help="OllamaホストURL（news用）",
    )
    parent.add_argument(
        "--overwrite",
        action="store_true",
        help="既存ファイルを上書き",
    )
    parent.add_argument(
        "--temperature",
        type=float,
        default=0.1,
        help="LLM温度パラメータ（news用）",
    )
    parent.add_argument(
        "--dry-run",
        action="store_true",
        help="処理件数確認のみ（LLM呼び出しなし）",
    )

    subparsers = parser.add_subparsers(
        dest="command", required=True
    )

    # events サブコマンド
    subparsers.add_parser(
        "events",
        parents=[parent],
        help="日次イベントCSV生成（決定論的）",
    )

    # news サブコマンド
    news_parser = subparsers.add_parser(
        "news",
        parents=[parent],
        help="日次ニュースLLM CSV生成",
    )
    news_parser.add_argument(
        "--news-dir",
        default=_DEFAULT_NEWS_DIR,
        help=(
            "GDELT生データCSVディレクトリ（news/）。"
            "デフォルト無効（news_rss_*.csvに統合済み）"
        ),
    )
    news_parser.add_argument(
        "--rss-dir",
        default=_DEFAULT_INPUT_DIR,
        help="RSSニュースCSVディレクトリ（news_rss_YYYY.csv）",
    )

    # all サブコマンド
    all_parser = subparsers.add_parser(
        "all",
        parents=[parent],
        help="イベント + ニュース両方生成",
    )
    all_parser.add_argument(
        "--news-dir",
        default=_DEFAULT_NEWS_DIR,
        help=(
            "GDELT生データCSVディレクトリ（news/）。"
            "デフォルト無効（news_rss_*.csvに統合済み）"
        ),
    )
    all_parser.add_argument(
        "--rss-dir",
        default=_DEFAULT_INPUT_DIR,
        help="RSSニュースCSVディレクトリ（news_rss_YYYY.csv）",
    )

    return parser.parse_args()


def parse_year_range(years_str: str) -> list[int]:
    """年範囲文字列をパース

    Args:
        years_str: "2010-2025" 形式の文字列

    Returns:
        list[int]: 年のリスト

    Raises:
        ValueError: フォーマット不正時
    """
    if "-" not in years_str:
        raise ValueError(
            f"年範囲フォーマットが不正: {years_str}. "
            "例: 2010-2025"
        )
    parts = years_str.split("-")
    if len(parts) != 2:
        raise ValueError(
            f"年範囲フォーマットが不正: {years_str}"
        )
    start, end = int(parts[0]), int(parts[1])
    if start > end:
        raise ValueError(
            f"開始年 > 終了年: {start} > {end}"
        )
    return list(range(start, end + 1))


def load_events_csv(
    csv_path: Path,
) -> list[EconomicEvent]:
    """経済イベントCSVを読み込み

    Args:
        csv_path: CSVファイルパス

    Returns:
        list[EconomicEvent]: 読み込んだイベントリスト
    """
    if not csv_path.exists():
        logger.warning(f"CSVが見つかりません: {csv_path}")
        return []

    events: list[EconomicEvent] = []
    fetched_at = datetime.now(timezone.utc)

    def _parse_float(val: str) -> float | None:
        """文字列をfloatに変換"""
        if not val or val.strip() == "":
            return None
        try:
            return float(val)
        except ValueError:
            return None

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                event_time_str = row.get("event_time", "")
                if not event_time_str:
                    continue
                event_time = datetime.fromisoformat(
                    event_time_str
                )
                if event_time.tzinfo is None:
                    event_time = event_time.replace(
                        tzinfo=timezone.utc
                    )

                currency = row.get("currency", "").upper()
                event_name = row.get("event_name", "")
                if not currency or not event_name:
                    continue

                impact_str = row.get(
                    "impact", "low"
                ).lower()
                impact = {
                    "high": ImpactLevel.HIGH,
                    "medium": ImpactLevel.MEDIUM,
                    "low": ImpactLevel.LOW,
                }.get(impact_str, ImpactLevel.LOW)

                events.append(
                    EconomicEvent(
                        event_id=row.get(
                            "event_id",
                            f"bt_{hash(event_name)}",
                        ),
                        event_time=event_time,
                        currency=currency,
                        event_name=event_name,
                        impact=impact,
                        source=EventSource.MT5,
                        fetched_at=fetched_at,
                        actual=_parse_float(
                            row.get("actual", "")
                        ),
                        forecast=_parse_float(
                            row.get("forecast", "")
                        ),
                        previous=_parse_float(
                            row.get("previous", "")
                        ),
                    )
                )
            except Exception as e:
                logger.debug(f"行スキップ: {e}")
                continue

    logger.info(
        f"読込完了: {len(events)}件 ({csv_path.name})"
    )
    return events


def load_all_news(
    rss_dir: Path | None,
    news_dir: Path | None,
    year: int,
) -> list[NewsItem]:
    """RSSとGDELTのニュースCSVを読み込み

    news_rss_*.csv を優先使用する。
    news/news_*.csv（GDELT生データ）はnews_rss_*.csvに
    統合済みのため、デフォルトでは読み込まない。
    --news-dir を明示指定した場合のみGDELTも読み込み、
    news_id による重複排除を行う。

    Args:
        rss_dir: news_rss_YYYY.csv のディレクトリ
        news_dir: news/news_YYYY.csv のディレクトリ
        year: 対象年

    Returns:
        list[NewsItem]: ニュースリスト（重複排除済み）
    """
    items: list[NewsItem] = []
    seen_ids: set[str] = set()

    # RSS ニュース（優先）
    if rss_dir:
        rss_path = Path(rss_dir) / f"news_rss_{year}.csv"
        if rss_path.exists():
            rss_items = read_news_csv(rss_path)
            logger.info(
                f"[RSS] {year}: {len(rss_items)}件読込"
            )
            for item in rss_items:
                seen_ids.add(item.news_id)
            items.extend(rss_items)
        else:
            logger.warning(f"[RSS] 未存在: {rss_path}")

    # GDELT ニュース（明示指定時のみ、重複排除）
    if news_dir:
        news_path = Path(news_dir) / f"news_{year}.csv"
        if news_path.exists():
            news_items = read_news_csv(news_path)
            # RSS既読のIDを除外
            new_items = [
                item
                for item in news_items
                if item.news_id not in seen_ids
            ]
            logger.info(
                f"[GDELT] {year}: {len(news_items)}件読込"
                f"→{len(new_items)}件追加"
                f"（{len(news_items) - len(new_items)}"
                f"件はRSSと重複）"
            )
            items.extend(new_items)
        else:
            logger.warning(f"[GDELT] 未存在: {news_path}")

    return items


def check_ollama_available(
    host: str, model: str
) -> bool:
    """Ollamaが利用可能か確認

    Args:
        host: OllamaホストURL
        model: 使用モデル名

    Returns:
        bool: 利用可能ならTrue
    """
    try:
        if _ollama_check is None:
            raise ImportError(
                "ollama パッケージが未インストール"
            )
        import ollama  # noqa: PLC0415

        client = ollama.Client(host=host)
        response = client.list()
        model_names = [m.model for m in response.models]
        base_model = model.split(":")[0]
        available = any(
            m.startswith(base_model) for m in model_names
        )
        if not available:
            logger.error(
                f"モデル未インストール: {model}\n"
                f"利用可能モデル: {model_names}\n"
                f"インストール: ollama pull {model}"
            )
        return available
    except Exception as e:
        logger.error(
            f"Ollama接続失敗 ({host}): {e}\n"
            "Ollamaが起動しているか確認: ollama serve"
        )
        return False


def _resolve_years(args: argparse.Namespace) -> list[int]:
    """引数から年リストを解決

    Args:
        args: パース済み引数

    Returns:
        list[int]: 年のリスト
    """
    if args.year:
        return args.year
    return parse_year_range(args.years)


def _make_settings(
    args: argparse.Namespace,
) -> OllamaSettings:
    """引数からOllama設定を生成

    Args:
        args: パース済み引数

    Returns:
        OllamaSettings: 設定オブジェクト
    """
    return OllamaSettings(
        host=args.host,
        model=args.model,
        temperature=args.temperature,
    )


def run_events(args: argparse.Namespace) -> int:
    """日次イベントCSV生成（決定論的）

    Args:
        args: パース済み引数

    Returns:
        int: 終了コード
    """
    from autotrader.adapters.fundamental.deterministic_event_analyzer import (  # noqa: PLC0415, E501
        DeterministicEventAnalyzer,
    )

    years = _resolve_years(args)
    symbols = args.symbol
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)

    logger.info(
        f"[Events] シンボル: {symbols}\n"
        f"  年: {years[0]}〜{years[-1]} ({len(years)}年)\n"
        f"  モード: 決定論的（LLM不使用）"
    )

    if args.dry_run:
        total_days = len(symbols) * len(years) * 365
        logger.info(
            f"[DryRun] 処理予定: {total_days}日分\n"
            f"  {len(symbols)}シンボル × "
            f"{len(years)}年 × 365日"
        )
        for year in years:
            csv_path = input_dir / f"events_{year}.csv"
            status = (
                "OK" if csv_path.exists() else "未存在"
            )
            logger.info(f"  {csv_path}: {status}")
        return 0

    generator = DeterministicEventAnalyzer()

    error_count = 0
    success_count = 0

    for symbol in symbols:
        for year in years:
            csv_path = input_dir / f"events_{year}.csv"
            events = load_events_csv(csv_path)
            if not events:
                logger.warning(
                    f"[{symbol}/{year}] "
                    f"イベントデータなし"
                )
                error_count += 1
                continue

            try:
                output_path = (
                    generator.generate_for_symbol_year(
                        symbol=symbol,
                        year=year,
                        events=events,
                        output_dir=output_dir,
                        overwrite=args.overwrite,
                    )
                )
                logger.info(
                    f"[{symbol}/{year}] 完了: {output_path}"
                )
                success_count += 1
            except Exception as e:
                logger.error(
                    f"[{symbol}/{year}] エラー: {e}"
                )
                error_count += 1

    logger.info(
        f"\n[Events] {success_count}件成功, "
        f"{error_count}件エラー"
    )
    return 0 if error_count == 0 else 1


def run_news(args: argparse.Namespace) -> int:
    """日次ニュースLLM CSV生成

    Args:
        args: パース済み引数

    Returns:
        int: 終了コード
    """
    from autotrader.adapters.fundamental.llm_news_generator import (  # noqa: PLC0415
        LLMNewsGenerator,
    )

    years = _resolve_years(args)
    symbols = args.symbol
    output_dir = Path(args.output_dir)
    rss_dir = (
        Path(args.rss_dir) if args.rss_dir else None
    )
    news_dir = (
        Path(args.news_dir) if args.news_dir else None
    )

    logger.info(
        f"[News] シンボル: {symbols}\n"
        f"  年: {years[0]}〜{years[-1]} ({len(years)}年)\n"
        f"  モデル: {args.model}\n"
        f"  RSS: {rss_dir or '（なし）'}\n"
        f"  GDELT: {news_dir or '（なし）'}"
    )

    if args.dry_run:
        total_days = len(symbols) * len(years) * 365
        logger.info(
            f"[DryRun] 処理予定: {total_days}日分"
        )
        for year in years:
            rss_path = (
                rss_dir / f"news_rss_{year}.csv"
                if rss_dir
                else None
            )
            news_path = (
                news_dir / f"news_{year}.csv"
                if news_dir
                else None
            )
            rss_status = (
                "OK"
                if rss_path and rss_path.exists()
                else "未存在"
            )
            news_status = (
                "OK"
                if news_path and news_path.exists()
                else "未存在"
            )
            logger.info(
                f"  {year}: RSS={rss_status} "
                f"GDELT={news_status}"
            )
        return 0

    if not check_ollama_available(args.host, args.model):
        return 1

    generator = LLMNewsGenerator(
        ollama_settings=_make_settings(args)
    )

    error_count = 0
    success_count = 0

    for symbol in symbols:
        for year in years:
            news_items = load_all_news(
                rss_dir, news_dir, year
            )
            if not news_items:
                logger.warning(
                    f"[{symbol}/{year}] ニュースデータなし"
                )
                error_count += 1
                continue

            try:
                output_path = (
                    generator.generate_for_symbol_year(
                        symbol=symbol,
                        year=year,
                        news_items=news_items,
                        output_dir=output_dir,
                        overwrite=args.overwrite,
                    )
                )
                logger.info(
                    f"[{symbol}/{year}] 完了: {output_path}"
                )
                success_count += 1
            except Exception as e:
                logger.error(
                    f"[{symbol}/{year}] エラー: {e}"
                )
                error_count += 1

    logger.info(
        f"\n[News] {success_count}件成功, "
        f"{error_count}件エラー"
    )
    return 0 if error_count == 0 else 1


def main() -> int:
    """メイン処理

    Returns:
        int: 終了コード（0=成功、1=エラー）
    """
    args = parse_args()

    try:
        if args.command == "events":
            return run_events(args)
        elif args.command == "news":
            return run_news(args)
        elif args.command == "all":
            code = run_events(args)
            if code != 0:
                return code
            return run_news(args)
        else:
            logger.error(
                f"不明なコマンド: {args.command}"
            )
            return 1
    except ValueError as e:
        logger.error(str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
