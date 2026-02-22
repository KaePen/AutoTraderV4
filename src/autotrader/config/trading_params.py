"""トレードパラメータの単一ソース."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TradingParams:
    """全トレードパラメータの一元管理.

    Attributes:
        spread_pips: スプレッド（pips）
        default_sl_pips: デフォルトSL（pips）
        default_tp_pips: デフォルトTP（pips）
        pip_value: 1pipあたりの価値（円）
        min_lot: 最小ロット
        max_lot: 最大ロット
        slippage_pips: スリッページ（pips）
        commission_per_lot: ロットあたり手数料
    """

    spread_pips: float = 1.5
    default_sl_pips: float = 20.0
    default_tp_pips: float = 40.0
    pip_value: float = 100.0
    min_lot: float = 0.01
    max_lot: float = 10.0
    slippage_pips: float = 0.5
    commission_per_lot: float = 0.0


# デフォルトインスタンス
DEFAULT_TRADING_PARAMS = TradingParams()


# ---------------------------------------------------------------------------
# シンボルプリセット
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SymbolPreset:
    """通貨ペア別取引パラメータプリセット.

    Attributes:
        symbol: 通貨ペア名
        pip_value: 1pipあたりの価値（円）
        spread_pips: スプレッド（pips）
        slippage_pips: スリッページ（pips）
        default_sl_pips: デフォルトSL（pips）
        default_tp_pips: デフォルトTP（pips）
        min_lot: 最小ロット
        max_lot: 最大ロット
        commission_per_lot: ロットあたり手数料
        max_positions: 最大ポジション数（通常時）
        bonus_max_positions: ボーナスポジション数
        bonus_score_threshold: ボーナス発動スコア閾値
        base_risk_pct: 基本リスク割合
        max_lot_per_trade: トレードあたり最大ロット
        max_total_exposure_lot: 合計エクスポージャー上限ロット
        equity_floor_pct: 資産フロア割合
    """

    symbol: str = "USDJPY"
    pip_value: float = 100.0
    spread_pips: float = 1.5
    slippage_pips: float = 0.5
    default_sl_pips: float = 20.0
    default_tp_pips: float = 40.0
    min_lot: float = 0.01
    max_lot: float = 10.0
    commission_per_lot: float = 0.0
    max_positions: int = 2
    bonus_max_positions: int = 1
    bonus_score_threshold: float = 7.0
    base_risk_pct: float = 0.02
    max_lot_per_trade: float = 2.0
    max_total_exposure_lot: float = 5.0
    equity_floor_pct: float = 0.30

    def to_trading_params(self) -> TradingParams:
        """TradingParams に変換（後方互換）.

        Returns:
            TradingParams: 変換済みトレードパラメータ
        """
        return TradingParams(
            spread_pips=self.spread_pips,
            default_sl_pips=self.default_sl_pips,
            default_tp_pips=self.default_tp_pips,
            pip_value=self.pip_value,
            min_lot=self.min_lot,
            max_lot=self.max_lot,
            slippage_pips=self.slippage_pips,
            commission_per_lot=self.commission_per_lot,
        )


# プリセットキャッシュ
# parents[3] = プロジェクトルート（src/autotrader/config/ から3階層上）
_DEFAULT_PRESET_PATH = (
    Path(__file__).resolve().parents[3] / "config" / "symbol_presets.yaml"
)
_preset_cache: dict[str, SymbolPreset] = {}
_presets_loaded: bool = False
_loaded_path: Path | None = None  # 現在キャッシュしているYAMLパス


def _load_presets(path: Path | None = None) -> None:
    """YAMLからプリセットを読み込みキャッシュに格納.

    defaults をベースに symbols[X] で上書きするマージ方式。

    Args:
        path: YAMLファイルパス（None時はデフォルトパス）
    """
    global _presets_loaded, _loaded_path
    import dataclasses  # noqa: PLC0415
    import yaml  # noqa: PLC0415

    target = path or _DEFAULT_PRESET_PATH
    _loaded_path = target

    if not target.exists():
        logger.warning(
            "シンボルプリセットファイルなし: %s", target,
        )
        _presets_loaded = True
        return

    with open(target, encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    defaults: dict[str, Any] = raw.get("defaults", {})
    symbols: dict[str, Any] = raw.get("symbols", {})

    valid = {f.name for f in dataclasses.fields(SymbolPreset)}
    for sym, overrides in symbols.items():
        merged = {**defaults, **(overrides or {})}
        # symbol フィールドを注入
        merged["symbol"] = sym
        filtered = {k: v for k, v in merged.items() if k in valid}
        _preset_cache[sym] = SymbolPreset(**filtered)

    _presets_loaded = True


def get_preset(
    symbol: str,
    path: Path | None = None,
) -> SymbolPreset:
    """シンボルプリセットを取得.

    未定義シンボルは USDJPY 相当のデフォルト値を返す。
    path が現在のキャッシュと異なる場合は再読み込みを行う。

    Args:
        symbol: 通貨ペア名
        path: YAMLファイルパス（None時はデフォルトパス）

    Returns:
        SymbolPreset: プリセット設定
    """
    effective_path = path if path is not None else _DEFAULT_PRESET_PATH
    # 未ロードまたは異なるパスが指定された場合は再読み込み
    if not _presets_loaded or effective_path != _loaded_path:
        _preset_cache.clear()
        _load_presets(path)
    if symbol in _preset_cache:
        return _preset_cache[symbol]
    logger.warning(
        "プリセット未定義シンボル、デフォルト使用: %s", symbol,
    )
    return SymbolPreset(symbol=symbol)


def reload_presets(path: Path | None = None) -> None:
    """プリセットキャッシュをリセットして再読み込み.

    テストや設定変更後に使用。

    Args:
        path: YAMLファイルパス（None時はデフォルトパス）
    """
    global _presets_loaded, _loaded_path
    _preset_cache.clear()
    _presets_loaded = False
    _loaded_path = None
    _load_presets(path)
