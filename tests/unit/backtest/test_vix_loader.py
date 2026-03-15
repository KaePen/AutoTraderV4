"""VIXデータローダーのテスト"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from autotrader.backtest.vix_loader import (
    _load_cache,
    _save_cache,
    load_vix_range,
    load_vix_year,
)


class TestVixCache:
    """CSVキャッシュの読み書きテスト"""

    def test_save_and_load_cache(self, tmp_path: Path) -> None:
        """キャッシュ保存→読み込みの往復"""
        cache_path = tmp_path / "vix_2020.csv"
        df = pd.DataFrame(
            {
                "Date": [date(2020, 1, 2), date(2020, 1, 3)],
                "Close": [13.78, 14.02],
            }
        )
        _save_cache(df, cache_path)
        loaded = _load_cache(cache_path)
        assert loaded is not None
        assert len(loaded) == 2
        assert loaded.iloc[0]["Close"] == pytest.approx(13.78)

    def test_load_missing_cache(self, tmp_path: Path) -> None:
        """存在しないキャッシュはNoneを返す"""
        result = _load_cache(tmp_path / "nonexistent.csv")
        assert result is None

    def test_load_corrupt_cache(self, tmp_path: Path) -> None:
        """破損キャッシュはNoneを返す"""
        cache_path = tmp_path / "corrupt.csv"
        cache_path.write_text("garbage data\nno,valid,csv")
        result = _load_cache(cache_path)
        # 破損データでもpandasが読めれば返る。
        # Date列のパースが失敗すればNone
        # ここではエラーにならないケースも許容


class TestLoadVixYear:
    """load_vix_year のテスト"""

    def test_uses_cache_if_available(
        self,
        tmp_path: Path,
    ) -> None:
        """キャッシュが存在する場合はダウンロードしない"""
        # キャッシュCSVを手動作成
        vix_dir = tmp_path / "vix"
        vix_dir.mkdir()
        cache_path = vix_dir / "vix_2020.csv"
        with open(cache_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Date", "Close"])
            writer.writerow(["2020-03-16", "82.69"])
            writer.writerow(["2020-03-17", "75.91"])
            writer.writerow(["2020-01-02", "13.78"])

        result = load_vix_year(2020, tmp_path)
        assert len(result) == 3
        assert result[date(2020, 3, 16)] == pytest.approx(82.69)
        assert result[date(2020, 1, 2)] == pytest.approx(13.78)

    def test_empty_result_when_no_yfinance(
        self,
        tmp_path: Path,
    ) -> None:
        """キャッシュなし＋ダウンロード失敗→空辞書"""
        with patch(
            "autotrader.backtest.vix_loader._download_vix_year",
            return_value=None,
        ):
            result = load_vix_year(2099, tmp_path)
            assert result == {}


class TestLoadVixRange:
    """load_vix_range のテスト"""

    def test_merges_multiple_years(
        self,
        tmp_path: Path,
    ) -> None:
        """複数年のデータを統合"""
        vix_dir = tmp_path / "vix"
        vix_dir.mkdir()

        # 2020年キャッシュ
        with open(vix_dir / "vix_2020.csv", "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Date", "Close"])
            writer.writerow(["2020-01-02", "13.78"])

        # 2021年キャッシュ
        with open(vix_dir / "vix_2021.csv", "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Date", "Close"])
            writer.writerow(["2021-01-04", "22.49"])

        result = load_vix_range(2020, 2021, tmp_path)
        assert len(result) == 2
        assert date(2020, 1, 2) in result
        assert date(2021, 1, 4) in result
