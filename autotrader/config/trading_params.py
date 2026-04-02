"""トレードパラメータの単一ソース."""

from __future__ import annotations

import logging
import threading
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
        pip_unit: 1pipあたりの価格変動量（JPY=0.01, USD=0.0001）
        quote_ccy_rate: クォート通貨→口座通貨(JPY)変換レート
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
        use_position_manager: PositionManager有効化フラグ
        trailing_start_r: トレーリング開始R値
        trailing_atr_multiplier: ATRトレーリング倍率
        breakeven_at_1r: 1R到達時に建値移動するか
    """

    symbol: str = "USDJPY"
    pip_unit: float = 0.01
    quote_ccy_rate: float = 1.0
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
    base_risk_pct: float = 0.04
    max_lot_per_trade: float = 5.0
    max_total_exposure_lot: float = 5.0
    equity_floor_pct: float = 0.30
    # トレーリングストップ設定
    use_position_manager: bool = True
    trailing_start_r: float = 0.5
    trailing_atr_multiplier: float = 2.0
    breakeven_at_1r: bool = True
    # SoftGuardスプレッド閾値（ペア別、None=グローバルデフォルト2.0）
    sg_spread_threshold_pips: float | None = None
    # 通貨ペア別TFリスト（NoneでデフォルトTF使用）
    timeframes: list[str] | None = None

    def to_pm_config(self) -> PositionManagerConfig:
        """PositionManagerConfig を生成.

        Returns:
            PositionManagerConfig: PM設定
        """
        from autotrader.decision.unified.risk.position_manager import (  # noqa: PLC0415
            PositionManagerConfig,
        )

        return PositionManagerConfig(
            trailing_start_r=self.trailing_start_r,
            trailing_atr_multiplier=self.trailing_atr_multiplier,
            breakeven_at_1r=self.breakeven_at_1r,
            spread_pips=self.spread_pips,
            slippage_pips=self.slippage_pips,
            pip_unit=self.pip_unit,
        )

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
# parents[2] = プロジェクトルート（autotrader/config/ から2階層上）
_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
_DEFAULT_PRESET_PATH = _CONFIG_DIR / "symbol_presets.yaml"
_DEFAULT_OVERRIDES_PATH = _CONFIG_DIR / "symbol_overrides.yaml"
_lock = threading.Lock()
_preset_cache: dict[str, SymbolPreset] = {}
# ペア別 signal/filter/pm_config 上書き辞書
_symbol_overrides_cache: dict[
    str, dict[str, dict[str, Any]]
] = {}
_presets_loaded: bool = False
_loaded_path: Path | None = None  # 現在キャッシュしているYAMLパス


def _load_presets(path: Path | None = None) -> None:
    """YAMLからプリセットを読み込みキャッシュに格納.

    symbol_overrides.yaml（新構成）が存在すればそちらを優先し、
    存在しない場合は symbol_presets.yaml にフォールバックする。
    defaults をベースに symbols[X] で上書きするマージ方式。

    Args:
        path: YAMLファイルパス（None時は自動検出）
    """
    global _presets_loaded, _loaded_path
    import dataclasses  # noqa: PLC0415

    import yaml  # noqa: PLC0415

    # 新構成ファイルが存在すれば優先使用
    if path is None:
        if _DEFAULT_OVERRIDES_PATH.exists():
            target = _DEFAULT_OVERRIDES_PATH
        else:
            target = _DEFAULT_PRESET_PATH
    else:
        target = path

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

    # グローバルの signal/filter/risk_mgmt/pm_config デフォルト
    # symbol_overrides.yaml には存在しないが、後方互換で読み込む
    _global_signal: dict[str, Any] = raw.get("signal", {})
    _global_filter: dict[str, Any] = raw.get("filter", {})
    _global_risk: dict[str, Any] = raw.get("risk_mgmt", {})
    _global_pm: dict[str, Any] = raw.get("pm_config", {})

    valid = {f.name for f in dataclasses.fields(SymbolPreset)}
    for sym, overrides in symbols.items():
        _ovr = overrides or {}
        merged = {**defaults, **_ovr}
        # symbol フィールドを注入
        merged["symbol"] = sym
        filtered = {
            k: v for k, v in merged.items() if k in valid
        }
        _preset_cache[sym] = SymbolPreset(**filtered)

        # ペア別 signal/filter/risk_mgmt/pm_config 上書きを保存
        # グローバルデフォルト → ペア別で上書き
        _sym_signal = {
            **_global_signal,
            **(_ovr.get("signal") or {}),
        }
        _sym_filter = {
            **_global_filter,
            **(_ovr.get("filter") or {}),
        }
        _sym_risk = {
            **_global_risk,
            **(_ovr.get("risk_mgmt") or {}),
        }
        _sym_pm = {
            **_global_pm,
            **(_ovr.get("pm_config") or {}),
        }
        _symbol_overrides_cache[sym] = {
            "signal": _sym_signal,
            "filter": _sym_filter,
            "risk_mgmt": _sym_risk,
            "pm_config": _sym_pm,
        }

    _presets_loaded = True


def get_preset(
    symbol: str,
    path: Path | None = None,
) -> SymbolPreset:
    """シンボルプリセットを取得.

    未定義シンボルは USDJPY 相当のデフォルト値を返す。
    path が現在のキャッシュと異なる場合は再読み込みを行う。
    path 未指定時は symbol_overrides.yaml → symbol_presets.yaml の順で自動検出。

    Args:
        symbol: 通貨ペア名
        path: YAMLファイルパス（None時は自動検出）

    Returns:
        SymbolPreset: プリセット設定
    """
    with _lock:
        if path is None:
            effective_path = (
                _DEFAULT_OVERRIDES_PATH
                if _DEFAULT_OVERRIDES_PATH.exists()
                else _DEFAULT_PRESET_PATH
            )
        else:
            effective_path = path
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


def get_symbol_overrides(
    symbol: str,
    path: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """ペア別の signal/filter/pm_config 上書き辞書を取得.

    YAMLのグローバル signal/filter/pm_config をベースに、
    symbols[X] 内の signal/filter/pm_config で上書きしたもの。

    Args:
        symbol: 通貨ペア名
        path: YAMLファイルパス

    Returns:
        dict: {"signal": {...}, "filter": {...}, "pm_config": {...}}
    """
    with _lock:
        if path is None:
            effective_path = (
                _DEFAULT_OVERRIDES_PATH
                if _DEFAULT_OVERRIDES_PATH.exists()
                else _DEFAULT_PRESET_PATH
            )
        else:
            effective_path = path
        if not _presets_loaded or effective_path != _loaded_path:
            _preset_cache.clear()
            _symbol_overrides_cache.clear()
            _load_presets(path)
        return _symbol_overrides_cache.get(symbol, {
            "signal": {},
            "filter": {},
            "risk_mgmt": {},
            "pm_config": {},
        })


def reload_presets(path: Path | None = None) -> None:
    """プリセットキャッシュをリセットして再読み込み.

    テストや設定変更後に使用。

    Args:
        path: YAMLファイルパス（None時はデフォルトパス）
    """
    global _presets_loaded, _loaded_path
    with _lock:
        _preset_cache.clear()
        _symbol_overrides_cache.clear()
        _presets_loaded = False
        _loaded_path = None
        _load_presets(path)


# -------------------------------------------------------------------
# ヘルパー関数（プリセット未登録シンボルでも動作）
# -------------------------------------------------------------------


def get_pip_unit(symbol: str) -> float:
    """シンボルからpip単位を取得.

    プリセット登録済みならプリセット値、未登録なら通貨名から推定。

    Args:
        symbol: 通貨ペア名（例: "USDJPY", "EURUSD"）

    Returns:
        float: pip単位（JPYペア=0.01, その他=0.0001）
    """
    if _presets_loaded and symbol in _preset_cache:
        return _preset_cache[symbol].pip_unit
    return 0.01 if "JPY" in symbol.upper() else 0.0001


def get_pip_value(symbol: str) -> float:
    """シンボルから1lot/1pipあたりのJPY価値を取得.

    公式: 100,000 × pip_unit × quote_ccy_rate

    プリセット登録済みならプリセット値、未登録なら通貨名から推定。

    Args:
        symbol: 通貨ペア名（例: "USDJPY", "EURUSD"）

    Returns:
        float: 1lot/1pipあたりのJPY価値
    """
    if _presets_loaded and symbol in _preset_cache:
        p = _preset_cache[symbol]
        return 100_000 * p.pip_unit * p.quote_ccy_rate
    # プリセット未ロード時のフォールバック
    pip_unit = 0.01 if "JPY" in symbol.upper() else 0.0001
    rate = 1.0 if "JPY" in symbol.upper() else 150.0
    return 100_000 * pip_unit * rate


def get_quote_ccy_rate(symbol: str) -> float:
    """クォート通貨→口座通貨(JPY)変換レート概算を取得.

    プリセット登録済みならプリセット値、未登録なら通貨名から推定。

    Args:
        symbol: 通貨ペア名（例: "USDJPY", "EURUSD"）

    Returns:
        float: 変換レート（JPYペア=1.0, USDペア≈150.0）
    """
    if _presets_loaded and symbol in _preset_cache:
        return _preset_cache[symbol].quote_ccy_rate
    return 1.0 if "JPY" in symbol.upper() else 150.0
