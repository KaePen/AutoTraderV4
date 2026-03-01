"""認証モジュール"""

from __future__ import annotations

from autotrader.web.auth.config import AuthSettings, get_auth_settings
from autotrader.web.auth.dependencies import (
    get_current_user,
    get_optional_user,
    require_admin,
)
from autotrader.web.auth.security import (
    create_access_token,
    get_password_hash,
    verify_password,
)
from autotrader.web.auth.user_store import (
    UserInfo,
    UserStore,
    get_user_store,
    reset_user_store,
)

__all__ = [
    "AuthSettings",
    "get_auth_settings",
    "get_current_user",
    "get_optional_user",
    "require_admin",
    "create_access_token",
    "get_password_hash",
    "verify_password",
    "UserInfo",
    "UserStore",
    "get_user_store",
    "reset_user_store",
]
