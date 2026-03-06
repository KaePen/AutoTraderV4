"""Rate Limit ミドルウェア

slowapi を使用した API レート制限。
"""

from __future__ import annotations

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

# グローバル Limiter インスタンス（ローカル利用のため無効化）
limiter = Limiter(
    key_func=get_remote_address,
    enabled=False,
)


def configure_rate_limit(app) -> Limiter:
    """Rate Limit 設定

    Args:
        app: FastAPI アプリケーション

    Returns:
        Limiter: Rate Limiter インスタンス
    """
    app.state.limiter = limiter
    app.add_exception_handler(
        RateLimitExceeded, _rate_limit_exceeded_handler
    )
    return limiter
