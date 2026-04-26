"""バックテスト用データ前処理パイプライン

■ OHLCVパイプライン:
  OHLCV単一CSV → インジケータ事前計算 → 月別Parquetキャッシュ

■ ティックパイプライン:
  ティック単一CSV → 月別Parquetキャッシュ

Usage:
    # OHLCV: インジケータ計算 + 月別キャッシュ
    uv run python -m autotrader.backtest.data_pipeline prepare-ohlcv \\
        --symbol USDJPY --start 2010 --end 2026

    # ティック: 単一CSV → 月別キャッシュ
    uv run python -m autotrader.backtest.data_pipeline prepare-ticks \\
        --symbol USDJPY --tick-csv /path/to/USDJPY_ticks.csv

    # キャッシュ状態確認
    uv run python -m autotrader.backtest.data_pipeline status --symbol USDJPY
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

# UnifiedBotConfig.timeframes のデフォルトと一致
ALL_TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4", "H8", "D1"]


def _get_data_dir() -> Path:
    from autotrader.config.paths import get_data_dir
    return Path(get_data_dir())


# ============================================================
# manifest.json 管理
# ============================================================

def _manifest_path(symbol: str, data_dir: Path) -> Path:
    return data_dir / symbol / "monthly_cache" / "manifest.json"


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


# ============================================================
# OHLCV CSV 読み込み
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
        csv_dir = data_dir / symbol / "chart"
        if not csv_dir.exists():
            logger.warning("[Pipeline] CSVディレクトリなし: %s", csv_dir)
            return None

    tf_csv = "Daily" if tf_name == "D1" else tf_name
    pattern = f"{symbol}_{tf_csv}_*.csv"
    files = sorted(csv_dir.glob(pattern))
    if not files:
        alt_dir = data_dir / symbol / "chart" / "csv"
        if alt_dir.exists():
            files = sorted(alt_dir.glob(pattern))

    if not files:
        logger.warning("[Pipeline] %s %s: CSVなし (%s)", symbol, tf_name, pattern)
        return None

    csv_path = files[-1]
    size_mb = csv_path.stat().st_size / 1024 / 1024
    logger.info("[Pipeline] CSV読み込み: %s (%.1fMB)", csv_path.name, size_mb)

    df = DataLoader.load_mt5_csv(csv_path)
    if df is not None and not df.empty:
        if "time" in df.columns and not isinstance(df.index, pd.DatetimeIndex):
            df = df.set_index("time")
        df.index.name = "time"
    return df


# ============================================================
# OHLCV → インジケータ計算 → 月別Parquetキャッシュ
# ============================================================

def prepare_ohlcv(
    symbol: str,
    start_year: int = 2010,
    end_year: int = 2026,
    timeframes: list[str] | None = None,
    data_dir: Path | None = None,
    force: bool = False,
) -> dict:
    """OHLCV単一CSV → インジケータ計算 → 月別Parquetキャッシュ

    Args:
        symbol: 通貨ペア
        start_year: 開始年
        end_year: 終了年（この年を含む）
        timeframes: 対象時間足（Noneで ALL_TIMEFRAMES）
        data_dir: データディレクトリ
        force: 強制再生成

    Returns:
        処理結果サマリー
    """
    from autotrader.calculator.precompute import PrecomputeEngine

    if data_dir is None:
        data_dir = _get_data_dir()
    tfs = timeframes or ALL_TIMEFRAMES

    logger.info("=== OHLCV パイプライン: %s ===", symbol)
    logger.info("期間: %d-%d, 時間足: %s", start_year, end_year, ", ".join(tfs))

    total_start = time.time()
    results: dict = {"symbol": symbol, "timeframes": {}}
    engine = PrecomputeEngine()

    for tf_name in tfs:
        tf_start = time.time()
        logger.info("[%s] %s 処理開始...", symbol, tf_name)

        # CSV読み込み
        df = _load_ohlcv_csv(symbol, tf_name, data_dir)
        if df is None or df.empty:
            results["timeframes"][tf_name] = {"status": "skip", "reason": "no_data"}
            continue

        # インジケータ計算（PrecomputeEngineのキャッシュ活用）
        tf_enum = Timeframe(tf_name)
        df_with_indicators = engine.precompute(df, symbol, tf_enum, use_cache=not force)

        logger.info(
            "[%s] %s: インジケータ完了 (%d行 × %d列)",
            symbol, tf_name, len(df_with_indicators), len(df_with_indicators.columns),
        )

        # 期間フィルタ
        start_dt = pd.Timestamp(f"{start_year}-01-01")
        end_dt = pd.Timestamp(f"{end_year + 1}-01-01")
        df_filtered = df_with_indicators.loc[
            (df_with_indicators.index >= start_dt) & (df_with_indicators.index < end_dt)
        ]

        # 月別Parquetキャッシュ分割
        n_files = _split_to_monthly_parquet(
            df_filtered, symbol, tf_name, data_dir, force=force,
        )

        elapsed = time.time() - tf_start
        results["timeframes"][tf_name] = {
            "status": "ok",
            "rows": len(df_filtered),
            "columns": len(df_filtered.columns),
            "monthly_files": n_files,
            "elapsed_sec": round(elapsed, 1),
        }
        logger.info("[%s] %s: %dヶ月分キャッシュ生成 (%.1f秒)", symbol, tf_name, n_files, elapsed)

    # manifest更新
    manifest = _load_manifest(symbol, data_dir)
    manifest["ohlcv"] = {
        "timeframes": tfs,
        "start_year": start_year,
        "end_year": end_year,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_manifest(symbol, data_dir, manifest)

    total_elapsed = time.time() - total_start
    results["total_elapsed_sec"] = round(total_elapsed, 1)
    logger.info("=== OHLCV完了: %s (%.1f秒) ===", symbol, total_elapsed)
    return results


def _split_to_monthly_parquet(
    df: pd.DataFrame,
    symbol: str,
    tf_name: str,
    data_dir: Path,
    force: bool = False,
) -> int:
    """DataFrame を月別 Parquet ファイルに分割保存

    ファイル名: YYYY-MM.parquet（例: 2024-01.parquet）
    """
    cache_dir = data_dir / symbol / "monthly_cache" / tf_name
    cache_dir.mkdir(parents=True, exist_ok=True)

    df_copy = df.copy()
    df_copy["_ym"] = df.index.to_period("M")

    count = 0
    for period, month_df in df_copy.groupby("_ym"):
        filename = f"{period}.parquet"
        out_path = cache_dir / filename
        if out_path.exists() and not force:
            continue

        month_df = month_df.drop(columns=["_ym"])
        month_df.to_parquet(out_path, engine="pyarrow")
        count += 1

    return count


# ============================================================
# ティック単一CSV → 月別Parquetキャッシュ
# ============================================================

def prepare_ticks(
    symbol: str,
    tick_csv: str | Path | None = None,
    data_dir: Path | None = None,
    force: bool = False,
) -> dict:
    """ティック単一CSV → 月別Parquetキャッシュ

    MT5からエクスポートした単一CSVまたはParquetを読み込み、
    月別Parquetに分割保存する。

    ティックCSVフォーマット（MT5エクスポート形式）:
        タブ区切り: <DATE> <TIME> <BID> <ASK> <LAST> <VOLUME> <FLAGS>
    またはカンマ区切り: timestamp,bid,ask,volume,flags

    自動検出:
        tick_csv未指定時は data/{SYMBOL}/ticks/ 内の最大ファイルを使用

    Args:
        symbol: 通貨ペア
        tick_csv: ティックCSV/Parquetファイルパス（Noneで自動検出）
        data_dir: データディレクトリ
        force: 強制再生成

    Returns:
        処理結果サマリー
    """
    if data_dir is None:
        data_dir = _get_data_dir()

    logger.info("=== ティックパイプライン: %s ===", symbol)
    total_start = time.time()

    # ティックファイル検出
    tick_path = _resolve_tick_file(symbol, tick_csv, data_dir)
    if tick_path is None:
        logger.warning("[Pipeline] %s: ティックファイルなし", symbol)
        return {"symbol": symbol, "status": "no_data"}

    size_mb = tick_path.stat().st_size / 1024 / 1024
    logger.info("[Pipeline] ティック読み込み: %s (%.1fMB)", tick_path.name, size_mb)

    # 読み込み
    df = _load_tick_file(tick_path)
    if df is None or df.empty:
        return {"symbol": symbol, "status": "load_failed"}

    logger.info("[Pipeline] ティック: %d行読み込み完了", len(df))

    # 月別分割
    cache_dir = data_dir / symbol / "monthly_cache" / "ticks"
    cache_dir.mkdir(parents=True, exist_ok=True)

    df["_ym"] = df.index.to_period("M")
    count = 0
    for period, month_df in df.groupby("_ym"):
        filename = f"{period}.parquet"
        out_path = cache_dir / filename
        if out_path.exists() and not force:
            continue

        month_df = month_df.drop(columns=["_ym"])
        month_df.to_parquet(out_path, engine="pyarrow")
        count += 1
        logger.info("  %s: %d ticks → %s", period, len(month_df), filename)

    # manifest更新
    manifest = _load_manifest(symbol, data_dir)
    first_month = str(df["_ym"].min())
    last_month = str(df["_ym"].max())
    manifest["ticks"] = {
        "source": tick_path.name,
        "total_ticks": len(df),
        "first_month": first_month,
        "last_month": last_month,
        "monthly_files": count,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_manifest(symbol, data_dir, manifest)

    total_elapsed = time.time() - total_start
    result = {
        "symbol": symbol,
        "status": "ok",
        "total_ticks": len(df),
        "period": f"{first_month} → {last_month}",
        "monthly_files": count,
        "elapsed_sec": round(total_elapsed, 1),
    }
    logger.info(
        "=== ティック完了: %s %dヶ月分 (%.1f秒) ===",
        symbol, count, total_elapsed,
    )
    return result


def _resolve_tick_file(
    symbol: str,
    tick_csv: str | Path | None,
    data_dir: Path,
) -> Path | None:
    """ティックファイルパスを解決"""
    if tick_csv is not None:
        p = Path(tick_csv)
        if p.exists():
            return p
        logger.warning("[Pipeline] 指定ファイルなし: %s", p)
        return None

    # 自動検出: data/{SYMBOL}/ticks/ 内の最大ファイル
    tick_dir = data_dir / symbol / "ticks"
    if not tick_dir.exists():
        return None

    candidates = (
        list(tick_dir.glob(f"{symbol}_*.csv"))
        + list(tick_dir.glob(f"{symbol}_*.parquet"))
        + list(tick_dir.glob("ticks_*.csv"))
        + list(tick_dir.glob("ticks_*.parquet"))
    )
    if not candidates:
        return None

    # 最大ファイルを選択
    return max(candidates, key=lambda p: p.stat().st_size)


def _load_tick_file(tick_path: Path) -> pd.DataFrame | None:
    """ティックファイルを読み込み（CSV/Parquet自動判定）"""
    try:
        if tick_path.suffix == ".parquet":
            df = pd.read_parquet(tick_path)
            if not isinstance(df.index, pd.DatetimeIndex):
                if "timestamp" in df.columns:
                    df = df.set_index("timestamp")
            return df

        # CSV読み込み: フォーマット自動判定
        # まず先頭行を読んで区切り文字とカラムを判定
        with open(tick_path, "r", encoding="utf-8") as f:
            header = f.readline().strip()

        if "\t" in header and "<DATE>" in header:
            # MT5タブ区切り形式: <DATE> <TIME> <BID> <ASK> <LAST> <VOLUME> <FLAGS>
            return _load_mt5_tick_csv(tick_path)
        elif "," in header and "timestamp" in header.lower():
            # カンマ区切り形式: timestamp,bid,ask,volume,flags
            df = pd.read_csv(tick_path, parse_dates=["timestamp"])
            df = df.set_index("timestamp")
            return df
        else:
            # フォールバック: タブ区切りを試行
            return _load_mt5_tick_csv(tick_path)

    except Exception as e:
        logger.error("[Pipeline] ティック読み込み失敗: %s - %s", tick_path.name, e)
        return None


def _load_mt5_tick_csv(tick_path: Path) -> pd.DataFrame:
    """MT5エクスポート形式のティックCSVを読み込み

    フォーマット（タブ区切り）:
    <DATE>        <TIME>          <BID>      <ASK>      <LAST>     <VOLUME>  <FLAGS>
    2022.01.03    00:02:09.925    115.107    115.117    0          0         6
    """
    import polars as pl

    df = pl.read_csv(tick_path, separator="\t", has_header=True)
    columns = df.columns

    # カラム名の正規化
    if "<DATE>" in columns and "<TIME>" in columns:
        # 日時結合
        df = df.with_columns(
            pl.concat_str([
                pl.col("<DATE>"),
                pl.lit(" "),
                pl.col("<TIME>"),
            ]).alias("datetime_str")
        )
        # ミリ秒付き時刻のパース（"2022.01.03 00:02:09.925"）
        df = df.with_columns(
            pl.col("datetime_str")
            .str.strptime(pl.Datetime("ms"), "%Y.%m.%d %H:%M:%S%.3f")
            .alias("timestamp")
        )

        # 必要列を選択
        select_map = {"timestamp": "timestamp"}
        for col, alias in [
            ("<BID>", "bid"), ("<ASK>", "ask"),
            ("<LAST>", "last"), ("<VOLUME>", "volume"), ("<FLAGS>", "flags"),
        ]:
            if col in columns:
                select_map[col] = alias

        df = df.select([
            pl.col(src).alias(dst) for src, dst in select_map.items()
        ])
    else:
        raise ValueError(f"未知のティックCSV形式: {columns}")

    pdf = df.to_pandas()
    pdf = pdf.set_index("timestamp")
    pdf.index.name = "timestamp"
    return pdf


# ============================================================
# 月別キャッシュ読み込みユーティリティ
# ============================================================

def load_monthly_cache(
    symbol: str,
    tf_name: str,
    year: int,
    month: int,
    data_dir: Path | None = None,
) -> pd.DataFrame | None:
    """月別Parquetキャッシュを1ヶ月分読み込み

    Args:
        symbol: 通貨ペア
        tf_name: 時間足名（"M15", "H1", "ticks" 等）
        year: 年
        month: 月（1-12）
        data_dir: データディレクトリ

    Returns:
        DataFrame or None（ファイルなし時）
    """
    if data_dir is None:
        data_dir = _get_data_dir()

    filename = f"{year}-{month:02d}.parquet"
    path = data_dir / symbol / "monthly_cache" / tf_name / filename
    if not path.exists():
        return None

    return pd.read_parquet(path)


def list_cached_months(
    symbol: str,
    tf_name: str,
    data_dir: Path | None = None,
) -> list[tuple[int, int]]:
    """キャッシュ済み年月一覧を取得

    Returns:
        ソート済み (year, month) タプルリスト
    """
    if data_dir is None:
        data_dir = _get_data_dir()

    cache_dir = data_dir / symbol / "monthly_cache" / tf_name
    if not cache_dir.exists():
        return []

    months = []
    for f in cache_dir.glob("*.parquet"):
        try:
            parts = f.stem.split("-")
            if len(parts) == 2:
                months.append((int(parts[0]), int(parts[1])))
        except (ValueError, IndexError):
            continue

    return sorted(months)


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

    # prepare-ohlcv
    p_ohlcv = sub.add_parser(
        "prepare-ohlcv",
        help="OHLCV → インジケータ計算 → 月別Parquetキャッシュ",
    )
    p_ohlcv.add_argument("--symbol", required=True, help="通貨ペア")
    p_ohlcv.add_argument("--start", type=int, default=2010, help="開始年")
    p_ohlcv.add_argument("--end", type=int, default=2026, help="終了年")
    p_ohlcv.add_argument("--timeframes", nargs="*", default=None,
                         help=f"時間足（デフォルト: {', '.join(ALL_TIMEFRAMES)}）")
    p_ohlcv.add_argument("--force", action="store_true", help="強制再生成")
    p_ohlcv.add_argument("--data-dir", default=None, help="データディレクトリ")

    # prepare-ticks
    p_ticks = sub.add_parser(
        "prepare-ticks",
        help="ティック単一CSV → 月別Parquetキャッシュ",
    )
    p_ticks.add_argument("--symbol", required=True, help="通貨ペア")
    p_ticks.add_argument("--tick-csv", default=None,
                         help="ティックCSVパス（省略時: data/{SYMBOL}/ticks/ 自動検出）")
    p_ticks.add_argument("--force", action="store_true", help="強制再生成")
    p_ticks.add_argument("--data-dir", default=None, help="データディレクトリ")

    # status
    p_status = sub.add_parser("status", help="キャッシュ状態確認")
    p_status.add_argument("--symbol", required=True, help="通貨ペア")
    p_status.add_argument("--data-dir", default=None, help="データディレクトリ")

    args = parser.parse_args()

    if args.command == "prepare-ohlcv":
        data_dir = Path(args.data_dir) if args.data_dir else None
        result = prepare_ohlcv(
            symbol=args.symbol,
            start_year=args.start,
            end_year=args.end,
            timeframes=args.timeframes,
            data_dir=data_dir,
            force=args.force,
        )
        print(json.dumps(result, indent=2, default=str))

    elif args.command == "prepare-ticks":
        data_dir = Path(args.data_dir) if args.data_dir else None
        tick_csv = args.tick_csv
        result = prepare_ticks(
            symbol=args.symbol,
            tick_csv=tick_csv,
            data_dir=data_dir,
            force=args.force,
        )
        print(json.dumps(result, indent=2, default=str))

    elif args.command == "status":
        data_dir = Path(args.data_dir) if args.data_dir else _get_data_dir()
        manifest = _load_manifest(args.symbol, data_dir)
        if not manifest:
            print(f"{args.symbol}: キャッシュなし")
            return

        print(f"=== {args.symbol} キャッシュ状態 ===")

        ohlcv = manifest.get("ohlcv", {})
        if ohlcv:
            print(f"OHLCV 更新: {ohlcv.get('updated_at', '?')}")
            print(f"  期間: {ohlcv.get('start_year')}-{ohlcv.get('end_year')}")
            for tf in ohlcv.get("timeframes", []):
                months = list_cached_months(args.symbol, tf, data_dir)
                if months:
                    print(f"  {tf}: {len(months)}ヶ月 ({months[0][0]}-{months[0][1]:02d} → {months[-1][0]}-{months[-1][1]:02d})")
                else:
                    print(f"  {tf}: キャッシュなし")

        ticks = manifest.get("ticks", {})
        if ticks:
            print(f"ティック 更新: {ticks.get('updated_at', '?')}")
            print(f"  ソース: {ticks.get('source')}")
            print(f"  期間: {ticks.get('first_month')} → {ticks.get('last_month')}")
            print(f"  総ティック: {ticks.get('total_ticks', 0):,}")
            tick_months = list_cached_months(args.symbol, "ticks", data_dir)
            if tick_months:
                print(f"  月別ファイル: {len(tick_months)}ヶ月")
        else:
            print("ティック: キャッシュなし")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
