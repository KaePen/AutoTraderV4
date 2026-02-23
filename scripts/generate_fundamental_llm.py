"""LLMによるファンダメンタルコンテキスト事前生成スクリプト

事前収集した経済指標CSVをOllamaで月次バッチ分析し、
バックテスト用のLLMコンテキストCSVを生成する。

前提:
  data/fundamental/events_YYYY.csv が存在すること
  （collect_fundamental_data.py で収集済み）

使用方法:
  python scripts/generate_fundamental_llm.py \\
      --symbol USDJPY --year 2024

  python scripts/generate_fundamental_llm.py \\
      --symbol USDJPY --year 2020 2021 2022

  python scripts/generate_fundamental_llm.py \\
      --symbol USDJPY --years 2010-2025

  python scripts/generate_fundamental_llm.py \\
      --symbol USDJPY EURUSD --years 2010-2025 \\
      --model qwen3:14b --overwrite

出力先: data/fundamental/llm_context_SYMBOL_YYYY.csv
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

from loguru import logger

from autotrader.adapters.fundamental.llm_context_generator import (
    LLMContextGenerator,
)
from autotrader.adapters.fundamental.news_csv_writer import (
    read_news_csv,
)
from autotrader.adapters.fundamental.news_schemas import NewsItem
from autotrader.adapters.fundamental.schemas import (
    EconomicEvent,
    EventSource,
    ImpactLevel,
)
from autotrader.config.llm_settings import OllamaSettings

# デフォルト入力ディレクトリ
_DEFAULT_INPUT_DIR = "data/fundamental"

# CSVカラム定義
_EVENT_CSV_COLUMNS = [
    "event_id", "event_time", "currency", "event_name",
    "impact", "actual", "forecast", "previous",
]


def parse_args() -> argparse.Namespace:
    """コマンドライン引数をパース

    Returns:
        argparse.Namespace: パース済み引数
    """
    parser = argparse.ArgumentParser(
        description="LLMによるファンダメンタルコンテキスト事前生成",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
例:
  # USDJPY 2024年を生成
  python scripts/generate_fundamental_llm.py --symbol USDJPY --year 2024

  # USDJPY 複数年を指定（スペース区切り）
  python scripts/generate_fundamental_llm.py --symbol USDJPY --year 2020 2021 2022

  # USDJPY 2010-2025年を連続で生成
  python scripts/generate_fundamental_llm.py --symbol USDJPY --years 2010-2025

  # 複数シンボル・モデル指定
  python scripts/generate_fundamental_llm.py \\
      --symbol USDJPY EURUSD --years 2010-2025 \\
      --model qwen3:14b --overwrite
        """,
    )
    parser.add_argument(
        "--symbol",
        nargs="+",
        default=["USDJPY"],
        help="対象シンボル（複数指定可）",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--year",
        type=int,
        nargs="+",
        metavar="YEAR",
        help=(
            "年指定（1つまたは複数スペース区切り）: "
            "2024 / 2020 2021 2022"
        ),
    )
    group.add_argument(
        "--years",
        type=str,
        help="年範囲指定（例: 2010-2025）",
    )
    parser.add_argument(
        "--input-dir",
        default=_DEFAULT_INPUT_DIR,
        help="入力CSVディレクトリ（events_YYYY.csvを含む）",
    )
    parser.add_argument(
        "--output-dir",
        default=_DEFAULT_INPUT_DIR,
        help="出力CSVディレクトリ",
    )
    parser.add_argument(
        "--model",
        default="qwen3:14b",
        help="使用するOllamaモデル名",
    )
    parser.add_argument(
        "--host",
        default="http://localhost:11434",
        help="OllamaホストURL",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="既存ファイルを上書き",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.1,
        help="LLM温度パラメータ",
    )
    parser.add_argument(
        "--news-dir",
        default=None,
        help=(
            "ニュースCSVディレクトリ（news_YYYY.csvを含む）。"
            "指定するとプロンプトにニュース見出しを追加"
        ),
    )
    parser.add_argument(
        "--news-prefix",
        default="news",
        help=(
            "ニュースCSVファイル名のプレフィックス"
            "（デフォルト: news → news_YYYY.csv）"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="実際にLLMを呼び出さずに処理件数を確認",
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

                impact_str = row.get("impact", "low").lower()
                impact = {
                    "high": ImpactLevel.HIGH,
                    "medium": ImpactLevel.MEDIUM,
                    "low": ImpactLevel.LOW,
                }.get(impact_str, ImpactLevel.LOW)

                def parse_float(
                    val: str,
                ) -> float | None:
                    """文字列をfloatに変換"""
                    if not val or val.strip() == "":
                        return None
                    try:
                        return float(val)
                    except ValueError:
                        return None

                events.append(EconomicEvent(
                    event_id=row.get(
                        "event_id", f"bt_{hash(event_name)}"
                    ),
                    event_time=event_time,
                    currency=currency,
                    event_name=event_name,
                    impact=impact,
                    source=EventSource.MT5,
                    fetched_at=fetched_at,
                    actual=parse_float(row.get("actual", "")),
                    forecast=parse_float(row.get("forecast", "")),
                    previous=parse_float(row.get("previous", "")),
                ))
            except Exception as e:
                logger.debug(f"行スキップ: {e}")
                continue

    logger.info(f"読込完了: {len(events)}件 ({csv_path.name})")
    return events


def check_ollama_available(host: str, model: str) -> bool:
    """Ollamaが利用可能か確認

    Args:
        host: OllamaホストURL
        model: 使用モデル名

    Returns:
        bool: 利用可能ならTrue
    """
    try:
        if _ollama_check is None:
            raise ImportError("ollama パッケージが未インストール")
        import ollama  # noqa: PLC0415
        client = ollama.Client(host=host)
        response = client.list()
        # ListResponse の models 属性は Model オブジェクトのリスト
        model_names = [m.model for m in response.models]
        # バージョンタグなしで一致確認
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
            "Ollamaが起動しているか確認してください: ollama serve"
        )
        return False


def main() -> int:
    """メイン処理

    Returns:
        int: 終了コード（0=成功、1=エラー）
    """
    args = parse_args()

    # 年リストを構築
    if args.year:
        years = args.year  # nargs="+" なので既にリスト
    else:
        try:
            years = parse_year_range(args.years)
        except ValueError as e:
            logger.error(str(e))
            return 1

    symbols = args.symbol
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    news_dir = Path(args.news_dir) if args.news_dir else None
    news_prefix = args.news_prefix

    logger.info(
        f"対象シンボル: {symbols}\n"
        f"対象年: {years[0]}〜{years[-1]} ({len(years)}年)\n"
        f"モデル: {args.model}\n"
        f"入力ディレクトリ: {input_dir}\n"
        f"出力ディレクトリ: {output_dir}\n"
        f"ニュースディレクトリ: {news_dir or '（なし）'}"
    )

    # dry-run 処理件数確認
    if args.dry_run:
        total_months = len(symbols) * len(years) * 12
        logger.info(
            f"[DryRun] 処理予定: {total_months}ヶ月分の分析\n"
            f"  {len(symbols)}シンボル × {len(years)}年 × 12ヶ月"
        )
        for year in years:
            csv_path = input_dir / f"events_{year}.csv"
            status = "✓" if csv_path.exists() else "✗ 未存在"
            news_path = (
                news_dir / f"{news_prefix}_{year}.csv"
                if news_dir else None
            )
            news_status = (
                f" | ニュース: {'✓' if news_path and news_path.exists() else '✗'}"
                if news_dir else ""
            )
            logger.info(
                f"  {csv_path}: {status}{news_status}"
            )
        return 0

    # Ollama接続確認
    if not check_ollama_available(args.host, args.model):
        return 1

    # LLMコンテキスト生成器を初期化
    settings = OllamaSettings(
        host=args.host,
        model=args.model,
        temperature=args.temperature,
    )
    generator = LLMContextGenerator(ollama_settings=settings)

    error_count = 0
    success_count = 0

    for symbol in symbols:
        for year in years:
            # 入力CSVを読み込み
            csv_path = input_dir / f"events_{year}.csv"
            events = load_events_csv(csv_path)

            if not events:
                logger.warning(
                    f"[{symbol}/{year}] イベントデータなし: "
                    f"{csv_path}"
                )
                error_count += 1
                continue

            # ニュースCSVを読み込み（指定時のみ）
            news_items: list[NewsItem] | None = None
            if news_dir:
                news_path = news_dir / f"{news_prefix}_{year}.csv"
                if news_path.exists():
                    news_items = read_news_csv(news_path)
                    logger.info(
                        f"[{symbol}/{year}] ニュース"
                        f"{len(news_items)}件読込"
                    )
                else:
                    logger.warning(
                        f"[{symbol}/{year}] ニュースCSV"
                        f"が見つかりません: {news_path}"
                    )

            # LLMコンテキスト生成
            try:
                output_path = generator.generate_for_symbol_year(
                    symbol=symbol,
                    year=year,
                    events=events,
                    output_dir=output_dir,
                    overwrite=args.overwrite,
                    news_items=news_items,
                )
                logger.info(
                    f"[{symbol}/{year}] 完了: {output_path}"
                )
                success_count += 1
            except ValueError as e:
                logger.error(
                    f"[{symbol}/{year}] エラー: {e}"
                )
                error_count += 1
            except Exception as e:
                logger.error(
                    f"[{symbol}/{year}] 予期しないエラー: {e}"
                )
                error_count += 1

    logger.info(
        f"\n完了: {success_count}件成功, {error_count}件エラー"
    )
    return 0 if error_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
