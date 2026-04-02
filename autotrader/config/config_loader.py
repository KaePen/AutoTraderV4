"""YAML設定ファイルローダー"""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path
from typing import Any

import yaml

from autotrader.config.trading_params import get_preset
from autotrader.decision.unified.config import UnifiedBotConfig
from autotrader.decision.unified.risk.position_manager import (
    PositionManagerConfig,
)

logger = logging.getLogger(__name__)

# プロジェクトルートの config/ ディレクトリ
_DEFAULT_CONFIG_DIR = (
    Path(__file__).resolve().parent.parent.parent / "config"
)


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


def _deep_merge(base: dict, override: dict) -> dict:
    """ネスト辞書の再帰マージ（override優先）

    Args:
        base: ベース辞書
        override: 上書き辞書

    Returns:
        dict: マージ済み辞書
    """
    result = dict(base)
    for k, v in override.items():
        if (
            k in result
            and isinstance(result[k], dict)
            and isinstance(v, dict)
        ):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


class ConfigLoader:
    """YAML設定ファイルローダー

    3ファイル構成でパラメータを一元管理する:
      1. trading_defaults.yaml — 全トレードロジックのグローバルデフォルト
      2. symbol_overrides.yaml — 通貨ペア別上書き
      3. modes.yaml           — デモ/ライブ差分

    マージ優先順位（低→高）:
      trading_defaults.yaml → symbol_overrides.yaml → modes.yaml

    後方互換:
      trading_defaults.yaml/symbol_overrides.yaml 未存在時は
      symbol_presets.yaml にフォールバックする。

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

    # ------------------------------------------------------------------
    # プライベート: YAMLファイル読み込み
    # ------------------------------------------------------------------

    def _load_yaml(self, filename: str) -> dict:
        """YAMLファイルを読み込む

        Args:
            filename: ファイル名

        Returns:
            dict: YAML全体のデータ（ファイル未存在時は空辞書）
        """
        path = self._config_dir / filename
        if not path.exists():
            return {}
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _load_trading_defaults(self) -> dict:
        """trading_defaults.yaml を読み込む

        Returns:
            dict: グローバルデフォルト
        """
        return self._load_yaml("trading_defaults.yaml")

    def _load_symbol_overrides(self) -> dict:
        """symbol_overrides.yaml を読み込む

        Returns:
            dict: 通貨ペア別設定
        """
        return self._load_yaml("symbol_overrides.yaml")

    def _load_presets_yaml(self) -> dict:
        """symbol_presets.yaml を読み込む（後方互換フォールバック）

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

    def _use_new_config(self) -> bool:
        """新3ファイル構成が利用可能か確認

        Returns:
            bool: trading_defaults.yaml が存在すればTrue
        """
        return (
            self._config_dir / "trading_defaults.yaml"
        ).exists()

    # ------------------------------------------------------------------
    # プライベート: ソーストラッキング
    # ------------------------------------------------------------------

    def _track_sources(
        self,
        yaml_data: dict,
        cls: type,
        symbol: str | None = None,
    ) -> None:
        """YAMLキーとPythonデフォルト使用フィールドをログ出力

        Args:
            yaml_data: YAML由来のフィールド辞書
            cls: 対象dataclassクラス
            symbol: シンボル名（ログ用）
        """
        all_fields = {f.name for f in dataclasses.fields(cls)}
        yaml_keys = set(yaml_data.keys())
        default_used = all_fields - yaml_keys

        prefix = f"[{symbol}] " if symbol else ""
        if default_used:
            logger.debug(
                "%s%s Pythonデフォルト使用（YAML未定義）: %s",
                prefix,
                cls.__name__,
                sorted(default_used),
            )

    def _build_effective_config(
        self,
        bot_config: UnifiedBotConfig,
        pm_config: PositionManagerConfig,
        bot_yaml_keys: set[str],
        pm_yaml_keys: set[str],
        symbol: str,
    ) -> dict[str, Any]:
        """実効設定をdictで返す（job_config.json記録用）

        Args:
            bot_config: 構築済みBotConfig
            pm_config: 構築済みPMConfig
            bot_yaml_keys: BotConfigのYAML由来フィールド名
            pm_yaml_keys: PMConfigのYAML由来フィールド名
            symbol: シンボル名

        Returns:
            dict: 実効設定情報
        """
        all_bot = {
            f.name for f in dataclasses.fields(UnifiedBotConfig)
        }
        all_pm = {
            f.name for f in dataclasses.fields(PositionManagerConfig)
        }
        return {
            "symbol": symbol,
            "effective_bot_config": {
                f.name: getattr(bot_config, f.name)
                for f in dataclasses.fields(bot_config)
            },
            "effective_pm_config": {
                f.name: getattr(pm_config, f.name)
                for f in dataclasses.fields(pm_config)
            },
            "config_sources": {
                "bot_yaml_defined": sorted(
                    bot_yaml_keys & all_bot
                ),
                "bot_python_default": sorted(
                    all_bot - bot_yaml_keys
                ),
                "pm_yaml_defined": sorted(
                    pm_yaml_keys & all_pm
                ),
                "pm_python_default": sorted(
                    all_pm - pm_yaml_keys
                ),
            },
        }

    # ------------------------------------------------------------------
    # パブリック: プリセット設定読み込み
    # ------------------------------------------------------------------

    def load_preset_config(
        self,
        symbol: str = "USDJPY",
        multi_mode: bool = False,
    ) -> tuple[UnifiedBotConfig, PositionManagerConfig]:
        """3ファイル構成から統合設定を構築

        マージ優先順位:
          trading_defaults.yaml → symbol_overrides.yaml[symbol]

        後方互換:
          trading_defaults.yaml 未存在時は symbol_presets.yaml を使用。

        Args:
            symbol: 通貨ペアシンボル
            multi_mode: マルチペアモード（後方互換、現在未使用）

        Returns:
            tuple: (UnifiedBotConfig, PositionManagerConfig)
        """
        if self._use_new_config():
            return self._load_preset_from_new_config(symbol)
        # フォールバック: 旧symbol_presets.yaml
        return self._load_preset_from_legacy(symbol)

    def _load_preset_from_new_config(
        self,
        symbol: str,
    ) -> tuple[UnifiedBotConfig, PositionManagerConfig]:
        """新3ファイル構成からプリセットを読み込む

        Args:
            symbol: 通貨ペアシンボル

        Returns:
            tuple: (UnifiedBotConfig, PositionManagerConfig)
        """
        defaults = self._load_trading_defaults()
        overrides = self._load_symbol_overrides()
        preset = get_preset(symbol)

        # --- trading_defaults.yaml からセクション取得 ---
        signal_defaults = defaults.get("signal", {}) or {}
        risk_mgmt_defaults = defaults.get("risk_mgmt", {}) or {}
        filter_defaults = defaults.get("filter", {}) or {}
        pm_defaults = defaults.get("pm_config", {}) or {}

        # --- symbol_overrides.yaml からシンボル固有セクション ---
        sym_overrides = (
            (overrides.get("symbols") or {}).get(symbol) or {}
        )
        # シンボル固有のサブセクションを抽出（pop でネスト除去）
        sym_overrides = dict(sym_overrides)  # コピー
        sym_signal = sym_overrides.pop("signal", {}) or {}
        sym_risk_mgmt = sym_overrides.pop("risk_mgmt", {}) or {}
        sym_filter = sym_overrides.pop("filter", {}) or {}
        sym_pm = sym_overrides.pop("pm_config", {}) or {}

        # --- マージ: defaults ← symbol_overrides ---
        signal_merged = {**signal_defaults, **sym_signal}
        risk_mgmt_merged = {
            **risk_mgmt_defaults,
            **sym_risk_mgmt,
        }
        filter_merged = {**filter_defaults, **sym_filter}
        pm_merged = {**pm_defaults, **sym_pm}

        # 廃止フィールドの除去（後方互換）
        signal_merged.pop("multi_consensus_threshold", None)

        # --- SymbolPreset（通貨ペア固有値）---
        preset_bot_defaults = {
            "max_positions": preset.max_positions,
            "bonus_max_positions": preset.bonus_max_positions,
            "bonus_score_threshold": preset.bonus_score_threshold,
            "base_risk_pct": preset.base_risk_pct,
            "max_lot_per_trade": preset.max_lot_per_trade,
            "max_total_exposure_lot": (
                preset.max_total_exposure_lot
            ),
            "equity_floor_pct": preset.equity_floor_pct,
        }
        preset_pm_defaults = {
            "spread_pips": preset.spread_pips,
            "slippage_pips": preset.slippage_pips,
        }

        # --- Bot設定マージ（SymbolPreset → YAML order）---
        # SymbolPreset は最低優先、YAMLが上書き
        bot_data = {
            **preset_bot_defaults,
            **signal_merged,
            **risk_mgmt_merged,
            **filter_merged,
        }

        # YAML由来のキーを記録（ソーストラッキング用）
        bot_yaml_keys = set(bot_data.keys())

        bot_kwargs = _convert_tuple_fields(
            _filter_fields(bot_data, UnifiedBotConfig),
            UnifiedBotConfig,
        )

        # --- PM設定マージ ---
        pm_data = {**preset_pm_defaults, **pm_merged}
        pm_yaml_keys = set(pm_data.keys())
        pm_kwargs = _convert_tuple_fields(
            _filter_fields(pm_data, PositionManagerConfig),
            PositionManagerConfig,
        )

        # ソーストラッキングログ
        self._track_sources(bot_data, UnifiedBotConfig, symbol)
        self._track_sources(pm_data, PositionManagerConfig, symbol)

        return (
            UnifiedBotConfig(**bot_kwargs),
            PositionManagerConfig(**pm_kwargs),
        )

    def _load_preset_from_legacy(
        self,
        symbol: str,
    ) -> tuple[UnifiedBotConfig, PositionManagerConfig]:
        """旧symbol_presets.yaml からプリセットを読み込む（後方互換）

        Args:
            symbol: 通貨ペアシンボル

        Returns:
            tuple: (UnifiedBotConfig, PositionManagerConfig)
        """
        presets = self._load_presets_yaml()
        preset = get_preset(symbol)

        signal_defaults = presets.get("signal", {}) or {}
        risk_mgmt_defaults = presets.get("risk_mgmt", {}) or {}
        filter_defaults = presets.get("filter", {}) or {}
        pm_defaults = presets.get("pm_config", {}) or {}

        symbols = presets.get("symbols", {}) or {}
        sym_data = dict(symbols.get(symbol, {}) or {})
        sym_signal = sym_data.pop("signal", {}) or {}
        sym_risk_mgmt = sym_data.pop("risk_mgmt", {}) or {}
        sym_filter = sym_data.pop("filter", {}) or {}
        sym_pm = sym_data.pop("pm_config", {}) or {}

        signal_merged = {**signal_defaults, **sym_signal}
        risk_mgmt_merged = {
            **risk_mgmt_defaults,
            **sym_risk_mgmt,
        }
        filter_merged = {**filter_defaults, **sym_filter}
        pm_merged = {**pm_defaults, **sym_pm}

        signal_merged.pop("multi_consensus_threshold", None)

        preset_bot_defaults = {
            "max_positions": preset.max_positions,
            "bonus_max_positions": preset.bonus_max_positions,
            "bonus_score_threshold": preset.bonus_score_threshold,
            "base_risk_pct": preset.base_risk_pct,
            "max_lot_per_trade": preset.max_lot_per_trade,
            "max_total_exposure_lot": (
                preset.max_total_exposure_lot
            ),
            "equity_floor_pct": preset.equity_floor_pct,
        }
        preset_pm_defaults = {
            "spread_pips": preset.spread_pips,
            "slippage_pips": preset.slippage_pips,
        }

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

        pm_data = {**preset_pm_defaults, **pm_merged}
        pm_kwargs = _convert_tuple_fields(
            _filter_fields(pm_data, PositionManagerConfig),
            PositionManagerConfig,
        )

        return (
            UnifiedBotConfig(**bot_kwargs),
            PositionManagerConfig(**pm_kwargs),
        )

    def get_effective_config_info(
        self,
        symbol: str = "USDJPY",
    ) -> dict[str, Any]:
        """実効設定情報を返す（BT検証・ログ用）

        YAMLから来た値とPythonデフォルトを使用した値を分類して返す。
        バックテスト開始時に job_config.json へ記録するために使用する。

        Args:
            symbol: 通貨ペアシンボル

        Returns:
            dict: 実効設定情報（effective_bot_config, config_sources 等）
        """
        bot, pm = self.load_preset_config(symbol)

        if self._use_new_config():
            defaults = self._load_trading_defaults()
            overrides = self._load_symbol_overrides()
            sym_data = dict(
                (overrides.get("symbols") or {}).get(symbol) or {}
            )
            sym_data.pop("signal", None)
            sym_data.pop("risk_mgmt", None)
            sym_data.pop("filter", None)
            sym_data.pop("pm_config", None)

            signal_keys = set(
                (defaults.get("signal") or {}).keys()
            )
            risk_keys = set(
                (defaults.get("risk_mgmt") or {}).keys()
            )
            filter_keys = set(
                (defaults.get("filter") or {}).keys()
            )
            pm_keys = set(
                (defaults.get("pm_config") or {}).keys()
            )
            bot_yaml_keys = signal_keys | risk_keys | filter_keys
            pm_yaml_keys = pm_keys
        else:
            presets = self._load_presets_yaml()
            all_sections = ["signal", "risk_mgmt", "filter"]
            bot_yaml_keys = set()
            for sec in all_sections:
                bot_yaml_keys |= set(
                    (presets.get(sec) or {}).keys()
                )
            pm_yaml_keys = set(
                (presets.get("pm_config") or {}).keys()
            )

        return self._build_effective_config(
            bot,
            pm,
            bot_yaml_keys,
            pm_yaml_keys,
            symbol,
        )

    # ------------------------------------------------------------------
    # パブリック: EdgeValidatorConfig / PositionSizerConfig 読み込み
    # ------------------------------------------------------------------

    def load_edge_validator_config(self) -> Any:
        """EdgeValidatorConfig を trading_defaults.yaml から読み込む

        セクション未存在時はPythonデフォルト値を使用。

        Returns:
            EdgeValidatorConfig: エッジ検定設定
        """
        from autotrader.decision.unified.adaptive.edge_validator import (
            EdgeValidatorConfig,
        )

        section = {}
        if self._use_new_config():
            defaults = self._load_trading_defaults()
            section = defaults.get("edge_validator", {}) or {}
        else:
            # フォールバック: Pythonデフォルト
            logger.debug(
                "trading_defaults.yaml なし: EdgeValidatorConfig はPythonデフォルト使用"
            )

        if not section:
            return EdgeValidatorConfig()

        kwargs = _convert_tuple_fields(
            _filter_fields(section, EdgeValidatorConfig),
            EdgeValidatorConfig,
        )
        return EdgeValidatorConfig(**kwargs)

    def load_position_sizer_config(
        self,
        symbol: str = "",
    ) -> Any:
        """PositionSizerConfig を trading_defaults.yaml から読み込む

        セクション未存在時はPythonデフォルト値を使用。

        Args:
            symbol: 通貨ペアシンボル（pip_value自動計算用）

        Returns:
            PositionSizerConfig: ポジションサイザー設定
        """
        from autotrader.decision.unified.risk.position_sizer import (
            PositionSizerConfig,
        )

        section = {}
        if self._use_new_config():
            defaults = self._load_trading_defaults()
            section = defaults.get("position_sizer", {}) or {}

            # symbol_overrides.yaml からシンボル別の資金管理設定を上書き
            if symbol:
                overrides = self._load_symbol_overrides()
                sym_data = dict(
                    (overrides.get("symbols") or {}).get(symbol)
                    or {}
                )
                # シンボルプリセットの資金管理フィールドで上書き
                ps_fields = {
                    f.name
                    for f in dataclasses.fields(PositionSizerConfig)
                }
                for k in list(sym_data.keys()):
                    if k in ps_fields:
                        section[k] = sym_data[k]

        if not section:
            cfg = PositionSizerConfig(symbol=symbol) if symbol else PositionSizerConfig()
            return cfg

        if symbol:
            section["symbol"] = symbol

        kwargs = _convert_tuple_fields(
            _filter_fields(section, PositionSizerConfig),
            PositionSizerConfig,
        )
        return PositionSizerConfig(**kwargs)

    # ------------------------------------------------------------------
    # パブリック: ライブ・デモ設定読み込み
    # ------------------------------------------------------------------

    def load_live_config(
        self,
        filename: str = "live_trading.yaml",
    ) -> tuple[UnifiedBotConfig, PositionManagerConfig]:
        """ライブ設定をYAMLから読み込み

        trading_defaults.yaml + symbol_overrides.yaml をベースとし、
        modes.yaml[live] の値で上書きする。
        live_trading.yaml が存在する場合は後方互換として廃止警告を出す。

        Args:
            filename: 旧ライブ設定ファイル名（後方互換用）

        Returns:
            tuple: (UnifiedBotConfig, PositionManagerConfig)
        """
        if self._use_new_config():
            # 新構成: modes.yaml[live] で上書き
            base_bot, base_pm = self.load_preset_config()
            modes = self._load_yaml("modes.yaml")
            live_data = modes.get("live", {}) or {}
            if not live_data:
                return base_bot, base_pm

            live_bot = live_data.get("bot_config", {}) or {}
            live_pm = live_data.get("pm_config", {}) or {}
            return self._apply_overrides(
                base_bot, base_pm, live_bot, live_pm
            )

        # フォールバック: 旧live_trading.yaml
        path = self._config_dir / filename
        presets_path = self._config_dir / "symbol_presets.yaml"
        if not path.exists():
            if presets_path.exists():
                return self.load_preset_config()
            logger.warning(
                "設定ファイルなし、デフォルト使用: %s",
                path,
            )
            return UnifiedBotConfig(), PositionManagerConfig()

        import warnings

        warnings.warn(
            "live_trading.yaml は廃止予定です。"
            "trading_defaults.yaml に設定を移行してください。",
            DeprecationWarning,
            stacklevel=2,
        )

        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        symbol = raw.get("symbol", "USDJPY")
        base_bot, base_pm = self.load_preset_config(symbol)
        live_bot_data = raw.get("bot_config", {}) or {}
        live_pm_data = raw.get("pm_config", {}) or {}

        return self._apply_overrides(
            base_bot, base_pm, live_bot_data, live_pm_data
        )

    def load_demo_config(
        self,
        filename: str = "demo_trading.yaml",
    ) -> tuple[UnifiedBotConfig, PositionManagerConfig]:
        """デモトレード設定をYAMLから読み込み

        trading_defaults.yaml ベースに modes.yaml[demo] で上書きする。
        demo_trading.yaml 存在時は後方互換として読み込む。

        Args:
            filename: 旧デモ設定ファイル名（後方互換用）

        Returns:
            tuple: (UnifiedBotConfig, PositionManagerConfig)
        """
        if self._use_new_config():
            base_bot, base_pm = self.load_preset_config()
            modes = self._load_yaml("modes.yaml")
            demo_data = modes.get("demo", {}) or {}
            if not demo_data:
                # modes.yaml に demo セクションがない場合は旧ファイルを試みる
                return self._load_demo_from_file(filename)

            demo_bot = demo_data.get("bot_config", {}) or {}
            demo_pm = demo_data.get("pm_config", {}) or {}
            return self._apply_overrides(
                base_bot, base_pm, demo_bot, demo_pm
            )

        return self._load_demo_from_file(filename)

    def _load_demo_from_file(
        self,
        filename: str,
    ) -> tuple[UnifiedBotConfig, PositionManagerConfig]:
        """demo_trading.yaml からデモ設定を読み込む（後方互換）

        Args:
            filename: デモ設定ファイル名

        Returns:
            tuple: (UnifiedBotConfig, PositionManagerConfig)
        """
        path = self._config_dir / filename
        if not path.exists():
            logger.warning(
                "デモ設定ファイルなし、デモデフォルト使用: %s",
                path,
            )
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

    def _apply_overrides(
        self,
        base_bot: UnifiedBotConfig,
        base_pm: PositionManagerConfig,
        bot_override: dict,
        pm_override: dict,
    ) -> tuple[UnifiedBotConfig, PositionManagerConfig]:
        """ベース設定に上書きを適用する

        Args:
            base_bot: ベースBot設定
            base_pm: ベースPM設定
            bot_override: Bot設定上書き辞書
            pm_override: PM設定上書き辞書

        Returns:
            tuple: 上書き適用済み (UnifiedBotConfig, PositionManagerConfig)
        """
        if not bot_override and not pm_override:
            return base_bot, base_pm

        bot_dict = {
            f.name: getattr(base_bot, f.name)
            for f in dataclasses.fields(base_bot)
        }
        bot_dict.update(
            _filter_fields(bot_override, UnifiedBotConfig),
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
            _filter_fields(pm_override, PositionManagerConfig),
        )
        pm_kwargs = _convert_tuple_fields(
            pm_dict,
            PositionManagerConfig,
        )

        return (
            UnifiedBotConfig(**bot_kwargs),
            PositionManagerConfig(**pm_kwargs),
        )

    # ------------------------------------------------------------------
    # パブリック: 設定保存
    # ------------------------------------------------------------------

    def save_pm_config(
        self,
        pm_config: PositionManagerConfig,
        filename: str = "trading_defaults.yaml",
    ) -> None:
        """PM設定をYAMLに保存

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
        filename: str = "trading_defaults.yaml",
    ) -> None:
        """Bot設定をYAMLに保存

        Args:
            bot_config: 保存するBot設定
            filename: 設定ファイル名
        """
        self._save_section(
            "signal",
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

        if path.exists():
            with open(path, encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
        else:
            raw = {}

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
