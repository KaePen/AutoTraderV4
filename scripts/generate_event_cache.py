#!/usr/bin/env python3
"""経済イベントCSVを通貨ペア別Parquetキャッシュに変換

全通貨の経済イベントCSV（events_YYYY.csv）を通貨ペア別に
フィルタリングし、正規化・重複排除済みのParquetファイルを生成する。

使用例:
    # 全年・全通貨ペアを処理
    uv run python scripts/generate_event_cache.py

    # 既存キャッシュを上書き
    uv run python scripts/generate_event_cache.py --force

    # 特定通貨ペアのみ
    uv run python scripts/generate_event_cache.py --symbols USDJPY EURUSD

    # 特定年範囲
    uv run python scripts/generate_event_cache.py --years 2020-2025
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

# プロジェクトルートをパスに追加
try:
    project_root = Path(__file__).parent.parent
except NameError:
    project_root = Path("D:/Projects/AutoTraderV4")
sys.path.insert(0, str(project_root))

from autotrader.adapters.fundamental.normalizer import (
    EconomicEventNormalizer,
)
from autotrader.adapters.fundamental.schemas import (
    EconomicEvent,
    EventSource,
    ImpactLevel,
)

# CSVインパクト文字列→ImpactLevel
_IMPACT_MAP: dict[str, ImpactLevel] = {
    "high": ImpactLevel.HIGH,
    "medium": ImpactLevel.MEDIUM,
    "low": ImpactLevel.LOW,
}


def load_symbol_list(config_path: Path) -> list[str]:
    """symbol_presets.yaml からシンボル一覧を取得

    Args:
        config_path: symbol_presets.yaml のパス

    Returns:
        list[str]: シンボル名リスト
    """
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    symbols = list(data.get("symbols", {}).keys())
    return symbols


def parse_events_csv(csv_path: Path) -> list[EconomicEvent]:
    """経済イベントCSVをEconomicEventリストに変換

    Args:
        csv_path: CSVファイルパス

    Returns:
        list[EconomicEvent]: パース済みイベントリスト
    """
    df = pd.read_csv(csv_path, encoding="utf-8")
    events: list[EconomicEvent] = []
    fetched_at = datetime.now(timezone.utc)

    for _, row in df.iterrows():
        event_time_str = str(row.get("event_time", ""))
        if not event_time_str or event_time_str == "nan":
            continue

        try:
            event_time = datetime.fromisoformat(event_time_str)
            if event_time.tzinfo is None:
                event_time = event_time.replace(
                    tzinfo=timezone.utc
                )
        except ValueError:
            continue

        currency = str(row.get("currency", "")).upper()
        if not currency or currency == "NAN":
            continue

        event_name = str(row.get("event_name", ""))
        if not event_name or event_name == "nan":
            continue

        impact_str = str(row.get("impact", "low")).lower()
        impact = _IMPACT_MAP.get(impact_str, ImpactLevel.LOW)

        def _parse_float(val: object) -> float | None:
            """値をfloatに変換"""
            if pd.isna(val):
                return None
            try:
                return float(val)
            except (ValueError, TypeError):
                return None

        events.append(
            EconomicEvent(
                event_id=str(
                    row.get("event_id", f"bt_{hash(event_name)}")
                ),
                event_time=event_time,
                currency=currency,
                event_name=event_name,
                impact=impact,
                source=EventSource.MT5,
                fetched_at=fetched_at,
                actual=_parse_float(row.get("actual")),
                forecast=_parse_float(row.get("forecast")),
                previous=_parse_float(row.get("previous")),
            )
        )
    return events


def events_to_dataframe(
    events: list[EconomicEvent],
) -> pd.DataFrame:
    """EconomicEventリストをDataFrameに変換

    Args:
        events: イベントリスト

    Returns:
        pd.DataFrame: 変換済みDataFrame
    """
    records = []
    for ev in events:
        records.append({
            "event_id": ev.event_id,
            "event_time": ev.event_time.isoformat(),
            "currency": ev.currency,
            "event_name": ev.event_name,
            "impact": ev.impact.value,
            "actual": ev.actual,
            "forecast": ev.forecast,
            "previous": ev.previous,
        })
    return pd.DataFrame(records)


def get_symbol_currencies(symbol: str) -> set[str]:
    """シンボル名からbase/quote通貨を抽出

    Args:
        symbol: 通貨ペア名（例: USDJPY）

    Returns:
        set[str]: 通貨コードのセット
    """
    if len(symbol) >= 6:
        return {symbol[:3].upper(), symbol[3:6].upper()}
    return set()


def main() -> None:
    """メイン処理"""
    parser = argparse.ArgumentParser(
        description="経済イベントCSVを通貨ペア別Parquetに変換",
    )
    parser.add_argument(
        "--data-dir",
        default="data",
        help="データディレクトリ（デフォルト: data）",
    )
    parser.add_argument(
        "--config",
        default="config/symbol_presets.yaml",
        help="シンボル設定ファイル",
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=None,
        help="処理対象シンボル（指定なしで全シンボル）",
    )
    parser.add_argument(
        "--years",
        default=None,
        help="年範囲（例: 2010-2025）",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="既存キャッシュを上書き",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    config_path = Path(args.config)

    # シンボル一覧取得
    if args.symbols:
        symbols = [s.upper() for s in args.symbols]
    else:
        symbols = load_symbol_list(config_path)

    # 年範囲決定
    events_dir = data_dir / "fundamental" / "events"
    if not events_dir.exists():
        # フォールバック: data/fundamental/
        events_dir = data_dir / "fundamental"

    if args.years:
        parts = args.years.split("-")
        start_year = int(parts[0])
        end_year = int(parts[1]) if len(parts) > 1 else start_year
        years = list(range(start_year, end_year + 1))
    else:
        # 存在するCSVファイルから年を自動検出
        years = []
        for csv_file in sorted(events_dir.glob("events_*.csv")):
            stem = csv_file.stem
            try:
                year = int(stem.split("_")[1])
                years.append(year)
            except (IndexError, ValueError):
                continue

    if not years:
        sys.stderr.write(
            f"エラー: {events_dir} に events_YYYY.csv が"
            "見つかりません\n"
        )
        sys.exit(1)

    sys.stdout.write(
        f"対象シンボル: {', '.join(symbols)}\n"
    )
    sys.stdout.write(
        f"対象年: {years[0]}-{years[-1]} ({len(years)}年)\n"
    )
    sys.stdout.write(f"上書きモード: {args.force}\n\n")

    normalizer = EconomicEventNormalizer()
    total_files = 0
    total_events = 0
    skipped_files = 0

    for year in years:
        csv_path = events_dir / f"events_{year}.csv"
        if not csv_path.exists():
            sys.stderr.write(
                f"  警告: {csv_path} が見つかりません\n"
            )
            continue

        # CSV読み込み
        all_events = parse_events_csv(csv_path)
        sys.stdout.write(
            f"[{year}] CSV読込: {len(all_events)}件\n"
        )

        for symbol in symbols:
            # 出力先ディレクトリ
            out_dir = data_dir / symbol / "events"
            out_path = out_dir / f"events_{year}.parquet"

            # キャッシュ存在チェック
            if out_path.exists() and not args.force:
                skipped_files += 1
                continue

            # 通貨フィルタリング
            currencies = get_symbol_currencies(symbol)
            if not currencies:
                continue

            # normalizer のフィルタリングを使用
            filtered = normalizer.filter_by_symbol(
                all_events, symbol,
            )

            # 重複排除
            deduped = normalizer.deduplicate(filtered)

            if not deduped:
                continue

            # DataFrame変換・Parquet書き出し
            df = events_to_dataframe(deduped)
            out_dir.mkdir(parents=True, exist_ok=True)
            df.to_parquet(out_path, engine="pyarrow", index=False)

            total_files += 1
            total_events += len(deduped)
            file_size = out_path.stat().st_size
            sys.stdout.write(
                f"  {symbol}: {len(deduped)}件 → "
                f"{out_path} ({file_size:,} bytes)\n"
            )

    sys.stdout.write(f"\n--- サマリー ---\n")
    sys.stdout.write(f"生成ファイル数: {total_files}\n")
    sys.stdout.write(f"総イベント数: {total_events:,}\n")
    sys.stdout.write(f"スキップ: {skipped_files}\n")


if __name__ == "__main__":
    main()
