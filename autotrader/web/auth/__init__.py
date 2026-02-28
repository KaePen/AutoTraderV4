"""認証モジュール"""

from __future__ import annotations

from autotrader.web.auth.config import AuthSettings, get_auth_settings
from autotrader.web.auth.dependencies import (
    get_current_user,
    require_admin,
)
from autotrader.web.auth.security import (
    create_access_token,
    verify_password,
)

__all__ = [
    "AuthSettings",
    "get_auth_settings",
    "get_current_user",
    "require_admin",
    "create_access_token",
    "verify_password",
]
