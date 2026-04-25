"""バックテスト用データ前処理パイプライン

OHLCV単一CSV → インジケータ事前計算 → 日別Parquetキャッシュ
ティック月別CSV → 日別Parquetキャッシュ

Usage:
    uv run python -m autotrader.backtest.data_pipeline prepare \\
        --symbol USDJPY --start 2020 --end 2025

    uv run python -m autotrader.backtest.data_pipeline prepare \\
        --symbol USDJPY --timeframes M15 H1 --force
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from autotrader.core.enums import Timeframe

logger = logging.getLogger(__name__)

# バックテストで使用する主要時間足
DEFAULT_TIMEFRAMES = ["M15", "H1", "H4", "D1"]


def _get_data_dir() -> Path:
    from autotrader.config.paths import get_data_dir

    return Path(get_data_dir())


def _tf_enum(name: str) -> Timeframe:
    """時間足名 → Timeframe enum"""
    return Timeframe(name)


# ============================================================
# manifest.json 管理
# ============================================================

def _manifest_path(symbol: str, data_dir: Path) -> Path:
    return data_dir / symbol / "daily_cache" / "manifest.json"


def _load_manifest(symbol: str, data_dir: Path) -> dict:
    path = _manifest_path(symbol, data_dir)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _save_manifest(symbol: str, data_dir: Path, manifest: dict) -> None:
    path = _manifest_path(symbol, data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, indent=2, default=str),
        encoding="utf-8",
    )


def _config_hash(timeframes: list[str]) -> str:
    """設定のハッシュ（キャッシュ無効化用）"""
    key = f"tfs={'|'.join(sorted(timeframes))}"
    return hashlib.md5(key.encode()).hexdigest()[:8]


# ============================================================
# Step 1: OHLCV → インジケータ計算 → 単一Parquet
# ============================================================

def _load_ohlcv_csv(
    symbol: str,
    tf_name: str,
    data_dir: Path,
) -> pd.DataFrame | None:
    """OHLCV単一CSVを読み込み"""
    from autotrader.backtest.data_loader import DataLoader

    csv_dir = data_dir / symbol / "chart" / "csv"
    if not csv_dir.exists():
        # chart直下にもCSVがある場合（旧形式）
        csv_dir = data_dir / symbol / "chart"
        if not csv_dir.exists():
            logger.warning(
                "[Pipeline] CSVディレクトリなし: %s", csv_dir
            )
            return None

    # パターンマッチ: {SYMBOL}_{TF}_*.csv
    tf_csv = tf_name
    if tf_name == "D1":
        tf_csv = "Daily"
    pattern = f"{symbol}_{tf_csv}_*.csv"
    files = sorted(csv_dir.glob(pattern))
    if not files:
        # csv サブディレクトリも検索
        alt_dir = data_dir / symbol / "chart" / "csv"
        if alt_dir.exists():
            files = sorted(alt_dir.glob(pattern))

    if not files:
        logger.warning(
            "[Pipeline] %s %s: CSVファイルなし (%s)",
            symbol, tf_name, pattern,
        )
        return None

    # 単一ファイル想定（最新を使用）
    csv_path = files[-1]
    logger.info(
        "[Pipeline] CSV読み込み: %s (%s)",
        csv_path.name,
        f"{csv_path.stat().st_size / 1024 / 1024:.1f}MB",
    )

    df = DataLoader.load_mt5_csv(csv_path)
    if df is not None and not df.empty:
        if "time" in df.columns and not isinstance(
            df.index, pd.DatetimeIndex
        ):
            df = df.set_index("time")
        df.index.name = "time"
    return df


def compute_indicators(
    symbol: str,
    tf_name: str,
    data_dir: Path,
    force: bool = False,
) -> pd.DataFrame | None:
    """OHLCV CSV → インジケータ計算 → DataFrame返却

    PrecomputeEngine のキャッシュ機構を活用。
    """
    from autotrader.calculator.precompute import PrecomputeEngine

    df = _load_ohlcv_csv(symbol, tf_name, data_dir)
    if df is None or df.empty:
        return None

    engine = PrecomputeEngine()
    tf_enum = _tf_enum(tf_name)

    result = engine.precompute(
        df, symbol, tf_enum, use_cache=not force,
    )
    logger.info(
        "[Pipeline] %s %s: インジケータ計算完了 "
        "(%d行 × %d列)",
        symbol, tf_name, len(result), len(result.columns),
    )
    return result


# ============================================================
# Step 2: インジケータ付きDataFrame → 日別Parquetキャッシュ
# ============================================================

def split_to_daily_parquet(
    df: pd.DataFrame,
    symbol: str,
    tf_name: str,
    data_dir: Path,
    force: bool = False,
) -> int:
    """DataFrame を日別 Parquet ファイルに分割保存

    Args:
        df: インジケータ付き DataFrame（DatetimeIndex）
        symbol: 通貨ペア
        tf_name: 時間足名
        data_dir: データルートディレクトリ
        force: 既存キャッシュを強制再生成

    Returns:
        生成した日別ファイル数
    """
    cache_dir = data_dir / symbol / "daily_cache" / tf_name
    cache_dir.mkdir(parents=True, exist_ok=True)

    # 日付ごとにグループ化
    df_with_date = df.copy()
    df_with_date["_date"] = df.index.date

    count = 0
    for day, day_df in df_with_date.groupby("_date"):
        out_path = cache_dir / f"{day}.parquet"
        if out_path.exists() and not force:
            continue

        day_df = day_df.drop(columns=["_date"])
        day_df.to_parquet(out_path, engine="pyarrow")
        count += 1

    return count


# ============================================================
# Step 3: ティック月別CSV → 日別Parquetキャッシュ
# ============================================================

def split_ticks_to_daily(
    symbol: str,
    data_dir: Path,
    start_year: int | None = None,
    end_year: int | None = None,
    force: bool = False,
) -> int:
    """ティック月別CSV → 日別Parquetキャッシュ

    ティックCSVフォーマット（月別）:
        timestamp,bid,ask,volume,flags  （Parquet形式もサポート）

    Args:
        symbol: 通貨ペア
        data_dir: データルートディレクトリ
        start_year: 開始年（Noneで全期間）
        end_year: 終了年（Noneで全期間）
        force: 既存キャッシュを強制再生成

    Returns:
        生成した日別ファイル数
    """
    tick_dir = data_dir / symbol / "ticks"
    if not tick_dir.exists():
        logger.info(
            "[Pipeline] %s: ティックデータなし (%s)",
            symbol, tick_dir,
        )
        return 0

    cache_dir = data_dir / symbol / "daily_cache" / "ticks"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # 月別ファイルを検索（CSV + Parquet）
    tick_files = sorted(
        list(tick_dir.glob(f"{symbol}_ticks_*.csv"))
        + list(tick_dir.glob(f"ticks_*.parquet"))
        + list(tick_dir.glob(f"{symbol}_ticks_*.parquet"))
    )

    if not tick_files:
        logger.info(
            "[Pipeline] %s: ティックファイルなし", symbol
        )
        return 0

    total_count = 0
    for tick_file in tick_files:
        # 年フィルタリング
        if start_year or end_year:
            # ファイル名から年月を抽出
            stem = tick_file.stem
            digits = "".join(c for c in stem if c.isdigit())
            if len(digits) >= 4:
                file_year = int(digits[:4])
                if start_year and file_year < start_year:
                    continue
                if end_year and file_year > end_year:
                    continue

        logger.info("[Pipeline] ティック処理: %s", tick_file.name)

        # 読み込み
        if tick_file.suffix == ".parquet":
            df = pd.read_parquet(tick_file)
        else:
            df = pd.read_csv(tick_file, parse_dates=["timestamp"])
            if "timestamp" in df.columns:
                df = df.set_index("timestamp")

        if df.empty:
            continue

        # DatetimeIndex 確認
        if not isinstance(df.index, pd.DatetimeIndex):
            if "timestamp" in df.columns:
                df = df.set_index("timestamp")
            else:
                logger.warning(
                    "[Pipeline] DatetimeIndex不明: %s",
                    tick_file.name,
                )
                continue

        # 日別に分割
        df["_date"] = df.index.date
        for day, day_df in df.groupby("_date"):
            out_path = cache_dir / f"{day}.parquet"
            if out_path.exists() and not force:
                continue

            day_df = day_df.drop(columns=["_date"])
            day_df.to_parquet(out_path, engine="pyarrow")
            total_count += 1

    return total_count


# ============================================================
# 統合パイプライン
# ============================================================

def prepare(
    symbol: str,
    start_year: int = 2010,
    end_year: int = 2025,
    timeframes: list[str] | None = None,
    data_dir: Path | None = None,
    force: bool = False,
    include_ticks: bool = True,
) -> dict:
    """データ前処理パイプライン（メインエントリーポイント）

    Args:
        symbol: 通貨ペア
        start_year: 開始年
        end_year: 終了年
        timeframes: 対象時間足（Noneで DEFAULT_TIMEFRAMES）
        data_dir: データディレクトリ（Noneで自動検出）
        force: 既存キャッシュを強制再生成
        include_ticks: ティックデータも処理するか

    Returns:
        処理結果サマリーdict
    """
    if data_dir is None:
        data_dir = _get_data_dir()
    tfs = timeframes or DEFAULT_TIMEFRAMES

    logger.info(
        "=== データパイプライン開始: %s ===", symbol,
    )
    logger.info(
        "期間: %d-%d, 時間足: %s, ティック: %s",
        start_year, end_year,
        ", ".join(tfs),
        "ON" if include_ticks else "OFF",
    )

    start_time = time.time()
    results: dict = {"symbol": symbol, "timeframes": {}}

    # Step 1+2: OHLCV → インジケータ → 日別Parquet
    for tf_name in tfs:
        tf_start = time.time()
        logger.info("[Pipeline] %s %s 処理開始...", symbol, tf_name)

        # インジケータ計算
        df = compute_indicators(
            symbol, tf_name, data_dir, force=force,
        )
        if df is None:
            results["timeframes"][tf_name] = {"status": "skip", "reason": "no_data"}
            continue

        # 期間フィルタ
        start_dt = pd.Timestamp(f"{start_year}-01-01")
        end_dt = pd.Timestamp(f"{end_year + 1}-01-01")
        df = df.loc[
            (df.index >= start_dt) & (df.index < end_dt)
        ]

        # 日別分割
        n_files = split_to_daily_parquet(
            df, symbol, tf_name, data_dir, force=force,
        )

        elapsed = time.time() - tf_start
        results["timeframes"][tf_name] = {
            "status": "ok",
            "rows": len(df),
            "columns": len(df.columns),
            "daily_files": n_files,
            "elapsed_sec": round(elapsed, 1),
        }
        logger.info(
            "[Pipeline] %s %s: %d日分キャッシュ生成 (%.1f秒)",
            symbol, tf_name, n_files, elapsed,
        )

    # Step 3: ティック → 日別Parquet
    if include_ticks:
        tick_start = time.time()
        n_tick_files = split_ticks_to_daily(
            symbol, data_dir,
            start_year=start_year,
            end_year=end_year,
            force=force,
        )
        tick_elapsed = time.time() - tick_start
        results["ticks"] = {
            "daily_files": n_tick_files,
            "elapsed_sec": round(tick_elapsed, 1),
        }
        logger.info(
            "[Pipeline] ティック: %d日分キャッシュ生成 (%.1f秒)",
            n_tick_files, tick_elapsed,
        )

    # manifest.json 更新
    manifest = _load_manifest(symbol, data_dir)
    manifest.update({
        "symbol": symbol,
        "timeframes": tfs,
        "start_year": start_year,
        "end_year": end_year,
        "config_hash": _config_hash(tfs),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "include_ticks": include_ticks,
    })
    _save_manifest(symbol, data_dir, manifest)

    total_elapsed = time.time() - start_time
    results["total_elapsed_sec"] = round(total_elapsed, 1)
    logger.info(
        "=== パイプライン完了: %s (%.1f秒) ===",
        symbol, total_elapsed,
    )
    return results


# ============================================================
# 日別キャッシュ読み込みユーティリティ
# ============================================================

def load_daily_cache(
    symbol: str,
    tf_name: str,
    target_date: date,
    data_dir: Path | None = None,
) -> pd.DataFrame | None:
    """日別Parquetキャッシュを1日分読み込み

    Args:
        symbol: 通貨ペア
        tf_name: 時間足名（"M15", "H1", "ticks" 等）
        target_date: 対象日
        data_dir: データディレクトリ

    Returns:
        DataFrame or None（ファイルなし時）
    """
    if data_dir is None:
        data_dir = _get_data_dir()

    path = (
        data_dir / symbol / "daily_cache" / tf_name
        / f"{target_date}.parquet"
    )
    if not path.exists():
        return None

    return pd.read_parquet(path)


def list_cached_dates(
    symbol: str,
    tf_name: str,
    data_dir: Path | None = None,
) -> list[date]:
    """キャッシュ済み日付一覧を取得

    Args:
        symbol: 通貨ペア
        tf_name: 時間足名
        data_dir: データディレクトリ

    Returns:
        ソート済み日付リスト
    """
    if data_dir is None:
        data_dir = _get_data_dir()

    cache_dir = data_dir / symbol / "daily_cache" / tf_name
    if not cache_dir.exists():
        return []

    dates = []
    for f in cache_dir.glob("*.parquet"):
        try:
            d = date.fromisoformat(f.stem)
            dates.append(d)
        except ValueError:
            continue

    return sorted(dates)


# ============================================================
# CLI エントリーポイント
# ============================================================

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="バックテスト用データ前処理パイプライン",
    )
    sub = parser.add_subparsers(dest="command")

    # prepare コマンド
    p_prep = sub.add_parser(
        "prepare",
        help="OHLCV+ティック → 日別Parquetキャッシュ生成",
    )
    p_prep.add_argument(
        "--symbol", required=True, help="通貨ペア（例: USDJPY）",
    )
    p_prep.add_argument(
        "--start", type=int, default=2010, help="開始年（デフォルト: 2010）",
    )
    p_prep.add_argument(
        "--end", type=int, default=2025, help="終了年（デフォルト: 2025）",
    )
    p_prep.add_argument(
        "--timeframes", nargs="*", default=None,
        help=f"時間足（デフォルト: {', '.join(DEFAULT_TIMEFRAMES)}）",
    )
    p_prep.add_argument(
        "--force", action="store_true",
        help="既存キャッシュを強制再生成",
    )
    p_prep.add_argument(
        "--no-ticks", action="store_true",
        help="ティックデータをスキップ",
    )
    p_prep.add_argument(
        "--data-dir", default=None, help="データディレクトリ",
    )

    # status コマンド
    p_status = sub.add_parser(
        "status", help="キャッシュ状態を確認",
    )
    p_status.add_argument(
        "--symbol", required=True, help="通貨ペア",
    )
    p_status.add_argument(
        "--data-dir", default=None, help="データディレクトリ",
    )

    args = parser.parse_args()

    if args.command == "prepare":
        data_dir = Path(args.data_dir) if args.data_dir else None
        result = prepare(
            symbol=args.symbol,
            start_year=args.start,
            end_year=args.end,
            timeframes=args.timeframes,
            data_dir=data_dir,
            force=args.force,
            include_ticks=not args.no_ticks,
        )
        print(json.dumps(result, indent=2, default=str))

    elif args.command == "status":
        data_dir = (
            Path(args.data_dir) if args.data_dir
            else _get_data_dir()
        )
        manifest = _load_manifest(args.symbol, data_dir)
        if not manifest:
            print(f"{args.symbol}: キャッシュなし")
            return

        print(f"=== {args.symbol} キャッシュ状態 ===")
        print(f"更新日時: {manifest.get('updated_at', '不明')}")
        print(f"設定ハッシュ: {manifest.get('config_hash', '不明')}")
        print(f"時間足: {manifest.get('timeframes', [])}")
        print(f"期間: {manifest.get('start_year')}-{manifest.get('end_year')}")

        for tf in manifest.get("timeframes", []):
            dates = list_cached_dates(args.symbol, tf, data_dir)
            if dates:
                print(
                    f"  {tf}: {len(dates)}日 "
                    f"({dates[0]} → {dates[-1]})"
                )
            else:
                print(f"  {tf}: キャッシュなし")

        tick_dates = list_cached_dates(args.symbol, "ticks", data_dir)
        if tick_dates:
            print(
                f"  ticks: {len(tick_dates)}日 "
                f"({tick_dates[0]} → {tick_dates[-1]})"
            )
        else:
            print("  ticks: キャッシュなし")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
