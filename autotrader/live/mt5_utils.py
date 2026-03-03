"""MT5共通ユーティリティ

engine.py と position_sync.py で共有するヘルパー関数。
"""

from __future__ import annotations

import logging

from autotrader.core.enums import ExitReason

logger = logging.getLogger(__name__)

# MT5 DEAL_REASONコード → ExitReason マッピング
_MT5_REASON_MAP: dict[int, ExitReason] = {
    0: ExitReason.MANUAL_CLOSE,  # CLIENT
    1: ExitReason.MANUAL_CLOSE,  # MOBILE
    2: ExitReason.MANUAL_CLOSE,  # WEB
    3: ExitReason.EXTERNAL_CLOSE,  # EXPERT（他EA）
    4: ExitReason.STOP_LOSS,  # SL
    5: ExitReason.TAKE_PROFIT,  # TP
    6: ExitReason.STOP_OUT,  # ストップアウト
}


def mt5_reason_to_exit_reason(reason_code: int) -> str:
    """MT5 DEAL_REASONコードをExitReason.valueに変換

    Args:
        reason_code: MT5のDEAL_REASONコード

    Returns:
        str: ExitReason の文字列値
    """
    exit_reason = _MT5_REASON_MAP.get(reason_code)
    if exit_reason is None:
        logger.warning(
            "未知のMT5 DEAL_REASONコード: %d"
            " → EXTERNAL_CLOSE にフォールバック",
            reason_code,
        )
        return ExitReason.EXTERNAL_CLOSE.value
    return exit_reason.value
