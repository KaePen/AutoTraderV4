"""news_csv_writer のユニットテスト"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from autotrader.adapters.fundamental.news_csv_writer import (
    read_news_csv,
    write_news_csv,
)
from autotrader.adapters.fundamental.news_schemas import (
    NewsItem,
    NewsSource,
)


def _make_item(
    idx: int,
    source_type: NewsSource = NewsSource.GDELT,
) -> NewsItem:
    """テスト用NewsItemを生成"""
    return NewsItem(
        news_id=f"gdelt_{idx:012d}",
        published_at=datetime(
            2024, 1, idx + 1, 12, 0, 0, tzinfo=timezone.utc
        ),
        title=f"Test news title {idx}",
        source_name="reuters.com",
        source_url=f"https://reuters.com/{idx}",
        currencies=["USD", "JPY"],
        source_type=source_type,
        snippet=f"Snippet text {idx}",
    )


class TestWriteNewsCsv:
    """write_news_csv のテスト"""

    def test_ファイル作成(self, tmp_path: Path) -> None:
        """ファイルが存在しない場合に新規作成する"""
        items = [_make_item(0), _make_item(1)]
        out_path = tmp_path / "news_2024.csv"

        write_news_csv(items, out_path)

        assert out_path.exists()

    def test_ヘッダー確認(self, tmp_path: Path) -> None:
        """CSVにヘッダー行が含まれる"""
        items = [_make_item(0)]
        out_path = tmp_path / "news.csv"

        write_news_csv(items, out_path)

        content = out_path.read_text(encoding="utf-8")
        assert "news_id" in content
        assert "published_at" in content
        assert "currencies" in content

    def test_件数確認(self, tmp_path: Path) -> None:
        """書き込んだ件数分の行が存在する"""
        n = 5
        items = [_make_item(i) for i in range(n)]
        out_path = tmp_path / "news.csv"

        write_news_csv(items, out_path)

        # ヘッダー + n行
        lines = [
            l for l in
            out_path.read_text(encoding="utf-8").splitlines()
            if l
        ]
        assert len(lines) == n + 1

    def test_追記モード(self, tmp_path: Path) -> None:
        """append=Trueで既存ファイルに追記する"""
        out_path = tmp_path / "news.csv"
        write_news_csv([_make_item(0)], out_path)
        write_news_csv([_make_item(1)], out_path, append=True)

        lines = [
            l for l in
            out_path.read_text(encoding="utf-8").splitlines()
            if l
        ]
        # ヘッダー1行 + 2件
        assert len(lines) == 3

    def test_上書きモード(self, tmp_path: Path) -> None:
        """append=False（デフォルト）で上書き"""
        out_path = tmp_path / "news.csv"
        write_news_csv([_make_item(0), _make_item(1)], out_path)
        write_news_csv([_make_item(2)], out_path, append=False)

        lines = [
            l for l in
            out_path.read_text(encoding="utf-8").splitlines()
            if l
        ]
        # 上書きされて1件のみ
        assert len(lines) == 2

    def test_空リストは空ファイル(
        self, tmp_path: Path
    ) -> None:
        """空リストを書き込むとヘッダーのみのファイルが作成"""
        out_path = tmp_path / "news.csv"
        write_news_csv([], out_path)

        lines = [
            l for l in
            out_path.read_text(encoding="utf-8").splitlines()
            if l
        ]
        assert len(lines) == 1  # ヘッダーのみ

    def test_親ディレクトリ自動作成(
        self, tmp_path: Path
    ) -> None:
        """存在しない親ディレクトリを自動作成"""
        out_path = tmp_path / "sub" / "dir" / "news.csv"
        write_news_csv([_make_item(0)], out_path)
        assert out_path.exists()


class TestReadNewsCsv:
    """read_news_csv のテスト"""

    def test_存在しないファイルは空リスト(
        self, tmp_path: Path
    ) -> None:
        """存在しないCSVは空リストを返す"""
        result = read_news_csv(tmp_path / "notexist.csv")
        assert result == []

    def test_ラウンドトリップ(self, tmp_path: Path) -> None:
        """書き込んだデータを正確に読み込める"""
        items = [_make_item(i) for i in range(3)]
        out_path = tmp_path / "news.csv"

        write_news_csv(items, out_path)
        loaded = read_news_csv(out_path)

        assert len(loaded) == 3
        for original, loaded_item in zip(items, loaded):
            assert loaded_item.news_id == original.news_id
            assert loaded_item.title == original.title
            assert (
                loaded_item.published_at == original.published_at
            )
            assert (
                loaded_item.currencies == original.currencies
            )
            assert (
                loaded_item.source_type == original.source_type
            )

    def test_通貨リストをJSONで保存復元(
        self, tmp_path: Path
    ) -> None:
        """通貨リストがJSONとして正確に保存・復元される"""
        item = NewsItem(
            news_id="test_001",
            published_at=datetime(
                2024, 1, 1, tzinfo=timezone.utc
            ),
            title="Test",
            source_name="reuters.com",
            source_url="https://reuters.com/1",
            currencies=["USD", "EUR", "GBP"],
            source_type=NewsSource.GDELT,
        )
        out_path = tmp_path / "news.csv"

        write_news_csv([item], out_path)
        loaded = read_news_csv(out_path)

        assert loaded[0].currencies == ["USD", "EUR", "GBP"]

    def test_snippetNoneは正常に復元(
        self, tmp_path: Path
    ) -> None:
        """snippet=NoneのアイテムもNoneとして復元"""
        item = NewsItem(
            news_id="test_002",
            published_at=datetime(
                2024, 1, 1, tzinfo=timezone.utc
            ),
            title="No snippet test",
            source_name="bloomberg.com",
            source_url="https://bloomberg.com/1",
            currencies=["USD"],
            source_type=NewsSource.RSS,
            snippet=None,
        )
        out_path = tmp_path / "news.csv"

        write_news_csv([item], out_path)
        loaded = read_news_csv(out_path)

        assert loaded[0].snippet is None

    def test_RSSソースタイプを正確に保存復元(
        self, tmp_path: Path
    ) -> None:
        """NewsSource.RSS のソースタイプが正確に保存・復元"""
        items = [
            _make_item(0, NewsSource.GDELT),
            _make_item(1, NewsSource.RSS),
        ]
        out_path = tmp_path / "news.csv"

        write_news_csv(items, out_path)
        loaded = read_news_csv(out_path)

        assert loaded[0].source_type == NewsSource.GDELT
        assert loaded[1].source_type == NewsSource.RSS

    def test_不正行はスキップ(self, tmp_path: Path) -> None:
        """不正なCSV行は無視してスキップ"""
        out_path = tmp_path / "news.csv"
        # 不正なCSVを手動作成
        content = (
            "news_id,published_at,title,source_name,"
            "source_url,currencies,source_type,snippet\n"
            "valid_001,2024-01-01T00:00:00+00:00,"
            "Valid news,reuters.com,"
            "https://reuters.com/1,"
            '["USD"],gdelt,\n'
            # news_idなし
            ",invalid_date,Bad row,,,,,\n"
        )
        out_path.write_text(content, encoding="utf-8")

        loaded = read_news_csv(out_path)
        # 有効な1行のみ
        assert len(loaded) == 1
        assert loaded[0].news_id == "valid_001"
