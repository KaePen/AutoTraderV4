"""ニュースLLM統合テスト

BacktestFundamentalProviderのニュースLLMデータ読み込み・
コンテキストマージ・メモリ更新のテスト。
"""

from __future__ import annotations

import csv
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from autotrader.adapters.fundamental.backtest_provider import (
    BacktestFundamentalProvider,
    NewsLLMRecord,
)
from autotrader.adapters.fundamental.schemas import (
    FundamentalContext,
    FundamentalMemory,
)


def _make_news_csv(rows: list[dict]) -> Path:
    """テスト用ニュースLLM CSVを生成"""
    fieldnames = [
        "date", "article_count", "sentiment_score",
        "sentiment_confidence", "macro_bias_score",
        "policy_divergence_score", "risk_appetite_score",
        "geopolitical_risk_level", "dominant_theme",
        "summary",
    ]
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv",
        delete=False, encoding="utf-8",
    ) as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        return Path(f.name)


def _sample_row(
    d: str = "2024-01-15",
    sentiment: float = 0.3,
    confidence: float = 0.8,
    macro_bias: float = 0.4,
    policy_div: float = 0.2,
    risk_app: float = 0.1,
    geo_risk: int = 1,
    theme: str = "monetary_policy",
) -> dict:
    """サンプル行を作成"""
    return {
        "date": d,
        "article_count": "15",
        "sentiment_score": str(sentiment),
        "sentiment_confidence": str(confidence),
        "macro_bias_score": str(macro_bias),
        "policy_divergence_score": str(policy_div),
        "risk_appetite_score": str(risk_app),
        "geopolitical_risk_level": str(geo_risk),
        "dominant_theme": theme,
        "summary": "テストサマリー",
    }


class TestNewsLLMRecord:
    """NewsLLMRecord データクラスのテスト"""

    def test_basic_creation(self):
        """基本的な生成"""
        rec = NewsLLMRecord(
            record_date=date(2024, 1, 15),
            article_count=15,
            sentiment_score=0.3,
            sentiment_confidence=0.8,
            macro_bias_score=0.4,
            policy_divergence_score=0.2,
            risk_appetite_score=0.1,
            geopolitical_risk_level=1,
            dominant_theme="monetary_policy",
        )
        assert rec.record_date == date(2024, 1, 15)
        assert rec.sentiment_score == 0.3
        assert rec.macro_bias_score == 0.4

    def test_frozen(self):
        """frozen属性テスト"""
        rec = NewsLLMRecord(
            record_date=date(2024, 1, 15),
            article_count=15,
            sentiment_score=0.3,
            sentiment_confidence=0.8,
            macro_bias_score=0.4,
            policy_divergence_score=0.2,
            risk_appetite_score=0.1,
            geopolitical_risk_level=1,
            dominant_theme="test",
        )
        with pytest.raises(AttributeError):
            rec.sentiment_score = 0.9  # type: ignore


