"""ConfigLoaderのユニットテスト"""

from __future__ import annotations

from pathlib import Path

import yaml

from autotrader.config.config_loader import ConfigLoader
from autotrader.decision.unified.config import UnifiedBotConfig
from autotrader.decision.unified.position_manager import (
    PositionManagerConfig,
)


class TestConfigLoader:
    """ConfigLoaderのテスト"""

    def test_ファイルなしでデフォルト値を返す(
        self, tmp_path: Path,
    ) -> None:
        """設定ファイルが存在しない場合デフォルト値"""
        loader = ConfigLoader(config_dir=tmp_path)
        bot, pm = loader.load_live_config()

        assert isinstance(bot, UnifiedBotConfig)
        assert isinstance(pm, PositionManagerConfig)
        # デフォルト値確認
        assert bot.base_risk_pct == 0.04
        assert pm.partial_close_1r_ratio == 0.05

    def test_正常なYAML読み込み(
        self, tmp_path: Path,
    ) -> None:
        """YAMLファイルから正しくConfig構築"""
        config_data = {
            "bot_config": {
                "base_risk_pct": 0.03,
                "max_lot_per_trade": 3.0,
            },
            "pm_config": {
                "partial_close_1r_ratio": 0.4,
                "trailing_start_r": 2.5,
                "breakeven_at_1r": False,
            },
        }
        yaml_path = tmp_path / "live_trading.yaml"
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(config_data, f)

        loader = ConfigLoader(config_dir=tmp_path)
        bot, pm = loader.load_live_config()

        assert bot.base_risk_pct == 0.03
        assert bot.max_lot_per_trade == 3.0
        # 未指定フィールドはデフォルト
        assert bot.use_dynamic_lot is True

        assert pm.partial_close_1r_ratio == 0.4
        assert pm.trailing_start_r == 2.5
        assert pm.breakeven_at_1r is False
        # 未指定フィールドはデフォルト
        assert pm.spread_pips == 1.5

    def test_不明キーが無視される(
        self, tmp_path: Path,
    ) -> None:
        """YAMLに不明なキーがあっても無視される"""
        config_data = {
            "bot_config": {
                "base_risk_pct": 0.01,
                "unknown_field": 999,
            },
            "pm_config": {
                "partial_close_1r_ratio": 0.5,
                "typo_field": "bad",
            },
        }
        yaml_path = tmp_path / "live_trading.yaml"
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(config_data, f)

        loader = ConfigLoader(config_dir=tmp_path)
        bot, pm = loader.load_live_config()

        assert bot.base_risk_pct == 0.01
        assert pm.partial_close_1r_ratio == 0.5

    def test_空のYAMLファイル(
        self, tmp_path: Path,
    ) -> None:
        """空のYAMLファイルでデフォルト値"""
        yaml_path = tmp_path / "live_trading.yaml"
        yaml_path.write_text("")

        loader = ConfigLoader(config_dir=tmp_path)
        bot, pm = loader.load_live_config()

        assert isinstance(bot, UnifiedBotConfig)
        assert isinstance(pm, PositionManagerConfig)

    def test_save_pm_config永続化と再読み込み(
        self, tmp_path: Path,
    ) -> None:
        """PM設定をYAML保存→再読み込みで一致"""
        loader = ConfigLoader(config_dir=tmp_path)

        # カスタムPM設定を作成
        pm = PositionManagerConfig(
            partial_close_1r_ratio=0.45,
            trailing_start_r=3.0,
            insurance_trigger_r=1.5,
        )
        loader.save_pm_config(pm)

        # 再読み込み
        _, loaded_pm = loader.load_live_config()
        assert loaded_pm.partial_close_1r_ratio == 0.45
        assert loaded_pm.trailing_start_r == 3.0
        assert loaded_pm.insurance_trigger_r == 1.5

    def test_save_bot_config永続化と再読み込み(
        self, tmp_path: Path,
    ) -> None:
        """Bot設定をYAML保存→再読み込みで一致"""
        loader = ConfigLoader(config_dir=tmp_path)

        bot = UnifiedBotConfig(
            base_risk_pct=0.05,
            max_lot_per_trade=5.0,
        )
        loader.save_bot_config(bot)

        loaded_bot, _ = loader.load_live_config()
        assert loaded_bot.base_risk_pct == 0.05
        assert loaded_bot.max_lot_per_trade == 5.0

    def test_セクション差し替え保存で他セクション維持(
        self, tmp_path: Path,
    ) -> None:
        """pm_config保存時にbot_configが維持される"""
        # 初期YAML作成
        config_data = {
            "bot_config": {
                "base_risk_pct": 0.03,
            },
            "pm_config": {
                "partial_close_1r_ratio": 0.3,
            },
        }
        yaml_path = tmp_path / "live_trading.yaml"
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(config_data, f)

        # PM設定のみ更新
        loader = ConfigLoader(config_dir=tmp_path)
        pm = PositionManagerConfig(
            partial_close_1r_ratio=0.5,
        )
        loader.save_pm_config(pm)

        # bot_configが維持されている
        bot, loaded_pm = loader.load_live_config()
        assert bot.base_risk_pct == 0.03
        assert loaded_pm.partial_close_1r_ratio == 0.5

    def test_tuple型フィールドのlist変換(
        self, tmp_path: Path,
    ) -> None:
        """YAMLのlistがPM設定のtuple型に変換される"""
        config_data = {
            "pm_config": {
                "be_enabled_modes": [
                    "UNIVERSAL",
                ],
            },
        }
        yaml_path = tmp_path / "live_trading.yaml"
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(config_data, f)

        loader = ConfigLoader(config_dir=tmp_path)
        _, pm = loader.load_live_config()

        assert isinstance(pm.be_enabled_modes, tuple)
        assert len(pm.be_enabled_modes) == 1


