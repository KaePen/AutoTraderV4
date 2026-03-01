"""エラーメッセージサニタイズ

APIレスポンスに含めるエラーメッセージから、
内部構造情報（スタックトレース・ファイルパス・モジュール名）を除去し、
汎用的なエラーメッセージを返す。

セキュリティ原則:
- 詳細エラー情報はサーバーログにのみ記録
- クライアントには操作内容のみを伝える汎用メッセージを返す
- システム構成・バージョン・内部パスの漏洩を防止
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def sanitize_error_message(
    e: Exception,
    operation: str = "操作",
    log_context: dict[str, Any] | None = None,
) -> str:
    """例外から汎用エラーメッセージを生成

    内部例外の詳細はログに記録し、クライアントには操作名のみを返す。

    Args:
        e (Exception): 発生した例外
        operation (str): 操作名（例: "MT5接続", "エンジン起動"）
        log_context (dict[str, Any] | None): ログに記録する追加情報

    Returns:
        str: クライアントに返す汎用エラーメッセージ

    Examples:
        >>> try:
        ...     raise ValueError("Invalid config at /path/to/file.py:123")
        ... except Exception as e:
        ...     msg = sanitize_error_message(e, "MT5接続")
        >>> msg
        'MT5接続に失敗しました'
    """
    # サーバーログに詳細を記録（スタックトレース含む）
    log_msg = f"{operation}エラー"
    if log_context:
        logger.error(
            "%s: %s (context: %s)",
            log_msg,
            e,
            log_context,
            exc_info=True,
        )
    else:
        logger.error("%s: %s", log_msg, e, exc_info=True)

    # クライアントには汎用メッセージのみ
    return f"{operation}に失敗しました"
