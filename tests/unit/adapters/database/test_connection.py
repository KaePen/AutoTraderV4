"""connection.py テスト"""

from __future__ import annotations

import pytest

from autotrader.adapters.database.connection import (
    _engine_cache,
    _factory_cache,
    dispose_all_engines,
    dispose_engine,
    get_engine,
    get_session,
)


class TestGetEngine:
    """get_engine テスト"""

    def setup_method(self) -> None:
        """テスト間でキャッシュをクリア"""
        _engine_cache.clear()
        _factory_cache.clear()

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

    def setup_method(self) -> None:
        """テスト間でキャッシュをクリア"""
        _engine_cache.clear()
        _factory_cache.clear()

    def test_requires_url_argument(self) -> None:
        """引数なし呼び出しはTypeError"""
        with pytest.raises(TypeError):
            with get_session():  # type: ignore[call-arg]
                pass

    def test_session_context_manager(self) -> None:
        """セッションコンテキストマネージャ動作"""
        with get_session("sqlite:///:memory:") as session:
            assert session is not None


class TestDisposeEngine:
    """dispose_engine テスト"""

    def setup_method(self) -> None:
        """テスト間でキャッシュをクリア"""
        _engine_cache.clear()
        _factory_cache.clear()

    def test_dispose_removes_from_cache(self) -> None:
        """dispose_engineでキャッシュから削除される"""
        url = "sqlite:///test_dispose.db"
        engine = get_engine(url)
        assert url in _engine_cache

        # ファクトリも生成してキャッシュに載せる
        from autotrader.adapters.database.connection import (
            get_session_factory,
        )

        get_session_factory(engine)
        assert id(engine) in _factory_cache

        dispose_engine(url)

        assert url not in _engine_cache
        # ファクトリキャッシュも削除される
        assert id(engine) not in _factory_cache

    def test_dispose_unknown_url_noop(self) -> None:
        """存在しないURLのdisposeはエラーにならない"""
        # 例外が発生しないことを確認
        dispose_engine("sqlite:///nonexistent.db")

    def test_after_dispose_creates_new_engine(
        self,
    ) -> None:
        """dispose後にget_engineで新しいエンジンが生成される"""
        url = "sqlite:///test_recreate.db"
        e1 = get_engine(url)
        dispose_engine(url)
        e2 = get_engine(url)
        # 新しいエンジンが生成される（同一オブジェクトではない）
        assert e1 is not e2


class TestDisposeAllEngines:
    """dispose_all_engines テスト"""

    def setup_method(self) -> None:
        """テスト間でキャッシュをクリア"""
        _engine_cache.clear()
        _factory_cache.clear()

    def test_dispose_all_clears_caches(self) -> None:
        """dispose_all_enginesで全キャッシュがクリアされる"""
        url1 = "sqlite:///test_all_1.db"
        url2 = "sqlite:///test_all_2.db"
        get_engine(url1)
        get_engine(url2)

        assert len(_engine_cache) == 2

        dispose_all_engines()

        assert len(_engine_cache) == 0
        assert len(_factory_cache) == 0

    def test_after_dispose_all_creates_new(self) -> None:
        """dispose_all後にget_engineで新しいエンジンが生成される"""
        url = "sqlite:///test_all_recreate.db"
        e1 = get_engine(url)
        dispose_all_engines()
        e2 = get_engine(url)
        assert e1 is not e2
