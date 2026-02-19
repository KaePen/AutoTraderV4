"""YAML設定ファイルローダー"""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path

import yaml

from autotrader.decision.unified.config import UnifiedBotConfig
from autotrader.decision.unified.position_manager import (
    PositionManagerConfig,
)

logger = logging.getLogger(__name__)

# プロジェクトルートの config/ ディレクトリ
_DEFAULT_CONFIG_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent / "config"
)


def _filter_fields(
    data: dict, cls: type,
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
                k, cls.__name__,
            )
    return filtered


def _convert_tuple_fields(
    data: dict, cls: type,
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

    Attributes:
        _config_dir: 設定ファイルディレクトリ
    """

    def __init__(
        self, config_dir: Path | None = None,
    ) -> None:
        """初期化

        Args:
            config_dir: 設定ファイルディレクトリ
        """
        self._config_dir = config_dir or _DEFAULT_CONFIG_DIR

    def load_live_config(
        self, filename: str = "live_trading.yaml",
    ) -> tuple[UnifiedBotConfig, PositionManagerConfig]:
        """ライブ設定をYAMLから読み込み

        Args:
            filename: 設定ファイル名

        Returns:
            tuple: (UnifiedBotConfig, PositionManagerConfig)
        """
        path = self._config_dir / filename
        if not path.exists():
            logger.warning(
                "設定ファイルなし、デフォルト使用: %s", path,
            )
            return UnifiedBotConfig(), PositionManagerConfig()

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

    def load_demo_config(
        self, filename: str = "demo_trading.yaml",
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
                "デモ設定ファイルなし、デモデフォルト使用: %s", path,
            )
            # demo_mode=Trueのデフォルト
            bot_defaults = {
                f.name: f.default
                for f in dataclasses.fields(UnifiedBotConfig)
                if f.default is not dataclasses.MISSING
            }
            bot_defaults["demo_mode"] = True
            bot_defaults["consensus_day_trade_threshold"] = 4.5
            return UnifiedBotConfig(**bot_defaults), PositionManagerConfig()

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
            "pm_config", pm_config, filename,
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
            "bot_config", bot_config, filename,
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
                    item.value if hasattr(item, "value")
                    else item
                    for item in v
                ]
            elif isinstance(v, list):
                serializable[k] = [
                    item.value if hasattr(item, "value")
                    else item
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
            "設定保存: %s → %s", section, path,
        )
