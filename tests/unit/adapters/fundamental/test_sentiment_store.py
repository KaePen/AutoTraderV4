"""SentimentStore のユニットテスト"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from autotrader.adapters.fundamental.sentiment_store import (
    SentimentRecord,
    SentimentStore,
)


class TestSentimentStore:
    """SentimentStore テスト"""

    @pytest.fixture
    def store(self, tmp_path):
        return SentimentStore(
            data_dir=str(tmp_path),
            rotation_days=7,
        )

    @pytest.fixture
    def sample_record(self):
        return SentimentRecord(
            timestamp=datetime.now(
                timezone.utc,
            ).isoformat(),
            score=0.35,
            method="keyword",
            confidence=0.6,
            news_count=5,
            top_headlines=[
                "Fed hawkish stance",
                "Dollar rises",
            ],
        )

    def test_save_and_load_latest(
        self, store, sample_record
    ):
        """保存して最新レコードを読み込み"""
        store.save("USDJPY", sample_record)
        loaded = store.load_latest("USDJPY")
        assert loaded is not None
        assert loaded.score == 0.35
        assert loaded.method == "keyword"
        assert loaded.news_count == 5

    def test_load_latest_no_file(self, store):
        """ファイルがない場合はNone"""
        assert store.load_latest("USDJPY") is None

    def test_multiple_saves(self, store):
        """複数回保存で最新が取得される"""
        rec1 = SentimentRecord(
            timestamp=datetime.now(
                timezone.utc,
            ).isoformat(),
            score=0.1,
            method="keyword",
            confidence=0.5,
            news_count=3,
            top_headlines=["headline1"],
        )
        rec2 = SentimentRecord(
            timestamp=datetime.now(
                timezone.utc,
            ).isoformat(),
            score=0.8,
            method="llm",
            confidence=0.9,
            news_count=10,
            top_headlines=["headline2"],
        )
        store.save("USDJPY", rec1)
        store.save("USDJPY", rec2)
        loaded = store.load_latest("USDJPY")
        assert loaded is not None
        assert loaded.score == 0.8

    def test_load_history(self, store):
        """過去レコードの時間フィルタリング"""
        now = datetime.now(timezone.utc)
        old = SentimentRecord(
            timestamp=(
                now - timedelta(hours=48)
            ).isoformat(),
            score=0.1,
            method="keyword",
            confidence=0.5,
            news_count=2,
            top_headlines=[],
        )
        recent = SentimentRecord(
            timestamp=now.isoformat(),
            score=0.5,
            method="keyword",
            confidence=0.7,
            news_count=5,
            top_headlines=[],
        )
        store.save("USDJPY", old)
        store.save("USDJPY", recent)
        history = store.load_history(
            "USDJPY", hours=24
        )
        assert len(history) == 1
        assert history[0].score == 0.5

    def test_rotation(self, store, tmp_path):
        """古いレコードのローテーション"""
        now = datetime.now(timezone.utc)
        old = SentimentRecord(
            timestamp=(
                now - timedelta(days=10)
            ).isoformat(),
            score=0.1,
            method="keyword",
            confidence=0.5,
            news_count=1,
            top_headlines=[],
        )
        recent = SentimentRecord(
            timestamp=now.isoformat(),
            score=0.5,
            method="keyword",
            confidence=0.7,
            news_count=3,
            top_headlines=[],
        )
        store.save("USDJPY", old)
        store.save("USDJPY", recent)
        # rotation_days=7 なので 10日前のは消える
        path = (
            tmp_path / "USDJPY" / "sentiment.jsonl"
        )
        with open(path, "r") as f:
            lines = [
                line.strip()
                for line in f
                if line.strip()
            ]
        assert len(lines) == 1  # recent のみ残る

    def test_different_symbols(self, store):
        """異なるシンボルは別ファイルに保存"""
        rec1 = SentimentRecord(
            timestamp=datetime.now(
                timezone.utc,
            ).isoformat(),
            score=0.3,
            method="keyword",
            confidence=0.5,
            news_count=3,
            top_headlines=[],
        )
        rec2 = SentimentRecord(
            timestamp=datetime.now(
                timezone.utc,
            ).isoformat(),
            score=-0.5,
            method="keyword",
            confidence=0.6,
            news_count=4,
            top_headlines=[],
        )
        store.save("USDJPY", rec1)
        store.save("EURUSD", rec2)
        assert (
            store.load_latest("USDJPY").score == 0.3
        )
        assert (
            store.load_latest("EURUSD").score == -0.5
        )

    def test_jsonl_format(self, store, tmp_path):
        """JSONL形式で保存されていること"""
        rec = SentimentRecord(
            timestamp=datetime.now(
                timezone.utc,
            ).isoformat(),
            score=0.5,
            method="keyword",
            confidence=0.7,
            news_count=5,
            top_headlines=["test"],
        )
        store.save("USDJPY", rec)
        path = (
            tmp_path / "USDJPY" / "sentiment.jsonl"
        )
        with open(path, "r") as f:
            line = f.readline().strip()
        data = json.loads(line)
        assert data["score"] == 0.5
        assert data["method"] == "keyword"
