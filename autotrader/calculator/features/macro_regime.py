"""マクロレジームフィルタ

VIX（恐怖指数）ベースでマクロ環境の急変を検知し、
トレード停止・慎重化を判断する。
バックテストとリアルトレードの両方で同じロジックを使用。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class MacroRegimeLevel(Enum):
    """マクロレジームレベル"""

    NORMAL = "normal"
    ELEVATED = "elevated"
    HIGH_FEAR = "high_fear"
    EXTREME_FEAR = "extreme_fear"


@dataclass(frozen=True)
class MacroRegimeConfig:
    """マクロレジームフィルタ設定

    Attributes:
        enabled: フィルタ有効化
        vix_elevated_threshold: ELEVATED閾値
        vix_high_fear_threshold: HIGH_FEAR閾値
        vix_extreme_fear_threshold: EXTREME_FEAR閾値
        elevated_penalty: ELEVATEDペナルティ
        high_fear_penalty: HIGH_FEARペナルティ
    """

    enabled: bool = False
    vix_elevated_threshold: float = 20.0
    vix_high_fear_threshold: float = 30.0
    vix_extreme_fear_threshold: float = 40.0
    elevated_penalty: float = 0.1
    high_fear_penalty: float = 0.3


class MacroRegimeFilter:
    """マクロレジームフィルタ

    VIX値からマクロ環境の状態を判定し、
    HardGuard/SoftGuardに統合するための情報を提供する。
    """

    def __init__(
        self,
        config: MacroRegimeConfig | None = None,
    ) -> None:
        self._config = config or MacroRegimeConfig()
        self._current_vix: float | None = None
        self._current_level = MacroRegimeLevel.NORMAL

    @property
    def config(self) -> MacroRegimeConfig:
        """設定"""
        return self._config

    @property
    def current_vix(self) -> float | None:
        """現在のVIX値"""
        return self._current_vix

    @property
    def current_level(self) -> MacroRegimeLevel:
        """現在のマクロレジームレベル"""
        return self._current_level

    def update_vix(self, vix_value: float) -> MacroRegimeLevel:
        """VIX値を更新してレジームを判定

        Args:
            vix_value: VIX値

        Returns:
            MacroRegimeLevel: 判定結果
        """
        self._current_vix = vix_value
        self._current_level = self._classify(vix_value)
        return self._current_level

    def _classify(self, vix: float) -> MacroRegimeLevel:
        """VIX値をレジームに分類

        Args:
            vix: VIX値

        Returns:
            MacroRegimeLevel: レジームレベル
        """
        if vix >= self._config.vix_extreme_fear_threshold:
            return MacroRegimeLevel.EXTREME_FEAR
        if vix >= self._config.vix_high_fear_threshold:
            return MacroRegimeLevel.HIGH_FEAR
        if vix >= self._config.vix_elevated_threshold:
            return MacroRegimeLevel.ELEVATED
        return MacroRegimeLevel.NORMAL

    def should_block_trade(self) -> tuple[bool, str | None]:
        """HardGuard: トレードをブロックすべきか

        Returns:
            tuple[bool, str | None]: (ブロックすべきか, 理由)
        """
        if not self._config.enabled:
            return False, None
        if self._current_vix is None:
            return False, None

        if self._current_level == MacroRegimeLevel.EXTREME_FEAR:
            return (
                True,
                f"VIX極度恐怖: {self._current_vix:.1f}"
                f"(>={self._config.vix_extreme_fear_threshold})",
            )
        return False, None

    def get_penalty(self) -> tuple[float, str | None]:
        """SoftGuard: ペナルティを取得

        Returns:
            tuple[float, str | None]: (ペナルティ, 理由)
        """
        if not self._config.enabled:
            return 0.0, None
        if self._current_vix is None:
            return 0.0, None

        if self._current_level == MacroRegimeLevel.HIGH_FEAR:
            return (
                self._config.high_fear_penalty,
                f"VIX高恐怖: {self._current_vix:.1f}",
            )
        if self._current_level == MacroRegimeLevel.ELEVATED:
            return (
                self._config.elevated_penalty,
                f"VIX警戒: {self._current_vix:.1f}",
            )
        return 0.0, None

    def get_status_dict(self) -> dict[str, object]:
        """ステータスを辞書形式で取得（API/diagnostics用）"""
        return {
            "macro_vix": self._current_vix,
            "macro_regime_level": self._current_level.value,
        }
