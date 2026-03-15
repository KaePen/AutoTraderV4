"""マクロレジームフィルタのBot統合テスト"""

from __future__ import annotations

import pytest

from autotrader.calculator.features.macro_regime import (
    MacroRegimeConfig,
    MacroRegimeFilter,
    MacroRegimeLevel,
)
from autotrader.decision.unified.config import UnifiedBotConfig


class TestMacroRegimeFilterIntegration:
    """UnifiedTradeBotとMacroRegimeFilterの統合テスト"""

    def test_bot_has_macro_regime_filter(self) -> None:
        """BotにMacroRegimeFilterが初期化される"""
        from autotrader.decision.unified import UnifiedTradeBot

        bot = UnifiedTradeBot(UnifiedBotConfig())
        assert hasattr(bot, "_macro_regime_filter")
        assert isinstance(
            bot._macro_regime_filter,
            MacroRegimeFilter,
        )

    def test_bot_macro_regime_disabled_by_default(self) -> None:
        """デフォルトでmacro_regime_enabledはFalse"""
        from autotrader.decision.unified import UnifiedTradeBot

        bot = UnifiedTradeBot(UnifiedBotConfig())
        assert not bot._macro_regime_filter.config.enabled

    def test_bot_macro_regime_enabled(self) -> None:
        """macro_regime_enabled=Trueでフィルタが有効化"""
        from autotrader.decision.unified import UnifiedTradeBot

        config = UnifiedBotConfig(
            macro_regime_enabled=True,
        )
        bot = UnifiedTradeBot(config)
        assert bot._macro_regime_filter.config.enabled

    def test_update_macro_regime(self) -> None:
        """VIX値更新でレジーム判定が変わる"""
        from autotrader.decision.unified import UnifiedTradeBot

        config = UnifiedBotConfig(
            macro_regime_enabled=True,
        )
        bot = UnifiedTradeBot(config)

        # 正常値
        bot.update_macro_regime(15.0)
        assert (
            bot._macro_regime_filter.current_level
            == MacroRegimeLevel.NORMAL
        )

        # ELEVATED
        bot.update_macro_regime(25.0)
        assert (
            bot._macro_regime_filter.current_level
            == MacroRegimeLevel.ELEVATED
        )

        # HIGH_FEAR
        bot.update_macro_regime(35.0)
        assert (
            bot._macro_regime_filter.current_level
            == MacroRegimeLevel.HIGH_FEAR
        )

        # EXTREME_FEAR
        bot.update_macro_regime(45.0)
        assert (
            bot._macro_regime_filter.current_level
            == MacroRegimeLevel.EXTREME_FEAR
        )

    def test_config_params_propagated(self) -> None:
        """UnifiedBotConfigのパラメータがMacroRegimeConfigに伝搬"""
        from autotrader.decision.unified import UnifiedTradeBot

        config = UnifiedBotConfig(
            macro_regime_enabled=True,
            macro_regime_vix_elevated=25.0,
            macro_regime_vix_high_fear=35.0,
            macro_regime_vix_extreme_fear=50.0,
            macro_regime_elevated_penalty=0.2,
            macro_regime_high_fear_penalty=0.4,
        )
        bot = UnifiedTradeBot(config)
        mrc = bot._macro_regime_filter.config

        assert mrc.vix_elevated_threshold == 25.0
        assert mrc.vix_high_fear_threshold == 35.0
        assert mrc.vix_extreme_fear_threshold == 50.0
        assert mrc.elevated_penalty == 0.2
        assert mrc.high_fear_penalty == 0.4

    def test_extreme_fear_blocks_trade(self) -> None:
        """EXTREME_FEARでshould_block_trade=True"""
        from autotrader.decision.unified import UnifiedTradeBot

        config = UnifiedBotConfig(
            macro_regime_enabled=True,
        )
        bot = UnifiedTradeBot(config)
        bot.update_macro_regime(45.0)

        blocked, reason = (
            bot._macro_regime_filter.should_block_trade()
        )
        assert blocked
        assert reason is not None
        assert "45.0" in reason

    def test_disabled_filter_never_blocks(self) -> None:
        """disabled時はブロックしない"""
        from autotrader.decision.unified import UnifiedTradeBot

        config = UnifiedBotConfig(
            macro_regime_enabled=False,
        )
        bot = UnifiedTradeBot(config)
        bot.update_macro_regime(100.0)

        blocked, _ = (
            bot._macro_regime_filter.should_block_trade()
        )
        assert not blocked

    def test_elevated_returns_penalty(self) -> None:
        """ELEVATED時にペナルティが返る"""
        from autotrader.decision.unified import UnifiedTradeBot

        config = UnifiedBotConfig(
            macro_regime_enabled=True,
            macro_regime_elevated_penalty=0.15,
        )
        bot = UnifiedTradeBot(config)
        bot.update_macro_regime(22.0)

        penalty, reason = (
            bot._macro_regime_filter.get_penalty()
        )
        assert penalty == pytest.approx(0.15)
        assert reason is not None
