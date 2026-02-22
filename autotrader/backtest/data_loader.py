"""バックテスト用データローダー

Polars + Parquetによる大規模データの高速読み込み。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pandas as pd
import polars as pl

from autotrader.core.enums import Timeframe

logger = logging.getLogger(__name__)


class DataLoader:
    """バックテスト用データローダー

    大規模OHLCVデータをPolarsで高速読み込みし、
    バックテストエンジンに提供する。

    Attributes:
        data_dir: データディレクトリパス
    """

    def __init__(
        self,
        data_dir: str | Path = "data/raw",
    ) -> None:
        """初期化

        Args:
            data_dir: データディレクトリパス
        """
        self.data_dir = Path(data_dir)

    def _load_raw_data(
        self,
        symbol: str,
        timeframe: Timeframe,
        start_date: datetime,
        end_date: datetime,
    ) -> pd.DataFrame:
        """生データファイルから読み込み

        CSVまたはParquetファイルを検索して読み込む。

        Args:
            symbol: シンボル
            timeframe: 時間足
            start_date: 開始日時
            end_date: 終了日時

        Returns:
            pd.DataFrame: OHLCVデータ
        """
        # Parquetファイルを優先
        parquet_pattern = f"{symbol}_{timeframe.value}*.parquet"
        parquet_files = list(self.data_dir.glob(parquet_pattern))

        if parquet_files:
            return self._load_parquet(
                parquet_files, start_date, end_date
            )

        # CSVファイルを検索
        csv_pattern = f"{symbol}_{timeframe.value}*.csv"
        csv_files = list(self.data_dir.glob(csv_pattern))

        if csv_files:
            return self._load_csv(csv_files, start_date, end_date)

        # 単一ファイルパターンも試行
        single_parquet = self.data_dir / f"{symbol}_{timeframe.value}.parquet"
        if single_parquet.exists():
            return self._load_parquet([single_parquet], start_date, end_date)

        single_csv = self.data_dir / f"{symbol}_{timeframe.value}.csv"
        if single_csv.exists():
            return self._load_csv([single_csv], start_date, end_date)

        raise FileNotFoundError(
            f"データファイルが見つかりません: {symbol}_{timeframe.value}"
        )

    def _load_parquet(
        self,
        files: list[Path],
        start_date: datetime,
        end_date: datetime,
    ) -> pd.DataFrame:
        """Parquetファイルから読み込み

        Args:
            files: ファイルパスリスト
            start_date: 開始日時
            end_date: 終了日時

        Returns:
            pd.DataFrame: OHLCVデータ
        """
        # Polarsで高速読み込み
        dfs = []
        for file_path in sorted(files):
            lf = pl.scan_parquet(file_path)

            # 日時フィルタリング
            lf = lf.filter(
                (pl.col("time") >= start_date)
                & (pl.col("time") < end_date)
            )

            df = lf.collect()
            if df.height > 0:
                dfs.append(df)

        if not dfs:
            return pd.DataFrame(
                columns=["time", "open", "high", "low", "close", "volume"]
            )

        # 結合してpandasに変換
        combined = pl.concat(dfs).sort("time")
        return combined.to_pandas()

    def _load_csv(
        self,
        files: list[Path],
        start_date: datetime,
        end_date: datetime,
    ) -> pd.DataFrame:
        """CSVファイルから読み込み

        Args:
            files: ファイルパスリスト
            start_date: 開始日時
            end_date: 終了日時

        Returns:
            pd.DataFrame: OHLCVデータ
        """
        dfs = []
        for file_path in sorted(files):
            # Polarsで高速読み込み
            df = pl.read_csv(
                file_path,
                try_parse_dates=True,
            )

            # 列名の正規化
            df = self._normalize_columns(df)

            # 日時フィルタリング
            df = df.filter(
                (pl.col("time") >= start_date)
                & (pl.col("time") < end_date)
            )

            if df.height > 0:
                dfs.append(df)

        if not dfs:
            return pd.DataFrame(
                columns=["time", "open", "high", "low", "close", "volume"]
            )

        # 結合してpandasに変換
        combined = pl.concat(dfs).sort("time")
        return combined.to_pandas()

    def _normalize_columns(self, df: pl.DataFrame) -> pl.DataFrame:
        """列名を正規化

        様々なCSV形式に対応するため列名を統一。

        Args:
            df: 入力データフレーム

        Returns:
            pl.DataFrame: 正規化されたデータフレーム
        """
        # 一般的な列名マッピング
        column_mapping: dict[str, str] = {}

        for col in df.columns:
            col_lower = col.lower()

            if col_lower in ("time", "datetime", "date", "timestamp"):
                column_mapping[col] = "time"
            elif col_lower == "open":
                column_mapping[col] = "open"
            elif col_lower == "high":
                column_mapping[col] = "high"
            elif col_lower == "low":
                column_mapping[col] = "low"
            elif col_lower == "close":
                column_mapping[col] = "close"
            elif col_lower in ("volume", "vol", "tick_volume"):
                column_mapping[col] = "volume"

        return df.rename(column_mapping)

    def get_available_range(
        self,
        symbol: str,
        timeframe: Timeframe,
    ) -> tuple[datetime | None, datetime | None]:
        """利用可能なデータ範囲を取得

        Args:
            symbol: シンボル
            timeframe: 時間足

        Returns:
            tuple: (開始日時, 終了日時)、データがない場合は(None, None)
        """
        # Parquetファイルを検索
        parquet_pattern = f"{symbol}_{timeframe.value}*.parquet"
        parquet_files = list(self.data_dir.glob(parquet_pattern))

        if not parquet_files:
            # CSVファイルを検索
            csv_pattern = f"{symbol}_{timeframe.value}*.csv"
            csv_files = list(self.data_dir.glob(csv_pattern))

            if not csv_files:
                return None, None

            files = csv_files
        else:
            files = parquet_files

        # 最初と最後のファイルから範囲を取得
        min_time = None
        max_time = None

        for file_path in files:
            if file_path.suffix == ".parquet":
                df = pl.scan_parquet(file_path).select(
                    pl.col("time").min().alias("min_time"),
                    pl.col("time").max().alias("max_time"),
                ).collect()
            else:
                df = pl.read_csv(file_path, try_parse_dates=True)
                df = self._normalize_columns(df)
                df = df.select(
                    pl.col("time").min().alias("min_time"),
                    pl.col("time").max().alias("max_time"),
                )

            file_min = df["min_time"][0]
            file_max = df["max_time"][0]

            if min_time is None or file_min < min_time:
                min_time = file_min
            if max_time is None or file_max > max_time:
                max_time = file_max

        return min_time, max_time

    def load_csv(self, file_path: Path | str) -> pd.DataFrame | None:
        """単一CSVファイルを読み込み

        MT5形式（タブ区切り、<COLUMN>形式）にも対応。

        Args:
            file_path: CSVファイルパス

        Returns:
            pd.DataFrame | None: OHLCVデータ（失敗時None）
        """
        file_path = Path(file_path)
        if not file_path.exists():
            return None

        try:
            # まずヘッダーを確認
            with open(file_path, "r", encoding="utf-8") as f:
                first_line = f.readline()

            # MT5形式かどうかを判定
            if "<DATE>" in first_line or "<TIME>" in first_line:
                # MT5形式はload_mt5_csvを使用
                return self.load_mt5_csv(file_path)

            # 通常のCSV
            df = pl.read_csv(file_path, try_parse_dates=True)
            df = self._normalize_columns(df)

            if df.height > 0:
                return df.to_pandas()
            return None
        except Exception as e:
            logger.warning("CSVファイル読み込み失敗: %s - %s", file_path, e)
            return None

    @staticmethod
    def load_mt5_csv(file_path: Path | str) -> pd.DataFrame:
        """MT5形式のCSVファイルを読み込み

        MT5からエクスポートされたタブ区切りCSVを標準形式に変換。

        Args:
            file_path: CSVファイルパス

        Returns:
            pd.DataFrame: 正規化されたOHLCVデータ
        """
        df = pl.read_csv(file_path, separator="\t", has_header=True)
        columns = df.columns

        if "<TIME>" not in columns:
            # 日足形式（TIME列なし）
            df = df.rename({
                "<DATE>": "date",
                "<OPEN>": "open",
                "<HIGH>": "high",
                "<LOW>": "low",
                "<CLOSE>": "close",
                "<TICKVOL>": "volume",
            })
            df = df.with_columns(
                pl.col("date")
                .str.strptime(pl.Datetime, "%Y.%m.%d")
                .alias("time")
            )
        else:
            # 時間足形式（TIME列あり）
            df = df.rename({
                "<DATE>": "date",
                "<TIME>": "time_str",
                "<OPEN>": "open",
                "<HIGH>": "high",
                "<LOW>": "low",
                "<CLOSE>": "close",
                "<TICKVOL>": "volume",
            })
            df = df.with_columns(
                pl.concat_str([
                    pl.col("date"),
                    pl.lit(" "),
                    pl.col("time_str")
                ]).alias("datetime_str")
            )
            df = df.with_columns(
                pl.col("datetime_str")
                .str.strptime(pl.Datetime, "%Y.%m.%d %H:%M:%S")
                .alias("time")
            )

        df = df.select(["time", "open", "high", "low", "close", "volume"])
        return df.to_pandas()
