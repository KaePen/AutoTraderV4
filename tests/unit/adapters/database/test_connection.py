"""connection.py テスト"""

from __future__ import annotations

import pytest

from autotrader.adapters.database.connection import (
    get_engine,
    get_session,
    _engine_cache,
)


class TestGetEngine:
    """get_engine テスト"""

    def setup_method(self) -> None:
        """テスト間でキャッシュをクリア"""
        _engine_cache.clear()

    def test_sqlite_engine(self) -> None:
        """SQLiteエンジン生成"""
        engine = get_engine("sqlite:///:memory:")
        assert engine is not None
        assert "sqlite" in str(engine.url)

    def test_engine_cache(self) -> None:
        """同一URLでキャッシュされる"""
        url = "sqlite:///:memory:"
        e1 = get_engine(url)
        e2 = get_engine(url)
        assert e1 is e2

    def test_requires_url_argument(self) -> None:
        """引数なし呼び出しはTypeError"""
        with pytest.raises(TypeError):
            get_engine()  # type: ignore[call-arg]


class TestGetSession:
    """get_session テスト"""

    def test_requires_url_argument(self) -> None:
        """引数なし呼び出しはTypeError"""
        with pytest.raises(TypeError):
            with get_session():  # type: ignore[call-arg]
                pass

    def test_session_context_manager(self) -> None:
        """セッションコンテキストマネージャ動作"""
        with get_session("sqlite:///:memory:") as session:
            assert session is not None
