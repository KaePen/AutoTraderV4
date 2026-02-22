"""MT5口座プリセットのYAML読み書き"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_DIR = (
    Path(__file__).resolve().parent.parent.parent / "config"
)
_ACCOUNTS_FILE = "accounts.yaml"


class AccountsLoader:
    """MT5口座プリセットのYAML読み書き

    Attributes:
        _config_dir: 設定ファイルディレクトリ
        _path: accounts.yaml のパス
    """

    def __init__(
        self, config_dir: Path | None = None,
    ) -> None:
        """初期化

        Args:
            config_dir: 設定ファイルディレクトリ
        """
        self._config_dir = config_dir or _DEFAULT_CONFIG_DIR
        self._path = self._config_dir / _ACCOUNTS_FILE

    def load(self) -> list[dict]:
        """口座プリセット一覧を読み込み

        Returns:
            list[dict]: 口座プリセットリスト
        """
        if not self._path.exists():
            return []
        with open(self._path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        return raw.get("accounts", []) or []

    def save(self, accounts: list[dict]) -> None:
        """口座プリセット一覧を保存

        Args:
            accounts: 保存する口座リスト
        """
        self._config_dir.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            yaml.dump(
                {"accounts": accounts},
                f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )
        logger.info("口座プリセット保存: %d件", len(accounts))

    def add_or_update(
        self, login: int, server: str, name: str,
    ) -> list[dict]:
        """口座プリセットを追加または更新

        同一loginが存在する場合は更新、なければ追加する。

        Args:
            login: MT5ログインID
            server: サーバー名
            name: 表示名

        Returns:
            list[dict]: 更新後の口座リスト
        """
        accounts = self.load()
        for acct in accounts:
            if acct.get("login") == login:
                acct["server"] = server
                acct["name"] = name
                self.save(accounts)
                logger.info("口座更新: login=%d", login)
                return accounts
        accounts.append(
            {"login": login, "server": server, "name": name}
        )
        self.save(accounts)
        logger.info("口座追加: login=%d", login)
        return accounts

    def delete(self, login: int) -> list[dict]:
        """口座プリセットを削除

        Args:
            login: 削除するMT5ログインID

        Returns:
            list[dict]: 削除後の口座リスト
        """
        accounts = self.load()
        before = len(accounts)
        accounts = [
            a for a in accounts if a.get("login") != login
        ]
        self.save(accounts)
        logger.info(
            "口座削除: login=%d (削除数: %d)",
            login, before - len(accounts),
        )
        return accounts
