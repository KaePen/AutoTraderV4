"""シンボルプリセット設定のユニットテスト"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from autotrader.config.trading_params import (
    DEFAULT_TRADING_PARAMS,
    SymbolPreset,
    TradingParams,
    get_preset,
    reload_presets,
)


# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_preset_cache():
    """各テスト前後にキャッシュをリセット"""
    reload_presets()
    yield
    reload_presets()


@pytest.fixture()
def custom_yaml(tmp_path: Path) -> Path:
    """テスト用カスタムYAMLを生成"""
    content = textwrap.dedent("""
        defaults:
          pip_value: 100.0
          spread_pips: 2.0
          slippage_pips: 0.5
          default_sl_pips: 20.0
          default_tp_pips: 40.0
          min_lot: 0.01
          max_lot: 10.0
          commission_per_lot: 0.0
          max_positions: 2
          bonus_max_positions: 1
          bonus_score_threshold: 7.0
          base_risk_pct: 0.02
          max_lot_per_trade: 2.0
          max_total_exposure_lot: 5.0
          equity_floor_pct: 0.30

        symbols:
          TESTPAIR:
            pip_value: 50.0
            spread_pips: 3.0
            slippage_pips: 1.5
            default_sl_pips: 25.0
            default_tp_pips: 50.0
            max_positions: 1
            bonus_score_threshold: 8.0
            base_risk_pct: 0.015
            max_lot_per_trade: 1.0
    """).strip()
    path = tmp_path / "test_presets.yaml"
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# SymbolPreset 基本テスト
# ---------------------------------------------------------------------------

class TestGetPresetUsdjpy:
    """USDJPY プリセットのフィールド検証"""

    def test_usdjpy_pip_value(self):
        """pip_value が USDJPY 正しい値"""
        preset = get_preset("USDJPY")
        assert preset.pip_value == 100.0

    def test_usdjpy_spread_pips(self):
        """spread_pips が USDJPY 正しい値"""
        preset = get_preset("USDJPY")
        assert preset.spread_pips == 1.5

    def test_usdjpy_slippage_pips(self):
        """slippage_pips が USDJPY 正しい値"""
        preset = get_preset("USDJPY")
        assert preset.slippage_pips == 0.5

    def test_usdjpy_max_positions(self):
        """max_positions が USDJPY 正しい値"""
        preset = get_preset("USDJPY")
        assert preset.max_positions == 2

    def test_usdjpy_bonus_score_threshold(self):
        """bonus_score_threshold が USDJPY 正しい値"""
        preset = get_preset("USDJPY")
        assert preset.bonus_score_threshold == 7.0

    def test_usdjpy_symbol_field(self):
        """symbol フィールドが USDJPY"""
        preset = get_preset("USDJPY")
        assert preset.symbol == "USDJPY"

    def test_usdjpy_returns_symbol_preset(self):
        """戻り値が SymbolPreset インスタンス"""
        preset = get_preset("USDJPY")
        assert isinstance(preset, SymbolPreset)


class TestGetPresetUnknownSymbol:
    """未定義シンボルはデフォルト値を返す"""

    def test_unknown_returns_symbol_preset(self):
        """SymbolPreset を返す"""
        preset = get_preset("UNKNOWN_XYZ")
        assert isinstance(preset, SymbolPreset)

    def test_unknown_symbol_field(self):
        """symbol フィールドは要求したシンボル名"""
        preset = get_preset("UNKNOWN_XYZ")
        assert preset.symbol == "UNKNOWN_XYZ"

    def test_unknown_pip_value_default(self):
        """pip_value はデフォルト値 100.0"""
        preset = get_preset("UNKNOWN_XYZ")
        assert preset.pip_value == 100.0

    def test_unknown_spread_pips_default(self):
        """spread_pips はデフォルト値 1.5"""
        preset = get_preset("UNKNOWN_XYZ")
        assert preset.spread_pips == 1.5


class TestGetPresetWithCustomYaml:
    """path 引数でテスト用 YAML を注入できる"""

    def test_custom_yaml_pip_value(self, custom_yaml: Path):
        """カスタムYAML の pip_value が読まれる"""
        preset = get_preset("TESTPAIR", path=custom_yaml)
        assert preset.pip_value == 50.0

    def test_custom_yaml_spread_pips(self, custom_yaml: Path):
        """カスタムYAML の spread_pips が読まれる"""
        preset = get_preset("TESTPAIR", path=custom_yaml)
        assert preset.spread_pips == 3.0

    def test_custom_yaml_bonus_score_threshold(self, custom_yaml: Path):
        """カスタムYAML の bonus_score_threshold が読まれる"""
        preset = get_preset("TESTPAIR", path=custom_yaml)
        assert preset.bonus_score_threshold == 8.0

    def test_custom_yaml_defaults_merged(self, custom_yaml: Path):
        """明示指定なしのフィールドは defaults から補完される"""
        preset = get_preset("TESTPAIR", path=custom_yaml)
        # defaults の max_total_exposure_lot = 5.0
        assert preset.max_total_exposure_lot == 5.0

    def test_custom_yaml_unknown_uses_default(self, custom_yaml: Path):
        """カスタムYAMLにない未定義シンボルはデフォルト値"""
        preset = get_preset("NOEXIST", path=custom_yaml)
        assert preset.pip_value == 100.0


class TestReloadPresets:
    """reload_presets() でキャッシュがリセットされる"""

    def test_reload_with_different_yaml(self, tmp_path: Path):
        """異なる YAML で reload すると新しい値が返される"""
        yaml1 = tmp_path / "v1.yaml"
        yaml1.write_text(
            "defaults:\n  spread_pips: 1.0\nsymbols:\n  PAIR: {spread_pips: 1.0}\n",
            encoding="utf-8",
        )
        get_preset("PAIR", path=yaml1)

        yaml2 = tmp_path / "v2.yaml"
        yaml2.write_text(
            "defaults:\n  spread_pips: 5.0\nsymbols:\n  PAIR: {spread_pips: 5.0}\n",
            encoding="utf-8",
        )
        reload_presets(path=yaml2)
        preset = get_preset("PAIR", path=yaml2)
        assert preset.spread_pips == 5.0

    def test_reload_clears_cache(self, custom_yaml: Path):
        """reload 後にキャッシュが消える（再度読み込まれる）"""
        _ = get_preset("TESTPAIR", path=custom_yaml)
        reload_presets()
        # キャッシュ消去後は未定義扱いになり default 値を返す
        preset = get_preset("TESTPAIR")
        # デフォルトパスにTESTPAIRがなければデフォルト値
        assert isinstance(preset, SymbolPreset)


# ---------------------------------------------------------------------------
# to_trading_params() テスト
# ---------------------------------------------------------------------------

class TestToTradingParams:
    """to_trading_params() が正しい値を返す"""

    def test_returns_trading_params(self):
        """TradingParams インスタンスを返す"""
        preset = get_preset("USDJPY")
        tp = preset.to_trading_params()
        assert isinstance(tp, TradingParams)

    def test_spread_pips_matches(self):
        """spread_pips が一致"""
        preset = get_preset("USDJPY")
        tp = preset.to_trading_params()
        assert tp.spread_pips == preset.spread_pips

    def test_pip_value_matches(self):
        """pip_value が一致"""
        preset = get_preset("USDJPY")
        tp = preset.to_trading_params()
        assert tp.pip_value == preset.pip_value

    def test_slippage_pips_matches(self):
        """slippage_pips が一致"""
        preset = get_preset("USDJPY")
        tp = preset.to_trading_params()
        assert tp.slippage_pips == preset.slippage_pips

    def test_default_sl_pips_matches(self):
        """default_sl_pips が一致"""
        preset = get_preset("USDJPY")
        tp = preset.to_trading_params()
        assert tp.default_sl_pips == preset.default_sl_pips


# ---------------------------------------------------------------------------
# BacktestConfig.from_preset() テスト
# ---------------------------------------------------------------------------

class TestBacktestConfigFromPreset:
    """BacktestConfig.from_preset() の検証"""

    def test_returns_backtest_config(self):
        """BacktestConfig インスタンスを返す"""
        from autotrader.backtest.runner import BacktestConfig

        config = BacktestConfig.from_preset("USDJPY")
        assert isinstance(config, BacktestConfig)

    def test_symbol_set(self):
        """symbol フィールドが設定される"""
        from autotrader.backtest.runner import BacktestConfig

        config = BacktestConfig.from_preset("USDJPY")
        assert config.symbol == "USDJPY"

    def test_spread_pips_from_preset(self):
        """spread_pips がプリセット値"""
        from autotrader.backtest.runner import BacktestConfig

        preset = get_preset("USDJPY")
        config = BacktestConfig.from_preset("USDJPY")
        assert config.spread_pips == preset.spread_pips

    def test_pip_value_from_preset(self):
        """pip_value がプリセット値"""
        from autotrader.backtest.runner import BacktestConfig

        preset = get_preset("USDJPY")
        config = BacktestConfig.from_preset("USDJPY")
        assert config.pip_value == preset.pip_value

    def test_max_positions_from_preset(self):
        """max_positions がプリセット値"""
        from autotrader.backtest.runner import BacktestConfig

        preset = get_preset("USDJPY")
        config = BacktestConfig.from_preset("USDJPY")
        assert config.max_positions == preset.max_positions


class TestBacktestConfigFromPresetOverrides:
    """overrides でフィールドを上書きできる"""

    def test_override_spread_pips(self):
        """spread_pips を上書きできる"""
        from autotrader.backtest.runner import BacktestConfig

        config = BacktestConfig.from_preset("USDJPY", spread_pips=0.5)
        assert config.spread_pips == 0.5

    def test_override_max_positions(self):
        """max_positions を上書きできる"""
        from autotrader.backtest.runner import BacktestConfig

        config = BacktestConfig.from_preset("USDJPY", max_positions=5)
        assert config.max_positions == 5

    def test_override_initial_balance(self):
        """initial_balance を上書きできる"""
        from autotrader.backtest.runner import BacktestConfig

        config = BacktestConfig.from_preset(
            "USDJPY", initial_balance=500_000.0,
        )
        assert config.initial_balance == 500_000.0

    def test_non_overridden_fields_use_preset(self):
        """上書きしていないフィールドはプリセット値が維持される"""
        from autotrader.backtest.runner import BacktestConfig

        preset = get_preset("USDJPY")
        config = BacktestConfig.from_preset("USDJPY", spread_pips=0.5)
        # spread_pips は上書き、pip_value はプリセット値
        assert config.pip_value == preset.pip_value
