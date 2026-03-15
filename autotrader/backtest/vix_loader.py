"""VIXデータローダー

yfinanceで^VIXの日次データ（Close）を取得し、
ローカルCSVキャッシュに保存して再利用する。
バックテスト用にdate→float辞書で提供。
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


def _get_vix_cache_dir(data_dir: str | Path) -> Path:
    """VIXキャッシュディレクトリを取得

    Args:
        data_dir: データルートディレクトリ

    Returns:
        Path: VIXキャッシュディレクトリ
    """
    cache_dir = Path(data_dir) / "vix"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _download_vix_year(year: int) -> pd.DataFrame | None:
    """yfinanceでVIX年次データをダウンロード

    Args:
        year: 対象年

    Returns:
        pd.DataFrame | None: VIXデータ（Date, Close列）
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.error(
            "yfinanceが未インストール: "
            "pip install yfinance"
        )
        return None

    start = f"{year}-01-01"
    end = f"{year + 1}-01-01"
    logger.info("VIXデータ取得中: %s〜%s", start, end)

    try:
        ticker = yf.Ticker("^VIX")
        df = ticker.history(start=start, end=end)
    except Exception:
        logger.exception("VIXデータ取得失敗: %d年", year)
        return None

    if df is None or df.empty:
        logger.warning("VIXデータなし: %d年", year)
        return None

    # Date列を追加（indexがDatetimeIndex）
    df = df.reset_index()
    # 列名正規化（yfinance v0.2+で大文字始まり）
    col_map = {}
    for c in df.columns:
        if c.lower() == "date":
            col_map[c] = "Date"
        elif c.lower() == "close":
            col_map[c] = "Close"
    df = df.rename(columns=col_map)

    if "Date" not in df.columns or "Close" not in df.columns:
        logger.error(
            "VIXデータ列不正: %s", list(df.columns),
        )
        return None

    result = df[["Date", "Close"]].copy()
    result["Date"] = pd.to_datetime(result["Date"]).dt.date
    logger.info(
        "VIXデータ取得完了: %d年 %d日分",
        year,
        len(result),
    )
    return result


def _save_cache(
    df: pd.DataFrame,
    cache_path: Path,
) -> None:
    """CSVキャッシュに保存

    Args:
        df: VIXデータ
        cache_path: 保存先パス
    """
    df.to_csv(cache_path, index=False)
    logger.info("VIXキャッシュ保存: %s", cache_path)


def _load_cache(cache_path: Path) -> pd.DataFrame | None:
    """CSVキャッシュから読み込み

    Args:
        cache_path: キャッシュファイルパス

    Returns:
        pd.DataFrame | None: VIXデータ
    """
    if not cache_path.exists():
        return None
    try:
        df = pd.read_csv(cache_path)
        df["Date"] = pd.to_datetime(df["Date"]).dt.date
        return df
    except Exception:
        logger.warning(
            "VIXキャッシュ読み込み失敗: %s",
            cache_path,
        )
        return None


def load_vix_year(
    year: int,
    data_dir: str | Path,
) -> dict[date, float]:
    """1年分のVIXデータをロード（キャッシュ優先）

    Args:
        year: 対象年
        data_dir: データルートディレクトリ

    Returns:
        dict[date, float]: 日付→VIX Close値
    """
    cache_dir = _get_vix_cache_dir(data_dir)
    cache_path = cache_dir / f"vix_{year}.csv"

    # キャッシュ読み込み
    df = _load_cache(cache_path)
    if df is not None and not df.empty:
        logger.info(
            "VIXキャッシュ使用: %s (%d日分)",
            cache_path,
            len(df),
        )
        return {
            row["Date"]: float(row["Close"])
            for _, row in df.iterrows()
        }

    # ダウンロード
    df = _download_vix_year(year)
    if df is None or df.empty:
        logger.warning(
            "VIXデータ取得不可: %d年（空辞書を返却）",
            year,
        )
        return {}

    # キャッシュ保存
    _save_cache(df, cache_path)

    return {
        row["Date"]: float(row["Close"])
        for _, row in df.iterrows()
    }


def load_vix_range(
    start_year: int,
    end_year: int,
    data_dir: str | Path,
) -> dict[date, float]:
    """複数年分のVIXデータをロード

    Args:
        start_year: 開始年
        end_year: 終了年（含む）
        data_dir: データルートディレクトリ

    Returns:
        dict[date, float]: 日付→VIX Close値（全年統合）
    """
    merged: dict[date, float] = {}
    for year in range(start_year, end_year + 1):
        year_data = load_vix_year(year, data_dir)
        merged.update(year_data)

    logger.info(
        "VIXデータ統合: %d-%d年 %d日分",
        start_year,
        end_year,
        len(merged),
    )
    return merged
