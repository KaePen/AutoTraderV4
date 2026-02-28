"""テスト設定

pytest fixtures を提供する。
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from autotrader.web.main import create_app


@pytest.fixture
async def client():
    """非同期HTTPクライアント

    FastAPI TestClient の代わりに httpx.AsyncClient を使用。
    lifespan イベントを正しく処理するため。

    Yields:
        AsyncClient: 非同期HTTPクライアント
    """
    app = create_app()
    async with AsyncClient(
        app=app, base_url="http://test"
    ) as ac:
        yield ac
