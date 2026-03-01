"""YAMLベースユーザー管理

config/auth.yaml からユーザー情報を読み込み・管理する。
初回起動時にファイルがなければ、デフォルトユーザーで自動作成。
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import TypedDict

import yaml

from autotrader.web.auth.security import (
    get_password_hash,
    verify_password,
)

logger = logging.getLogger(__name__)

# デフォルトのauth.yamlパス
_DEFAULT_AUTH_FILE = Path("config/auth.yaml")


class UserInfo(TypedDict):
    """ユーザー情報

    Attributes:
        username: ユーザー名
        password_hash: bcryptハッシュ化パスワード
        role: ロール（admin/user）
    """

    username: str
    password_hash: str
    role: str


class UserStore:
    """YAMLベースユーザーストア

    config/auth.yaml からユーザー情報を読み込み、
    認証・ユーザー管理を行う。

    Attributes:
        _file_path: auth.yamlのパス
        _users: ユーザー情報のキャッシュ
    """

    def __init__(self, file_path: Path | None = None) -> None:
        """初期化

        Args:
            file_path: auth.yamlのパス（Noneの場合はデフォルト）
        """
        self._file_path = file_path or _DEFAULT_AUTH_FILE
        self._users: dict[str, UserInfo] = {}
        self._load()

    def _load(self) -> None:
        """YAMLファイルからユーザー情報を読み込み"""
        if not self._file_path.exists():
            logger.info(
                "auth.yaml が見つかりません。"
                "デフォルトユーザーで作成します: %s",
                self._file_path,
            )
            self._create_default()
            return

        try:
            with open(self._file_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            users = data.get("users", [])
            for user in users:
                username = user.get("username", "")
                if username:
                    self._users[username] = UserInfo(
                        username=username,
                        password_hash=user.get(
                            "password_hash", ""
                        ),
                        role=user.get("role", "user"),
                    )
            logger.debug(
                "auth.yaml から %d ユーザーを読み込み",
                len(self._users),
            )
        except Exception as e:
            logger.error("auth.yaml 読み込みエラー: %s", e)
            self._create_default()

    def _create_default(self) -> None:
        """デフォルトのauth.yamlを作成"""
        # デフォルトパスワード: admin
        default_hash = get_password_hash("admin")
        self._users = {
            "admin": UserInfo(
                username="admin",
                password_hash=default_hash,
                role="admin",
            )
        }
        self._save()
        logger.info(
            "デフォルトユーザー 'admin' を作成しました "
            "(パスワード: admin)"
        )

    def _save(self) -> None:
        """ユーザー情報をYAMLファイルに保存"""
        self._file_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "users": [
                {
                    "username": u["username"],
                    "password_hash": u["password_hash"],
                    "role": u["role"],
                }
                for u in self._users.values()
            ]
        }

        with open(self._file_path, "w", encoding="utf-8") as f:
            yaml.dump(
                data,
                f,
                default_flow_style=False,
                allow_unicode=True,
            )
        logger.debug("auth.yaml を保存: %s", self._file_path)

    def get_user(self, username: str) -> UserInfo | None:
        """ユーザー情報を取得

        Args:
            username: ユーザー名

        Returns:
            UserInfo | None: ユーザー情報（存在しない場合None）
        """
        return self._users.get(username)

    def authenticate(
        self, username: str, password: str
    ) -> UserInfo | None:
        """ユーザー認証

        Args:
            username: ユーザー名
            password: 平文パスワード

        Returns:
            UserInfo | None: 認証成功時はユーザー情報、失敗時はNone
        """
        user = self.get_user(username)
        if user is None:
            return None

        if not verify_password(password, user["password_hash"]):
            return None

        return user

    def add_user(
        self,
        username: str,
        password: str,
        role: str = "user",
    ) -> UserInfo:
        """ユーザーを追加

        Args:
            username: ユーザー名
            password: 平文パスワード
            role: ロール（デフォルト: user）

        Returns:
            UserInfo: 追加されたユーザー情報
        """
        password_hash = get_password_hash(password)
        user = UserInfo(
            username=username,
            password_hash=password_hash,
            role=role,
        )
        self._users[username] = user
        self._save()
        logger.info("ユーザー追加: %s (role=%s)", username, role)
        return user

    def update_password(
        self, username: str, new_password: str
    ) -> bool:
        """パスワードを更新

        Args:
            username: ユーザー名
            new_password: 新しい平文パスワード

        Returns:
            bool: 更新成功時True
        """
        user = self.get_user(username)
        if user is None:
            return False

        user["password_hash"] = get_password_hash(new_password)
        self._save()
        logger.info("パスワード更新: %s", username)
        return True

    def delete_user(self, username: str) -> bool:
        """ユーザーを削除

        Args:
            username: ユーザー名

        Returns:
            bool: 削除成功時True
        """
        if username not in self._users:
            return False

        del self._users[username]
        self._save()
        logger.info("ユーザー削除: %s", username)
        return True

    def list_users(self) -> list[str]:
        """ユーザー一覧を取得

        Returns:
            list[str]: ユーザー名のリスト
        """
        return list(self._users.keys())

    def reload(self) -> None:
        """YAMLファイルを再読み込み"""
        self._users.clear()
        self._load()


# シングルトンインスタンス
_user_store: UserStore | None = None


def get_user_store() -> UserStore:
    """UserStoreシングルトンを取得

    Returns:
        UserStore: ユーザーストアインスタンス
    """
    global _user_store
    if _user_store is None:
        _user_store = UserStore()
    return _user_store


def reset_user_store() -> None:
    """UserStoreシングルトンをリセット（テスト用）"""
    global _user_store
    _user_store = None
