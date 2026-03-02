"""戦略レジストリ

デコレータベースの戦略自動登録機構。
新戦略追加時に StrategyPool のハードコードを不要にする。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import BaseStrategy

# 登録済み戦略クラスの辞書（名前 -> クラス）
_STRATEGY_REGISTRY: dict[str, type[BaseStrategy]] = {}


def register_strategy(name: str):
    """戦略クラスを自動登録するデコレータ

    Args:
        name: 戦略名（StrategyId.value と一致させること）

    Returns:
        デコレータ関数

    Raises:
        ValueError: 同名の戦略が既に登録済みの場合
    """
    def decorator(cls: type[BaseStrategy]) -> type[BaseStrategy]:
        if name in _STRATEGY_REGISTRY:
            msg = f"戦略 '{name}' は既に登録済み: {_STRATEGY_REGISTRY[name]}"
            raise ValueError(msg)
        _STRATEGY_REGISTRY[name] = cls
        return cls
    return decorator


def get_registered_strategies() -> list[BaseStrategy]:
    """登録済み全戦略のインスタンスを返す

    Returns:
        list[BaseStrategy]: 戦略インスタンスのリスト
    """
    return [cls() for cls in _STRATEGY_REGISTRY.values()]


def get_strategy_class(name: str) -> type[BaseStrategy] | None:
    """名前から戦略クラスを取得

    Args:
        name: 戦略名

    Returns:
        type[BaseStrategy] | None: 戦略クラス（未登録時None）
    """
    return _STRATEGY_REGISTRY.get(name)


def get_registry() -> dict[str, type[BaseStrategy]]:
    """レジストリのコピーを返す（テスト用）

    Returns:
        dict[str, type[BaseStrategy]]: 登録済み戦略の辞書
    """
    return dict(_STRATEGY_REGISTRY)
