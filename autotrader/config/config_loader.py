"""YAML設定ファイルローダー"""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path

import yaml

from autotrader.config.trading_params import get_preset
from autotrader.decision.unified.config import UnifiedBotConfig
from autotrader.decision.unified.position_manager import (
    PositionManagerConfig,
)

logger = logging.getLogger(__name__)

# プロジェクトルートの config/ ディレクトリ
_DEFAULT_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"


def _filter_fields(
    data: dict,
    cls: type,
) -> dict:
    """dataclassの有効フィールドのみ抽出

    Args:
        data: 生データ辞書
        cls: 対象dataclass型

    Returns:
        dict: 有効フィールドのみの辞書
    """
    valid = {f.name for f in dataclasses.fields(cls)}
    filtered = {}
    for k, v in data.items():
        if k in valid:
            filtered[k] = v
        else:
            logger.warning(
                "不明な設定キー無視: %s (対象: %s)",
                k,
                cls.__name__,
            )
    return filtered


def _convert_tuple_fields(
    data: dict,
    cls: type,
) -> dict:
    """list→tuple変換（tuple型フィールド対応）

    Args:
        data: フィルタ済みデータ
        cls: 対象dataclass型

    Returns:
        dict: tuple変換済みデータ
    """
    result = dict(data)
    for f in dataclasses.fields(cls):
        if f.name in result and "tuple" in str(f.type):
            val = result[f.name]
            if isinstance(val, list):
                result[f.name] = tuple(val)
    return result


