"""Web設定モジュール"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class WebSettings(BaseSettings):
    """Web API設定

    Attributes:
        host: ホストアドレス
        port: ポート番号
        debug: デバッグモード
        cors_origins: CORSオリジン
        ws_heartbeat_interval: WebSocketハートビート間隔（秒）
    """

    model_config = SettingsConfigDict(
        env_prefix="AUTOTRADER_WEB_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://localhost:3000"]
    )
    ws_heartbeat_interval: int = 30


@lru_cache
def get_web_settings() -> WebSettings:
    """Web設定シングルトンを取得

    Returns:
        WebSettings: Web設定インスタンス
    """
    return WebSettings()
