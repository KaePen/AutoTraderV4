"""エラーハンドラーテスト"""

from __future__ import annotations

import logging

import pytest

from autotrader.web.utils.error_handler import (
    sanitize_error_message,
)


def test_sanitize_error_message_basic():
    """基本的なエラーメッセージのサニタイズ"""
    e = ValueError("Invalid config at /path/to/file.py:123")
    msg = sanitize_error_message(e, "MT5接続")
    assert msg == "MT5接続に失敗しました"
    assert "/path/to/file.py" not in msg
    assert "ValueError" not in msg


def test_sanitize_error_message_with_stack_trace():
    """スタックトレースを含むエラーのサニタイズ"""
    try:
        raise RuntimeError(
            "Connection failed: autotrader.adapters.mt5"
        )
    except Exception as e:
        msg = sanitize_error_message(e, "エンジン起動")
    assert msg == "エンジン起動に失敗しました"
    assert "autotrader.adapters.mt5" not in msg
    assert "RuntimeError" not in msg


def test_sanitize_error_message_with_context():
    """コンテキスト情報付きエラーのサニタイズ"""
    e = ConnectionError("MT5 terminal not found")
    context = {"symbol": "USDJPY", "account": 12345}
    msg = sanitize_error_message(
        e, "口座切替", log_context=context
    )
    assert msg == "口座切替に失敗しました"
    # コンテキスト情報はログにのみ記録され、メッセージには含まれない
    assert "USDJPY" not in msg
    assert "12345" not in msg


def test_sanitize_error_message_logs_details(caplog):
    """詳細エラーがログに記録されることを確認"""
    e = ValueError("Detailed error: /internal/path")
    with caplog.at_level(logging.ERROR):
        msg = sanitize_error_message(e, "シンボル切替")

    # メッセージは汎用化
    assert msg == "シンボル切替に失敗しました"

    # ログには詳細が記録される
    assert len(caplog.records) == 1
    log_msg = caplog.records[0].message
    assert "シンボル切替エラー" in log_msg
    assert "Detailed error" in log_msg


def test_sanitize_error_message_with_log_context(caplog):
    """ログコンテキストが記録されることを確認"""
    e = ValueError("Config error")
    context = {"operation": "init", "file": "config.yaml"}

    with caplog.at_level(logging.ERROR):
        sanitize_error_message(
            e, "設定読込", log_context=context
        )

    # ログにコンテキストが含まれる
    log_msg = caplog.records[0].message
    assert "設定読込エラー" in log_msg
    assert "operation" in log_msg
    assert "init" in log_msg
    assert "config.yaml" in log_msg


def test_sanitize_error_message_different_exceptions():
    """異なる例外タイプでの動作確認"""
    test_cases = [
        (ValueError("value error"), "MT5接続"),
        (KeyError("missing_key"), "エンジン起動"),
        (OSError("file not found"), "口座切替"),
        (
            ConnectionError("connection refused"),
            "シンボル切替",
        ),
    ]

    for exc, operation in test_cases:
        msg = sanitize_error_message(exc, operation)
        # 全て汎用メッセージに変換される
        assert msg == f"{operation}に失敗しました"
        # 例外の詳細は含まれない
        assert str(exc) not in msg
        assert type(exc).__name__ not in msg