class ConfigLoader:
    """YAML設定ファイルローダー

    symbol_presets.yaml をSSOT（Single Source of Truth）として
    全設定を一元管理する。live_trading.yaml は後方互換のため
    読み込みを残すが、symbol_presets.yaml の設定が優先される。

    Attributes:
        _config_dir: 設定ファイルディレクトリ
    """

    def __init__(
        self,
        config_dir: Path | None = None,
    ) -> None:
        """初期化

        Args:
            config_dir: 設定ファイルディレクトリ
        """
        self._config_dir = config_dir or _DEFAULT_CONFIG_DIR

    def _load_presets_yaml(self) -> dict:
        """symbol_presets.yaml を読み込み

        Returns:
            dict: YAML全体のデータ
        """
        path = self._config_dir / "symbol_presets.yaml"
        if not path.exists():
            logger.warning(
                "symbol_presets.yaml なし: %s",
                path,
            )
            return {}
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def load_preset_config(
        self,
        symbol: str = "USDJPY",
        multi_mode: bool = False,
    ) -> tuple[UnifiedBotConfig, PositionManagerConfig]:
        """symbol_presets.yaml から統合設定を構築

        トップレベルの signal/risk_mgmt/filter をデフォルトとし、
        symbols[symbol] の同名セクションで上書きする。

        Args:
            symbol: 通貨ペアシンボル
            multi_mode: マルチペア/ライブモード（後方互換、現在未使用）。

        Returns:
            tuple: (UnifiedBotConfig, PositionManagerConfig)
        """
        presets = self._load_presets_yaml()
        preset = get_preset(symbol)

        # --- トップレベルのサブConfig デフォルト ---
        signal_defaults = presets.get("signal", {}) or {}
        risk_mgmt_defaults = presets.get("risk_mgmt", {}) or {}
        filter_defaults = presets.get("filter", {}) or {}
        pm_defaults = presets.get("pm_config", {}) or {}

        # --- 通貨ペア固有のサブConfig 上書き ---
        symbols = presets.get("symbols", {}) or {}
        sym_data = symbols.get(symbol, {}) or {}
        sym_signal = sym_data.pop("signal", {}) or {}
        sym_risk_mgmt = sym_data.pop("risk_mgmt", {}) or {}
        sym_filter = sym_data.pop("filter", {}) or {}
        sym_pm = sym_data.pop("pm_config", {}) or {}

        # --- マージ: トップレベル ← 通貨ペア別 ---
        signal_merged = {**signal_defaults, **sym_signal}
        risk_mgmt_merged = {
            **risk_mgmt_defaults,
            **sym_risk_mgmt,
        }
        filter_merged = {**filter_defaults, **sym_filter}
        pm_merged = {**pm_defaults, **sym_pm}

        # 廃止フィールドの除去（後方互換）
        signal_merged.pop("multi_consensus_threshold", None)

        # --- プリセット値（SymbolPreset）---
        preset_bot_defaults = {
            "max_positions": preset.max_positions,
            "bonus_max_positions": preset.bonus_max_positions,
            "bonus_score_threshold": preset.bonus_score_threshold,
            "base_risk_pct": preset.base_risk_pct,
            "max_lot_per_trade": preset.max_lot_per_trade,
            "max_total_exposure_lot": (preset.max_total_exposure_lot),
            "equity_floor_pct": preset.equity_floor_pct,
        }
        preset_pm_defaults = {
            "spread_pips": preset.spread_pips,
            "slippage_pips": preset.slippage_pips,
        }

        # --- Bot設定マージ ---
        bot_data = {
            **preset_bot_defaults,
            **signal_merged,
            **risk_mgmt_merged,
            **filter_merged,
        }
        bot_kwargs = _convert_tuple_fields(
            _filter_fields(bot_data, UnifiedBotConfig),
            UnifiedBotConfig,
        )

        # --- PM設定マージ ---
        pm_data = {**preset_pm_defaults, **pm_merged}
        pm_kwargs = _convert_tuple_fields(
            _filter_fields(pm_data, PositionManagerConfig),
            PositionManagerConfig,
        )

        return (
            UnifiedBotConfig(**bot_kwargs),
            PositionManagerConfig(**pm_kwargs),
        )

    def load_live_config(
        self,
        filename: str = "live_trading.yaml",
    ) -> tuple[UnifiedBotConfig, PositionManagerConfig]:
        """ライブ設定をYAMLから読み込み

        symbol_presets.yaml をベースに、live_trading.yaml の
        明示値で上書きする（後方互換）。

        Args:
            filename: 設定ファイル名

        Returns:
            tuple: (UnifiedBotConfig, PositionManagerConfig)
        """
        path = self._config_dir / filename
        presets_path = self._config_dir / "symbol_presets.yaml"
        if not path.exists():
            if presets_path.exists():
                logger.info(
                    "live_trading.yaml なし、symbol_presets.yaml のみ使用: %s",
                    path,
                )
                return self.load_preset_config()
            logger.warning(
                "設定ファイルなし、デフォルト使用: %s",
                path,
            )
            return UnifiedBotConfig(), PositionManagerConfig()

        # live_trading.yaml が存在する場合は廃止警告
        import warnings

        warnings.warn(
            "live_trading.yaml は廃止予定です。"
            "symbol_presets.yaml に設定を移行してください。",
            DeprecationWarning,
            stacklevel=2,
        )

        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        # シンボル取得
        symbol = raw.get("symbol", "USDJPY")

        # symbol_presets.yaml ベースで構築
        base_bot, base_pm = self.load_preset_config(symbol)

        # live_trading.yaml の明示値で上書き
        live_bot_data = raw.get("bot_config", {}) or {}
        live_pm_data = raw.get("pm_config", {}) or {}

        if not live_bot_data and not live_pm_data:
            return base_bot, base_pm

        # ベースをdict化して上書き
        bot_dict = {
            f.name: getattr(base_bot, f.name)
            for f in dataclasses.fields(base_bot)
        }
        bot_dict.update(
            _filter_fields(live_bot_data, UnifiedBotConfig),
        )
        bot_kwargs = _convert_tuple_fields(
            bot_dict,
            UnifiedBotConfig,
        )

        pm_dict = {
            f.name: getattr(base_pm, f.name)
            for f in dataclasses.fields(base_pm)
        }
        pm_dict.update(
            _filter_fields(live_pm_data, PositionManagerConfig),
        )
        pm_kwargs = _convert_tuple_fields(
            pm_dict,
            PositionManagerConfig,
        )

        return (
            UnifiedBotConfig(**bot_kwargs),
            PositionManagerConfig(**pm_kwargs),
        )

    def load_demo_config(
        self,
        filename: str = "demo_trading.yaml",
    ) -> tuple[UnifiedBotConfig, PositionManagerConfig]:
        """デモトレード設定をYAMLから読み込み

        フィルター緩和設定でデモ取引頻度を増やす。
        ファイルなし時はdemo_mode=Trueのみ設定したデフォルト。

        Args:
            filename: 設定ファイル名

        Returns:
            tuple: (UnifiedBotConfig, PositionManagerConfig)
        """
        path = self._config_dir / filename
        if not path.exists():
            logger.warning(
                "デモ設定ファイルなし、デモデフォルト使用: %s",
                path,
            )
            # demo_mode=Trueのデフォルト
            bot_defaults = {
                f.name: f.default
                for f in dataclasses.fields(UnifiedBotConfig)
                if f.default is not dataclasses.MISSING
            }
            bot_defaults["demo_mode"] = True
            return (
                UnifiedBotConfig(**bot_defaults),
                PositionManagerConfig(),
            )

        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        bot_data = raw.get("bot_config", {}) or {}
        pm_data = raw.get("pm_config", {}) or {}

        bot_kwargs = _convert_tuple_fields(
            _filter_fields(bot_data, UnifiedBotConfig),
            UnifiedBotConfig,
        )
        pm_kwargs = _convert_tuple_fields(
            _filter_fields(pm_data, PositionManagerConfig),
            PositionManagerConfig,
        )

        return (
            UnifiedBotConfig(**bot_kwargs),
            PositionManagerConfig(**pm_kwargs),
        )

    def save_pm_config(
        self,
        pm_config: PositionManagerConfig,
        filename: str = "live_trading.yaml",
    ) -> None:
        """PM設定をYAMLに保存

        既存YAMLのpm_configセクションのみ差し替え。

        Args:
            pm_config: 保存するPM設定
            filename: 設定ファイル名
        """
        self._save_section(
            "pm_config",
            pm_config,
            filename,
        )

    def save_bot_config(
        self,
        bot_config: UnifiedBotConfig,
        filename: str = "live_trading.yaml",
    ) -> None:
        """Bot設定をYAMLに保存

        既存YAMLのbot_configセクションのみ差し替え。

        Args:
            bot_config: 保存するBot設定
            filename: 設定ファイル名
        """
        self._save_section(
            "bot_config",
            bot_config,
            filename,
        )

    def _save_section(
        self,
        section: str,
        config: object,
        filename: str,
    ) -> None:
        """YAML内の特定セクションを差し替え保存

        Args:
            section: セクション名
            config: 保存する設定オブジェクト
            filename: 設定ファイル名
        """
        path = self._config_dir / filename
        self._config_dir.mkdir(parents=True, exist_ok=True)

        # 既存YAML読み込み
        if path.exists():
            with open(path, encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
        else:
            raw = {}

        # dataclass→dict変換（tuple→list, Enum→value）
        data = dataclasses.asdict(config)  # type: ignore[arg-type]
        serializable = {}
        for k, v in data.items():
            if isinstance(v, tuple):
                serializable[k] = [
                    item.value if hasattr(item, "value") else item
                    for item in v
                ]
            elif isinstance(v, list):
                serializable[k] = [
                    item.value if hasattr(item, "value") else item
                    for item in v
                ]
            elif hasattr(v, "value"):
                serializable[k] = v.value
            else:
                serializable[k] = v

        raw[section] = serializable

        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(
                raw,
                f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )
        logger.info(
            "設定保存: %s → %s",
            section,
            path,
        )