class TestParseNewsLLMRow:
    """_parse_news_llm_row のテスト"""

    def test_valid_row(self):
        """正常な行のパース"""
        row = _sample_row()
        rec = BacktestFundamentalProvider._parse_news_llm_row(
            row,
        )
        assert rec is not None
        assert rec.record_date == date(2024, 1, 15)
        assert rec.article_count == 15
        assert rec.sentiment_score == pytest.approx(0.3)
        assert rec.sentiment_confidence == pytest.approx(0.8)
        assert rec.macro_bias_score == pytest.approx(0.4)
        assert rec.geopolitical_risk_level == 1
        assert rec.dominant_theme == "monetary_policy"

    def test_missing_date_returns_none(self):
        """日付なしの行はNone"""
        row = _sample_row()
        row["date"] = ""
        assert (
            BacktestFundamentalProvider._parse_news_llm_row(row)
            is None
        )

    def test_invalid_date_returns_none(self):
        """不正な日付はNone"""
        row = _sample_row()
        row["date"] = "not-a-date"
        assert (
            BacktestFundamentalProvider._parse_news_llm_row(row)
            is None
        )

    def test_clamping_sentiment(self):
        """センチメントは-1~+1にクランプ"""
        row = _sample_row(sentiment=2.5)
        rec = BacktestFundamentalProvider._parse_news_llm_row(
            row,
        )
        assert rec is not None
        assert rec.sentiment_score == 1.0

    def test_clamping_negative(self):
        """負の範囲外もクランプ"""
        row = _sample_row(macro_bias=-3.0)
        rec = BacktestFundamentalProvider._parse_news_llm_row(
            row,
        )
        assert rec is not None
        assert rec.macro_bias_score == -1.0

    def test_confidence_clamped_0_1(self):
        """信頼度は0~1にクランプ"""
        row = _sample_row(confidence=1.5)
        rec = BacktestFundamentalProvider._parse_news_llm_row(
            row,
        )
        assert rec is not None
        assert rec.sentiment_confidence == 1.0

    def test_geo_risk_clamped_0_3(self):
        """地政学リスクは0~3にクランプ"""
        row = _sample_row(geo_risk=5)
        rec = BacktestFundamentalProvider._parse_news_llm_row(
            row,
        )
        assert rec is not None
        assert rec.geopolitical_risk_level == 3

    def test_empty_numeric_fields_default(self):
        """空の数値フィールドはデフォルト値"""
        row = {
            "date": "2024-01-15",
            "article_count": "",
            "sentiment_score": "",
            "sentiment_confidence": "",
            "macro_bias_score": "",
            "policy_divergence_score": "",
            "risk_appetite_score": "",
            "geopolitical_risk_level": "",
            "dominant_theme": "",
        }
        rec = BacktestFundamentalProvider._parse_news_llm_row(
            row,
        )
        assert rec is not None
        assert rec.article_count == 0
        assert rec.sentiment_score == 0.0
        assert rec.sentiment_confidence == 0.5  # デフォルト0.5
        assert rec.macro_bias_score == 0.0


class TestLoadNewsLLMCsv:
    """load_news_llm_csv のテスト"""

    @pytest.fixture
    def provider(self):
        return BacktestFundamentalProvider(
            event_guard_minutes=30,
        )

    def test_load_basic(self, provider):
        """基本的な読み込み"""
        csv_path = _make_news_csv([
            _sample_row("2024-01-15"),
            _sample_row("2024-01-16"),
            _sample_row("2024-01-17"),
        ])
        count = provider.load_news_llm_csv(csv_path, "USDJPY")
        assert count == 3

    def test_load_nonexistent_file(self, provider):
        """存在しないファイルは0を返す"""
        count = provider.load_news_llm_csv(
            "/nonexistent/path.csv", "USDJPY",
        )
        assert count == 0

    def test_date_deduplication(self, provider):
        """同一日付は後勝ち"""
        csv_path = _make_news_csv([
            _sample_row("2024-01-15", sentiment=0.2),
            _sample_row("2024-01-15", sentiment=0.8),
        ])
        count = provider.load_news_llm_csv(csv_path, "USDJPY")
        # 2行読み込むが同一日付で上書き
        assert count == 2
        rec = provider._news_llm_by_date["USDJPY"][
            date(2024, 1, 15)
        ]
        assert rec.sentiment_score == pytest.approx(0.8)

    def test_multiple_files(self, provider):
        """複数ファイル読み込みでマージ"""
        csv1 = _make_news_csv([_sample_row("2024-01-15")])
        csv2 = _make_news_csv([_sample_row("2024-02-15")])
        c1 = provider.load_news_llm_csv(csv1, "USDJPY")
        c2 = provider.load_news_llm_csv(csv2, "USDJPY")
        assert c1 == 1
        assert c2 == 1
        assert len(provider._news_llm_by_date["USDJPY"]) == 2


