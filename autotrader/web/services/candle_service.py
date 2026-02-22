"""ローソク足データサービス

CSVファイルからローソク足データを読み込む。
"""

from __future__ import annotations

import logging
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import polars as pl

from autotrader.core.enums import Timeframe
from autotrader.web.schemas.responses import CandleResponse

logger = logging.getLogger(__name__)

# タイムフレーム別CSVファイル名パターン
_TF_FILE_MAP: dict[str, str] = {
    "M1": "M1",
    "M5": "M5",
    "M15": "M15",
    "M30": "M30",
    "H1": "H1",
    "H4": "H4",
    "D1": "D1",
    "W1": "W1",
}


class CandleService:
    """ローソク足データサービス

    CSVファイルからPolarsで高速読込し、LRUキャッシュで保持。
    """

    def __init__(self, data_dir: str = "data/csv") -> None:
        """初期化

        Args:
            data_dir: CSVデータディレクトリ
        """
        self._data_dir = Path(data_dir)

    def get_candles(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 200,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[CandleResponse]:
        """ローソク足データを取得

        Args:
            symbol: 通貨ペア
            timeframe: 時間足
            limit: 取得本数
            start_time: 開始時刻
            end_time: 終了時刻

        Returns:
            list[CandleResponse]: ローソク足一覧
        """
        df = self._load_candle_data(symbol, timeframe)
        if df is None or df.is_empty():
            return []

        # 時間フィルター
        if start_time is not None:
            df = df.filter(pl.col("time") >= start_time)
        if end_time is not None:
            df = df.filter(pl.col("time") <= end_time)

        # 最新からlimit件
        df = df.sort("time", descending=True).head(limit)
        df = df.sort("time")

        tf_enum = Timeframe(timeframe)
        return [
            CandleResponse(
                symbol=symbol,
                timeframe=tf_enum,
                time=row["time"],
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=row.get("volume", 0.0) or 0.0,
            )
            for row in df.to_dicts()
        ]

    def _load_candle_data(
        self, symbol: str, timeframe: str
    ) -> pl.DataFrame | None:
        """CSVからローソク足データを読込

        Args:
            symbol: 通貨ペア
            timeframe: 時間足

        Returns:
            pl.DataFrame | None: データフレーム
        """
        tf_dir = _TF_FILE_MAP.get(timeframe, timeframe)
        csv_dir = self._data_dir / symbol / tf_dir

        if not csv_dir.exists():
            # 別パターン: data/csv/USDJPY_M15.csv
            alt_path = self._data_dir / f"{symbol}_{timeframe}.csv"
            if alt_path.exists():
                return self._read_csv(alt_path)
            logger.warning(
                "ローソク足データなし: %s/%s", symbol, timeframe
            )
            return None

        # ディレクトリ内の全CSVを結合
        csv_files = sorted(csv_dir.glob("*.csv"))
        if not csv_files:
            return None

        dfs = []
        for f in csv_files:
            df = self._read_csv(f)
            if df is not None:
                dfs.append(df)

        if not dfs:
            return None

        return pl.concat(dfs).sort("time").unique(
            subset=["time"], keep="last"
        )

    @staticmethod
    def _read_csv(path: Path) -> pl.DataFrame | None:
        """CSV読込

        Args:
            path: CSVファイルパス

        Returns:
            pl.DataFrame | None: データフレーム
        """
        try:
            df = pl.read_csv(
                path,
                try_parse_dates=True,
                infer_schema_length=1000,
            )

            # カラム名正規化
            col_map = {}
            for col in df.columns:
                lower = col.lower().strip()
                if lower in ("time", "date", "datetime", "timestamp"):
                    col_map[col] = "time"
                elif lower == "open":
                    col_map[col] = "open"
                elif lower == "high":
                    col_map[col] = "high"
                elif lower == "low":
                    col_map[col] = "low"
                elif lower == "close":
                    col_map[col] = "close"
                elif lower in ("volume", "vol", "tick_volume"):
                    col_map[col] = "volume"

            if col_map:
                df = df.rename(col_map)

            required = {"time", "open", "high", "low", "close"}
            if not required.issubset(set(df.columns)):
                return None

            # time列をdatetime変換
            if df["time"].dtype != pl.Datetime:
                df = df.with_columns(
                    pl.col("time").str.to_datetime(
                        strict=False
                    )
                )

            return df.select(
                ["time", "open", "high", "low", "close"]
                + (["volume"] if "volume" in df.columns else [])
            )
        except Exception as e:
            logger.warning("CSV読込失敗 %s: %s", path, e)
            return None
