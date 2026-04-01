"""RSSニュース統合テスト

LiveTradingEngineにおけるRSS→LLM→FundamentalContextの
センチメントブレンドパイプラインをテストする。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autotrader.adapters.fundamental.schemas import (
    FundamentalContext,
)
from autotrader.live.engine import LiveTradingEngine


# ===== テスト用ダミーNewsItem =====


@dataclass
class _FakeNewsItem:
    """テスト用NewsItem代替"""

    news_id: str
    title: str
    currencies: list[str]
    published_at: datetime = None  # type: ignore[assignment]
    source_name: str = "test"
    source_url: str = "https://example.com"
    source_type: str = "rss"
    snippet: str | None = None
    content: str | None = None

    def __post_init__(self) -> None:
        if self.published_at is None:
            self.published_at = datetime.now(UTC)


def _make_engine_stub() -> LiveTradingEngine:
    """テスト用のEngine stub を作成

    __init__ をバイパスし、テストに必要な属性のみセットする。
    """
    engine = object.__new__(LiveTradingEngine)
    engine._active_symbol = "USDJPY"
    engine._news_buffer = []
    engine._rss_collector = None
    engine._news_analyzer = None
    # 共有コレクター（デフォルト: なし＝自前作成）
    engine._shared_fundamental_collector = None
    engine._shared_rss_collector = None
    engine._owns_collectors = True
    return engine


# ===== _blend_news_sentiment テスト =====


class TestBlendNewsSentiment:
    """_blend_news_sentiment ヘルパーのテスト"""

    def test_default_weight(self) -> None:
        """weight=0.15 で direction_bias がブレンドされる"""
        ctx = FundamentalContext(
            direction_bias=0.5,
            sentiment_score=0.0,
        )
        result = LiveTradingEngine._blend_news_sentiment(
            ctx, sentiment=1.0
        )
        # 0.5 * 0.85 + 1.0 * 0.15 = 0.575
        assert abs(result.direction_bias - 0.575) < 1e-9

    def test_zero_sentiment(self) -> None:
        """sentiment=0.0 でコンテキストほぼ不変"""
        ctx = FundamentalContext(
            direction_bias=0.4,
            sentiment_score=0.0,
        )
        result = LiveTradingEngine._blend_news_sentiment(
            ctx, sentiment=0.0
        )
        # 0.4 * 0.85 + 0.0 * 0.15 = 0.34
        assert abs(result.direction_bias - 0.34) < 1e-9
        assert result.sentiment_score == 0.0

    def test_updates_sentiment_score(self) -> None:
        """sentiment_score フィールドが更新される"""
        ctx = FundamentalContext(
            direction_bias=0.0,
            sentiment_score=0.0,
        )
        result = LiveTradingEngine._blend_news_sentiment(
            ctx, sentiment=-0.8
        )
        assert result.sentiment_score == -0.8

    def test_custom_weight(self) -> None:
        """カスタム weight でブレンドされる"""
        ctx = FundamentalContext(
            direction_bias=1.0,
            sentiment_score=0.0,
        )
        result = LiveTradingEngine._blend_news_sentiment(
            ctx, sentiment=-1.0, weight=0.5
        )
        # 1.0 * 0.5 + (-1.0) * 0.5 = 0.0
        assert abs(result.direction_bias) < 1e-9


# ===== _on_rss_news テスト =====


class TestOnRssNews:
    """_on_rss_news コールバックのテスト

    RSS/センチメントはATv5で再実装予定のため現在は無効化済み。
    _on_rss_news は何もしない（pass）ことを検証する。
    """

    def test_on_rss_news_is_noop(self) -> None:
        """_on_rss_news はバッファに蓄積しない（無効化済み）"""
        engine = _make_engine_stub()
        item = _FakeNewsItem(
            news_id="1",
            title="Fed rate",
            currencies=["USD"],
        )
        asyncio.get_event_loop().run_until_complete(
            engine._on_rss_news(item)
        )
        assert len(engine._news_buffer) == 0

    def test_get_news_for_symbol_filters(self) -> None:
        """get_news_for_symbol が正しくフィルタする"""
        engine = _make_engine_stub()
        usd_item = _FakeNewsItem(
            news_id="1",
            title="Fed rate",
            currencies=["USD"],
        )
        eur_item = _FakeNewsItem(
            news_id="2",
            title="ECB decision",
            currencies=["EUR"],
        )
        engine._news_buffer = [usd_item, eur_item]
        # USDJPYでフィルタ → USD関連のみ
        result = engine.get_news_for_symbol("USDJPY")
        assert len(result) == 1
        assert result[0] is usd_item
        # EURUSDでフィルタ → 両方含まれる
        result = engine.get_news_for_symbol("EURUSD")
        assert len(result) == 2


# ===== _init_fundamental RSS初期化テスト =====


class TestInitFundamentalRss:
    """_init_fundamental のRSS初期化テスト"""

    def test_rss_disabled(self) -> None:
        """use_rss_news=False で _rss_collector=None

        _init_fundamental のDB依存をbuiltins.__import__で
        モック化し、RSS分岐のみをテストする。
        """
        engine = _make_engine_stub()
        engine._fundamental_memory = None
        engine._fundamental_collector = None
        engine._morning_update_done_date = None

        from unittest.mock import MagicMock
        from autotrader.live.config import FundamentalConfig

        cfg = FundamentalConfig(
            enabled=True,
            use_rss_news=False,
        )

        # _init_fundamental 内の from ... import を全てモック化
        import builtins
        _real_import = builtins.__import__

        _mock_modules = {
            "autotrader.adapters.fundamental.memory",
            "autotrader.adapters.fundamental.collector",
            "autotrader.adapters.fundamental"
            ".deterministic_event_analyzer",
            "autotrader.adapters.database.connection",
            "autotrader.adapters.database",
        }

        def _fake_import(name, *args, **kwargs):
            if name in _mock_modules:
                mod = MagicMock()
                mod.get_session = lambda: None
                return mod
            return _real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_fake_import):
            engine._init_fundamental(cfg)

        assert engine._rss_collector is None
        assert engine._news_analyzer is None

    def test_rss_enabled(self) -> None:
        """use_rss_news=True で RSSCollector が初期化される

        RSS/NewsLLMAnalyzer のモッククラスが正しい引数で
        呼ばれ、engine属性にセットされることを検証する。
        """
        engine = _make_engine_stub()
        engine._fundamental_memory = None
        engine._fundamental_collector = None
        engine._morning_update_done_date = None

        from unittest.mock import MagicMock
        from autotrader.live.config import FundamentalConfig

        cfg = FundamentalConfig(
            enabled=True,
            use_rss_news=True,
            rss_poll_interval_minutes=10,
            rss_sentiment_ttl_hours=2,
        )

        mock_rss_cls = MagicMock()
        mock_llm_cls = MagicMock()

        import builtins
        _real_import = builtins.__import__

        _mock_modules = {
            "autotrader.adapters.fundamental.memory",
            "autotrader.adapters.fundamental.collector",
            "autotrader.adapters.fundamental"
            ".deterministic_event_analyzer",
            "autotrader.adapters.database.connection",
            "autotrader.adapters.database",
        }

        def _fake_import(name, *args, **kwargs):
            if name in _mock_modules:
                mod = MagicMock()
                mod.get_session = lambda: None
                return mod
            if name == (
                "autotrader.adapters.fundamental"
                ".rss_collector"
            ):
                mod = MagicMock()
                mod.RSSCollector = mock_rss_cls
                return mod
            if name == (
                "autotrader.adapters.fundamental"
                ".news_llm_analyzer"
            ):
                mod = MagicMock()
                mod.NewsLLMAnalyzer = mock_llm_cls
                return mod
            return _real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_fake_import):
            engine._init_fundamental(cfg)

        mock_rss_cls.assert_called_once_with(
            poll_interval=600,  # 10分 * 60秒
        )
        mock_llm_cls.assert_called_once_with(
            sentiment_ttl_hours=2,
        )
        assert engine._rss_collector is not None
        assert engine._news_analyzer is not None


# ===== 共有コレクター起動/停止テスト =====


class TestSharedCollectorStartStop:
    """共有コレクター使用時の起動/停止スキップテスト"""

    @pytest.mark.asyncio
    async def test_owns_collectors_trueで起動する(
        self,
    ) -> None:
        """_owns_collectors=Trueならコレクターを起動"""
        engine = _make_engine_stub()
        mock_fc = AsyncMock()
        mock_rss = AsyncMock()
        engine._fundamental_collector = mock_fc
        engine._rss_collector = mock_rss

        await engine._start_fundamental_tasks()

        mock_fc.start.assert_called_once()
        mock_rss.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_owns_collectors_falseで起動スキップ(
        self,
    ) -> None:
        """_owns_collectors=Falseなら起動しない"""
        engine = _make_engine_stub()
        engine._owns_collectors = False
        mock_fc = AsyncMock()
        mock_rss = AsyncMock()
        engine._fundamental_collector = mock_fc
        engine._rss_collector = mock_rss

        await engine._start_fundamental_tasks()

        mock_fc.start.assert_not_called()
        mock_rss.start.assert_not_called()

    @pytest.mark.asyncio
    async def test_owns_collectors_trueで停止する(
        self,
    ) -> None:
        """_owns_collectors=Trueならコレクターを停止"""
        engine = _make_engine_stub()
        mock_fc = AsyncMock()
        mock_rss = AsyncMock()
        engine._fundamental_collector = mock_fc
        engine._rss_collector = mock_rss

        await engine._stop_fundamental_tasks()

        mock_fc.stop.assert_called_once()
        mock_rss.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_owns_collectors_falseで停止スキップ(
        self,
    ) -> None:
        """_owns_collectors=Falseなら停止しない"""
        engine = _make_engine_stub()
        engine._owns_collectors = False
        mock_fc = AsyncMock()
        mock_rss = AsyncMock()
        engine._fundamental_collector = mock_fc
        engine._rss_collector = mock_rss

        await engine._stop_fundamental_tasks()

        mock_fc.stop.assert_not_called()
        mock_rss.stop.assert_not_called()
        # バッファはクリアされる
        assert engine._news_buffer == []

    def test_shared_collector使用時はコレクター再作成しない(
        self,
    ) -> None:
        """共有コレクター設定時は新規作成をスキップ"""
        engine = _make_engine_stub()
        engine._fundamental_memory = None
        engine._fundamental_collector = None
        engine._morning_update_done_date = None
        mock_shared_fc = MagicMock()
        engine._shared_fundamental_collector = (
            mock_shared_fc
        )

        from autotrader.live.config import FundamentalConfig

        cfg = FundamentalConfig(
            enabled=True,
            use_rss_news=False,
        )

        import builtins
        _real_import = builtins.__import__

        _mock_modules = {
            "autotrader.adapters.fundamental.memory",
            "autotrader.adapters.fundamental.collector",
            "autotrader.adapters.fundamental"
            ".deterministic_event_analyzer",
            "autotrader.adapters.database.connection",
            "autotrader.adapters.database",
        }

        def _fake_import(name, *args, **kwargs):
            if name in _mock_modules:
                mod = MagicMock()
                mod.get_session = lambda: None
                return mod
            return _real_import(name, *args, **kwargs)

        with patch(
            "builtins.__import__",
            side_effect=_fake_import,
        ):
            engine._init_fundamental(cfg)

        # 共有コレクターがそのまま使われる
        assert (
            engine._fundamental_collector
            is mock_shared_fc
        )