class TestMergeNewsIntoContext:
    """_merge_news_into_context のテスト"""

    @pytest.fixture
    def provider(self):
        return BacktestFundamentalProvider(
            event_guard_minutes=30,
        )

    def test_no_news_returns_original(self, provider):
        """ニュースデータなしなら元のコンテキストを返す"""
        ctx = FundamentalContext.neutral()
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        result = provider._merge_news_into_context(
            ctx, now, "USDJPY",
        )
        assert result is ctx  # 同一オブジェクト

    def test_merge_updates_fields(self, provider):
        """ニュースデータでセンチメントとマクロバイアスが更新"""
        csv_path = _make_news_csv([
            _sample_row(
                "2024-01-15",
                sentiment=0.6,
                macro_bias=0.7,
            ),
        ])
        provider.load_news_llm_csv(csv_path, "USDJPY")

        ctx = FundamentalContext.neutral()
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        result = provider._merge_news_into_context(
            ctx, now, "USDJPY",
        )

        # ニュースで上書きされたフィールド
        assert result.sentiment_score == pytest.approx(0.6)
        assert result.macro_bias_score == pytest.approx(0.7)
        # direction_biasにニュースバイアスがブレンド
        # neutral ctx.direction_bias=0.0, news=0.7, w=0.15
        # → 0.0*0.85 + 0.7*0.15 = 0.105
        expected_bias = 0.0 * 0.85 + 0.7 * 0.15
        assert result.direction_bias == pytest.approx(
            expected_bias,
        )

    def test_merge_wrong_date_no_change(self, provider):
        """日付が合わなければ変更なし"""
        csv_path = _make_news_csv([
            _sample_row("2024-01-16", sentiment=0.9),
        ])
        provider.load_news_llm_csv(csv_path, "USDJPY")

        ctx = FundamentalContext.neutral()
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        result = provider._merge_news_into_context(
            ctx, now, "USDJPY",
        )
        assert result is ctx

    def test_frozen_context_not_mutated(self, provider):
        """元のコンテキストは変更されない（frozen）"""
        csv_path = _make_news_csv([
            _sample_row("2024-01-15", sentiment=0.5),
        ])
        provider.load_news_llm_csv(csv_path, "USDJPY")

        ctx = FundamentalContext.neutral()
        original_sentiment = ctx.sentiment_score
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        provider._merge_news_into_context(
            ctx, now, "USDJPY",
        )
        # 元は変わらない
        assert ctx.sentiment_score == original_sentiment


class TestUpdateMemoryWithNews:
    """_update_memory のニュース更新テスト"""

    @pytest.fixture
    def provider(self):
        p = BacktestFundamentalProvider(event_guard_minutes=30)
        p.enable_memory()
        return p

    def test_news_updates_memory(self, provider):
        """ニュースデータがメモリのnews_biasを更新"""
        csv_path = _make_news_csv([
            _sample_row(
                "2024-01-15",
                macro_bias=0.5,
                confidence=0.8,
            ),
        ])
        provider.load_news_llm_csv(csv_path, "USDJPY")

        ctx = FundamentalContext.neutral()
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        provider._update_memory(ctx, now, "USDJPY")

        assert provider.memory is not None
        assert provider.memory.news_bias != 0.0
        assert provider.memory.last_news_date == date(2024, 1, 15)

    def test_news_once_per_day(self, provider):
        """同日の2回目呼び出しではニュース更新しない"""
        csv_path = _make_news_csv([
            _sample_row(
                "2024-01-15",
                macro_bias=0.5,
                confidence=0.8,
            ),
        ])
        provider.load_news_llm_csv(csv_path, "USDJPY")

        ctx = FundamentalContext.neutral()
        t1 = datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)
        t2 = datetime(2024, 1, 15, 14, 0, tzinfo=timezone.utc)

        provider._update_memory(ctx, t1, "USDJPY")
        bias_after_first = provider.memory.news_bias

        provider._update_memory(ctx, t2, "USDJPY")
        bias_after_second = provider.memory.news_bias

        # EMA更新は1日1回なので値は変わらない
        assert bias_after_first == bias_after_second

    def test_no_memory_no_error(self):
        """メモリ無効時はエラーなし"""
        provider = BacktestFundamentalProvider(
            event_guard_minutes=30,
        )
        # enable_memory() を呼ばない
        csv_path = _make_news_csv([
            _sample_row("2024-01-15"),
        ])
        provider.load_news_llm_csv(csv_path, "USDJPY")
        ctx = FundamentalContext.neutral()
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        # エラーが発生しないことを確認
        provider._update_memory(ctx, now, "USDJPY")

    def test_composite_bias_includes_news(self, provider):
        """composite_biasはイベント+ニュースの加重平均"""
        csv_path = _make_news_csv([
            _sample_row(
                "2024-01-15",
                macro_bias=0.8,
                confidence=0.9,
            ),
        ])
        provider.load_news_llm_csv(csv_path, "USDJPY")

        ctx = FundamentalContext.neutral()
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        provider._update_memory(ctx, now, "USDJPY")

        assert provider.memory is not None
        # ニュースバイアスが入ったのでcomposite_biasも非ゼロ
        assert provider.memory.composite_bias != 0.0
