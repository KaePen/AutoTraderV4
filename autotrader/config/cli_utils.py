"""Config dataclass から argparse 引数を自動生成するユーティリティ

dataclass フィールドの型・デフォルト値から CLI 引数を自動登録し、
CLI で明示指定された値だけを dict に抽出して frozen dataclass の
コンストラクタに渡す。

使用例::

    from autotrader.config.cli_utils import (
        add_config_args,
        collect_overrides,
    )

    add_config_args(parser, UnifiedBotConfig, prefix="bot")
    args = parser.parse_args()
    overrides = collect_overrides(args, UnifiedBotConfig, prefix="bot")
    config = UnifiedBotConfig(**overrides)
"""

from __future__ import annotations

import argparse
import dataclasses
import logging

import yaml

logger = logging.getLogger(__name__)


def _resolve_field_type(
    field: dataclasses.Field,  # type: ignore[type-arg]
) -> type | None:
    """フィールドの型を解決する。

    ``from __future__ import annotations`` 下では型ヒントが
    文字列になるため、文字列パースで対応する。

    Args:
        field: dataclass フィールド

    Returns:
        type | None: 解決された型。解決不能なら None。
    """
    ftype = field.type
    if isinstance(ftype, str):
        # 'float | None' 等の文字列型ヒント
        if "bool" in ftype:
            return bool
        if "float" in ftype:
            return float
        if "int" in ftype:
            return int
        if "str" in ftype:
            return str
        return None
    return ftype  # type: ignore[return-value]


def _add_field_arg(
    parser: argparse.ArgumentParser,
    field: dataclasses.Field,  # type: ignore[type-arg]
    prefix: str,
) -> None:
    """dataclass フィールドを argparse 引数として登録する。

    Args:
        parser: argparse パーサー
        field: dataclass フィールド
        prefix: 引数名のプレフィックス
    """
    name = field.name
    cli_name = name.replace("_", "-")
    if prefix:
        full_cli = f"--{prefix}-{cli_name}"
    else:
        full_cli = f"--{cli_name}"

    default = field.default
    if default is dataclasses.MISSING:
        default = None

    ftype = _resolve_field_type(field)
    if ftype is None:
        return  # 解決不能 → スキップ

    # bool 型: BooleanOptionalAction で --xxx / --no-xxx ペア
    if ftype is bool:
        parser.add_argument(
            full_cli,
            action=argparse.BooleanOptionalAction,
            default=None,  # None = CLI未指定
            help=f"{name} (デフォルト: {default})",
        )
        return

    # float / int / str
    type_map: dict[type, type] = {
        float: float,
        int: int,
        str: str,
    }
    if ftype not in type_map:
        return  # list, dict, tuple 等の複雑型はスキップ

    parser.add_argument(
        full_cli,
        type=type_map[ftype],
        default=None,
        help=f"{name} (デフォルト: {default})",
    )


def add_config_args(
    parser: argparse.ArgumentParser,
    config_cls: type,
    *,
    prefix: str = "",
    exclude: set[str] | None = None,
) -> None:
    """dataclass のフィールドから argparse 引数を自動生成する。

    Args:
        parser: argparse パーサー
        config_cls: dataclass クラス
        prefix: 引数名のプレフィックス（例: "bot" → --bot-xxx）
        exclude: 除外するフィールド名のセット
    """
    if not dataclasses.is_dataclass(config_cls):
        raise TypeError(
            f"{config_cls.__name__} は dataclass ではありません"
        )

    exclude = exclude or set()

    for f in dataclasses.fields(config_cls):
        if f.name in exclude:
            continue
        if f.name.startswith("_"):
            continue

        _add_field_arg(parser, f, prefix)


def collect_overrides(
    args: argparse.Namespace,
    config_cls: type,
    *,
    prefix: str = "",
    exclude: set[str] | None = None,
) -> dict[str, object]:
    """argparse 結果から CLI で明示指定された値だけを dict に抽出。

    None の値は除外されるので、frozen dataclass の ``**kwargs`` に
    そのまま渡せる。

    Args:
        args: argparse.Namespace
        config_cls: dataclass クラス
        prefix: add_config_args と同じプレフィックス
        exclude: 除外するフィールド名のセット

    Returns:
        dict[str, object]: フィールド名 → 明示指定値
    """
    exclude = exclude or set()
    overrides: dict[str, object] = {}

    for f in dataclasses.fields(config_cls):
        if f.name in exclude:
            continue
        if f.name.startswith("_"):
            continue

        # argparse が格納する属性名: prefix_field_name
        if prefix:
            attr_key = f"{prefix}_{f.name}"
        else:
            attr_key = f.name

        if hasattr(args, attr_key):
            value = getattr(args, attr_key)
            if value is not None:
                overrides[f.name] = value

    return overrides


def load_yaml_config(path: str) -> dict[str, object]:
    """YAML 設定ファイルを読み込む。

    Args:
        path: YAML ファイルパス

    Returns:
        dict[str, object]: 設定辞書
    """
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(
            f"YAML のトップレベルは dict である必要があります: {path}"
        )
    return data


def apply_yaml_overrides(
    yaml_data: dict[str, object],
    section: str,
    config_cls: type,
) -> dict[str, object]:
    """YAML データの特定セクションから config overrides を抽出。

    Args:
        yaml_data: load_yaml_config の戻り値
        section: セクション名（例: "bot", "pm"）
        config_cls: dataclass クラス

    Returns:
        dict[str, object]: フィールド名 → 値
    """
    section_data = yaml_data.get(section, {})
    if not isinstance(section_data, dict):
        return {}

    valid_fields = {
        f.name for f in dataclasses.fields(config_cls)
    }
    overrides: dict[str, object] = {}
    for key, value in section_data.items():
        if key in valid_fields:
            overrides[key] = value
        else:
            logger.warning(
                "[YAML] %s.%s は %s に存在しないフィールドです",
                section,
                key,
                config_cls.__name__,
            )
    return overrides


def apply_dot_overrides(
    overrides_list: list[str],
) -> dict[str, dict[str, object]]:
    """``--override`` のドット記法を解析する。

    例: ``["bot.consensus_threshold=10.0", "pm.trailing_start_r=2.0"]``

    Args:
        overrides_list: ドット記法の上書き指定リスト

    Returns:
        dict[str, dict[str, object]]: セクション → {フィールド→値}
    """
    result: dict[str, dict[str, object]] = {}
    for item in overrides_list:
        if "=" not in item:
            logger.warning(
                "[Override] '=' が含まれていません: %s", item
            )
            continue
        key_part, value_str = item.split("=", 1)
        parts = key_part.split(".", 1)
        if len(parts) != 2:
            logger.warning(
                "[Override] 'section.field=value' 形式が必要: %s",
                item,
            )
            continue
        section, field_name = parts

        # 型推論: bool → int → float → str
        value: object
        if value_str.lower() in ("true", "false"):
            value = value_str.lower() == "true"
        else:
            try:
                value = int(value_str)
            except ValueError:
                try:
                    value = float(value_str)
                except ValueError:
                    value = value_str

        result.setdefault(section, {})[field_name] = value
    return result
