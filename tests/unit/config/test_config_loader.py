"""ConfigLoaderのユニットテスト"""

from __future__ import annotations

from pathlib import Path

import pytest
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
        assert bot.base_risk_pct == 0.02
        assert pm.partial_close_1r_ratio == 0.3

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
