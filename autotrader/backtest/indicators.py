"""インジケータ計算モジュール

マルチタイムフレームのインジケータ計算を担当。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from autotrader.calculator.technical.batch import TechnicalIndicatorBatch


class MultiTimeframeDataLoader:
    """マルチタイムフレームデータローダー

    複数時間足のデータをロードし、インジケータを計算。
    """

    def __init__(
        self,
        data_dir: str | Path,
        symbol: str = "USDJPY",
        indicator_calculator: TechnicalIndicatorBatch | None = None,
    ):
        """初期化

        Args:
            data_dir: データディレクトリ
            symbol: 通貨ペア
            indicator_calculator: インジケータ計算機（Noneでデフォルト使用）
        """
        from autotrader.backtest.data_loader import DataLoader

        self._data_dir = Path(data_dir)
        self._symbol = symbol
        self._loader = DataLoader(data_dir)
        self._calculator = indicator_calculator or TechnicalIndicatorBatch()

    def load_timeframes(
        self,
        timeframes: list[str],
        calculate_indicators: bool = True,
    ) -> dict[str, pd.DataFrame]:
        """指定時間足のデータをロード

        Args:
            timeframes: ロードする時間足リスト
            calculate_indicators: インジケータを計算するか

        Returns:
            dict[str, pd.DataFrame]: 時間足別データフレーム
        """
        data = {}

        for tf in timeframes:
            df = self._load_single_timeframe(tf)
            if df is not None:
                if calculate_indicators:
                    df = self._calculator.calculate_single(df)
                data[tf] = df

        return data

    def load_all_standard(
        self,
        include_short: bool = False,
        calculate_indicators: bool = True,
    ) -> dict[str, pd.DataFrame]:
        """標準時間足をすべてロード

        Args:
            include_short: M1/M5を含めるか
            calculate_indicators: インジケータを計算するか

        Returns:
            dict[str, pd.DataFrame]: 時間足別データフレーム
        """
        if include_short:
            timeframes = ["M1", "M5", "M15", "H1", "H4", "D1"]
        else:
            timeframes = ["M15", "H1", "H4", "D1"]

        return self.load_timeframes(timeframes, calculate_indicators)

    def _load_single_timeframe(self, tf: str) -> pd.DataFrame | None:
        """単一時間足をロード

        Args:
            tf: 時間足

        Returns:
            pd.DataFrame | None: データフレーム（見つからない場合None）
        """
        # ワイルドカードでファイル検索
        pattern = f"{self._symbol}_{tf}*.csv"
        tf_files = list(self._data_dir.glob(pattern))

        if tf_files:
            # 最初に見つかったファイルを使用
            tf_path = sorted(tf_files)[0]
            return self._loader.load_csv(tf_path)

        # 完全一致も試行（後方互換性）
        tf_path = self._data_dir / f"{self._symbol}_{tf}.csv"
        if tf_path.exists():
            return self._loader.load_csv(tf_path)

        return None
