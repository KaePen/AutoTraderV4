"""ファンダメンタルイベントデータの自動発見・プロバイダ生成ユーティリティ

バックテスト用のイベントCSV/Parquetを自動発見し、
BacktestFundamentalProviderを生成する共通関数を提供する。
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def create_fundamental_provider(
    data_dir: str,
    symbol: str,
    start_year: int,
    end_year: int,
    guard_minutes: int = 30,
) -> any:
    """イベントCSV/Parquetを自動発見してプロバイダを生成

    発見順序（年ごとに独立判定）:
    1. data/{SYMBOL}/events/cache/events_YYYY.parquet
    2. data/{SYMBOL}/events/csv/events_YYYY.csv

    Args:
        data_dir: データルートディレクトリ
        symbol: 通貨ペア（例: USDJPY）
        start_year: 開始年
        end_year: 終了年（含む）
        guard_minutes: 重要指標前の取引停止分数

    Returns:
        BacktestFundamentalProvider | None:
            イベントデータが見つかった場合はプロバイダ、
            なければ None
    """
    from autotrader.adapters.fundamental.backtest_provider import (
        BacktestFundamentalProvider,
    )

    data_base = Path(data_dir)
    event_csvs: list[str] = []
    event_parquets: list[str] = []

    for yr in range(start_year, end_year + 1):
        # 優先1: Parquet
        pq = (
            data_base / symbol / "events" / "cache"
            / f"events_{yr}.parquet"
        )
        if pq.exists():
            event_parquets.append(str(pq))
            continue
        # 優先2: CSV
        csv = (
            data_base / symbol / "events" / "csv"
            / f"events_{yr}.csv"
        )
        if csv.exists():
            event_csvs.append(str(csv))

    if not event_csvs and not event_parquets:
        logger.debug(
            "[FundamentalUtils] %s のイベントデータなし（%s）",
            symbol,
            data_base / symbol / "events",
        )
        return None

    provider = BacktestFundamentalProvider(
        event_guard_minutes=guard_minutes,
    )

    total = 0
    for pq_path in event_parquets:
        total += provider.load_parquet(pq_path)
    for csv_path in event_csvs:
        total += provider.load_csv(csv_path)

    if total == 0:
        logger.warning(
            "[FundamentalUtils] %s: ファイルはあるがイベント0件",
            symbol,
        )
        return None

    logger.info(
        "[FundamentalUtils] %s: %d件ロード"
        "（Parquet %d, CSV %d）",
        symbol,
        total,
        len(event_parquets),
        len(event_csvs),
    )
    return provider