class TestPresetConfig:
    """load_preset_config のテスト"""

    def test_デフォルト設定でUSDJPY読み込み(self) -> None:
        """実際のsymbol_presets.yamlからUSDJPY設定を読み込み"""
        loader = ConfigLoader()
        bot, pm = loader.load_preset_config("USDJPY")

        assert isinstance(bot, UnifiedBotConfig)
        assert isinstance(pm, PositionManagerConfig)
        # symbol_presets.yamlのsignal.consensus_threshold
        assert bot.consensus_threshold == 9.0
        # symbol_presets.yamlのsignal.bca_min_edge
        assert bot.bca_min_edge == 0.60
        # USDJPYプリセットのmax_positions
        assert bot.max_positions == 1

    def test_EURJPY通貨ペア別上書き(self) -> None:
        """EURJPYの通貨ペア別signal設定が適用される"""
        loader = ConfigLoader()
        bot, _ = loader.load_preset_config("EURJPY")

        # EURJPY固有: bca_min_edge=0.70
        assert bot.bca_min_edge == 0.70
        # 共通デフォルト: consensus_threshold=9.0
        assert bot.consensus_threshold == 9.0
        # EURJPYプリセットのmax_positions
        assert bot.max_positions == 1

    def test_存在しない通貨ペアでフォールバック(self) -> None:
        """未定義シンボルはデフォルト値が使用される"""
        loader = ConfigLoader()
        bot, _ = loader.load_preset_config("XYZJPY")

        # デフォルト値
        assert bot.consensus_threshold == 9.0
        assert bot.bca_min_edge == 0.60

    def test_PM設定が読み込まれる(self) -> None:
        """pm_configセクションが正しく読み込まれる"""
        loader = ConfigLoader()
        _, pm = loader.load_preset_config("USDJPY")

        # symbol_presets.yamlのpm_config
        assert pm.partial_close_1r_ratio == 0.05
        assert pm.trailing_start_r == 0.5
        assert pm.stagnation_exit_minutes == 120.0

    def test_フィルター設定が読み込まれる(self) -> None:
        """filterセクションが正しく読み込まれる"""
        loader = ConfigLoader()
        bot, _ = loader.load_preset_config("USDJPY")

        # symbol_presets.yamlのfilter
        assert bot.weak_hours_enabled is True
        assert bot.sg_spread_penalty_rate == 0.2
        assert bot.regime_threshold_enabled is True


class TestSubConfigs:
    """サブConfig のテスト"""

    def test_to_signal_config(self) -> None:
        """UnifiedBotConfigからSignalConfigを抽出"""
        from autotrader.decision.unified.config import (
            SignalConfig,
        )

        bot = UnifiedBotConfig(
            consensus_threshold=10.0,
            bca_min_edge=0.65,
        )
        sig = bot.to_signal_config()

        assert isinstance(sig, SignalConfig)
        assert sig.consensus_threshold == 10.0
        assert sig.bca_min_edge == 0.65
        assert sig.htf_score_filter_enabled is True

    def test_to_risk_management_config(self) -> None:
        """UnifiedBotConfigからRiskManagementConfigを抽出"""
        from autotrader.decision.unified.config import (
            RiskManagementConfig,
        )

        bot = UnifiedBotConfig(
            sl_min_pips=25.0,
            max_positions=5,
        )
        risk = bot.to_risk_management_config()

        assert isinstance(risk, RiskManagementConfig)
        assert risk.sl_min_pips == 25.0
        assert risk.max_positions == 5

    def test_to_filter_config(self) -> None:
        """UnifiedBotConfigからFilterConfigを抽出"""
        from autotrader.decision.unified.config import (
            FilterConfig,
        )

        bot = UnifiedBotConfig(
            weak_hours_enabled=False,
            off_hours_trend_block=True,
        )
        flt = bot.to_filter_config()

        assert isinstance(flt, FilterConfig)
        assert flt.weak_hours_enabled is False
        assert flt.off_hours_trend_block is True

    def test_from_sub_configs_roundtrip(self) -> None:
        """サブConfig経由でUnifiedBotConfigを再構築"""
        original = UnifiedBotConfig(
            consensus_threshold=10.0,
            bca_min_edge=0.65,
            sl_min_pips=25.0,
            weak_hours_enabled=False,
        )
        sig = original.to_signal_config()
        risk = original.to_risk_management_config()
        flt = original.to_filter_config()

        rebuilt = UnifiedBotConfig.from_sub_configs(
            signal=sig, risk_mgmt=risk, filter_cfg=flt,
        )

        assert rebuilt.consensus_threshold == 10.0
        assert rebuilt.bca_min_edge == 0.65
        assert rebuilt.sl_min_pips == 25.0
        assert rebuilt.weak_hours_enabled is False

    def test_from_sub_configs_with_kwargs(self) -> None:
        """サブConfig + kwargs でUnifiedBotConfig構築"""
        from autotrader.decision.unified.config import (
            SignalConfig,
        )

        sig = SignalConfig(consensus_threshold=8.0)
        rebuilt = UnifiedBotConfig.from_sub_configs(
            signal=sig, demo_mode=True,
        )

        assert rebuilt.consensus_threshold == 8.0
        assert rebuilt.demo_mode is True
