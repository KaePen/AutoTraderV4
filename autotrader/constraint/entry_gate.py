"""エントリーゲート（BT/ライブ共通）

シグナル生成後、注文実行前のエントリー可否判定を一元管理する。
データI/Oは呼び出し側が担当し、本モジュールは純粋なロジックのみ提供する。
"""

from __future__ import annotations

from dataclasses import dataclass

from autotrader.core.entities import SignalType


@dataclass(frozen=True)
class EntryGateContext:
    """エントリーゲート判定に必要な状態スナップショット

    呼び出し側（BT: year_runner / ライブ: engine）がI/Oで取得した値を
    このデータクラスに詰めて渡す。本クラス自体はI/O依存を持たない。

    Attributes:
        signal_direction: シグナル方向
        consensus_score: コンセンサススコア（None時はボーナス判定スキップ）
        symbol_position_count: 対象シンボルの現在ポジション数
        global_position_count: 全シンボル合計ポジション数（シングルペアBTでは0）
        global_exposure_lot: 全シンボル合計ロット数（シングルペアBTでは0.0）
        jpy_same_direction_count: JPYペア同方向ポジション数（非JPYでは0）
        max_positions: シンボル別最大ポジション数
        bonus_max_positions: ボーナス枠（0=無効）
        bonus_score_threshold: ボーナス発動閾値
        global_max_positions: グローバル最大ポジション数（0=無制限）
        global_max_exposure_lot: グローバル最大ロット（0.0=無制限）
        max_same_direction_jpy: JPY同方向上限（0=無制限）
        is_jpy_pair: JPYペアか否か
        current_spread_pips: 現在スプレッド（pips）
        spread_threshold_pips: スプレッド閾値（None=ゲート無効）
        dd_emergency_active: DD緊急停止中か
        margin_usage_pct: マージン使用率%（0-100）
        margin_limit_pct: マージン上限%（0=チェック無効）
        jpy_sl_circuit_breaker_active: JPY SLサーキットブレーカー発動中か
        prev_same_dir_exit_was_stag: 直前の同方向トレードがSTAGNATION exitか
    """

    signal_direction: SignalType
    consensus_score: float | None

    symbol_position_count: int
    global_position_count: int
    global_exposure_lot: float
    jpy_same_direction_count: int

    max_positions: int
    bonus_max_positions: int
    bonus_score_threshold: float
    global_max_positions: int
    global_max_exposure_lot: float
    max_same_direction_jpy: int
    is_jpy_pair: bool

    current_spread_pips: float
    spread_threshold_pips: float | None

    dd_emergency_active: bool

    margin_usage_pct: float
    margin_limit_pct: float

    # JPY SLサーキットブレーカー: 直近N分以内に同方向JPYペアのSLが発生
    jpy_sl_circuit_breaker_active: bool = False
    # STAGNATION後再エントリーブロック
    prev_same_dir_exit_was_stag: bool = False


@dataclass(frozen=True)
class EntryGateResult:
    """エントリーゲート判定結果

    Attributes:
        allowed: エントリー許可
        deny_reason: 拒否理由（人間向けメッセージ）
        deny_code: 拒否コード（プログラム用識別子）
    """

    allowed: bool
    deny_reason: str | None = None
    deny_code: str | None = None


_ALLOWED = EntryGateResult(allowed=True)


def _deny(code: str, reason: str) -> EntryGateResult:
    return EntryGateResult(allowed=False, deny_reason=reason, deny_code=code)


class EntryGateChecker:
    """BT/ライブ共通のエントリーゲート判定

    純粋ロジックのみ。I/O・副作用は一切持たない。
    ゲート順序はライブ側の現行順序（engine.py:1489-1602）に準拠。
    """

    def evaluate(self, ctx: EntryGateContext) -> EntryGateResult:
        """エントリー可否を判定する

        Args:
            ctx: 判定に必要な状態スナップショット

        Returns:
            EntryGateResult: 判定結果
        """
        # 1. DD緊急停止
        if ctx.dd_emergency_active:
            return _deny("dd_emergency", "DD緊急停止中")

        # 2. シンボルポジション上限（ボーナス枠含む）
        eff_max = ctx.max_positions
        if (
            ctx.bonus_max_positions > 0
            and ctx.consensus_score is not None
            and ctx.consensus_score >= ctx.bonus_score_threshold
        ):
            eff_max += ctx.bonus_max_positions
        if ctx.symbol_position_count >= eff_max:
            return _deny(
                "symbol_position_limit",
                f"ポジション上限 {ctx.symbol_position_count}/{eff_max}",
            )

        # 3. グローバルポジション上限
        if (
            ctx.global_max_positions > 0
            and ctx.global_position_count >= ctx.global_max_positions
        ):
            return _deny(
                "global_position_limit",
                f"グローバルポジション上限 "
                f"{ctx.global_position_count}/{ctx.global_max_positions}",
            )

        # 4. グローバルエクスポージャー上限
        if (
            ctx.global_max_exposure_lot > 0
            and ctx.global_exposure_lot >= ctx.global_max_exposure_lot
        ):
            return _deny(
                "global_exposure_limit",
                f"ロット上限 "
                f"{ctx.global_exposure_lot:.2f}"
                f"/{ctx.global_max_exposure_lot:.1f}",
            )

        # 5. JPY同方向制限
        if (
            ctx.is_jpy_pair
            and ctx.max_same_direction_jpy > 0
            and ctx.jpy_same_direction_count >= ctx.max_same_direction_jpy
        ):
            return _deny(
                "jpy_direction_limit",
                f"JPY {ctx.signal_direction.value}上限 "
                f"{ctx.jpy_same_direction_count}"
                f"/{ctx.max_same_direction_jpy}",
            )

        # 5b. JPY SLサーキットブレーカー
        if ctx.is_jpy_pair and ctx.jpy_sl_circuit_breaker_active:
            return _deny(
                "jpy_sl_circuit_breaker",
                f"JPY {ctx.signal_direction.value} "
                f"SLサーキットブレーカー発動中",
            )

        # 5c. STAGNATION後同方向ブロック
        if ctx.prev_same_dir_exit_was_stag:
            return _deny(
                "post_stagnation_block",
                "直前同方向STAGNATION後のエントリー抑制",
            )

        # 6. スプレッドゲート
        if (
            ctx.spread_threshold_pips is not None
            and ctx.current_spread_pips > ctx.spread_threshold_pips
        ):
            return _deny(
                "spread_gate",
                f"スプレッド超過 {ctx.current_spread_pips:.1f}"
                f" > {ctx.spread_threshold_pips:.1f} pips",
            )

        # 7. マージンチェック
        if (
            ctx.margin_limit_pct > 0
            and ctx.margin_usage_pct >= ctx.margin_limit_pct
        ):
            return _deny(
                "insufficient_margin",
                f"マージン不足 {ctx.margin_usage_pct:.1f}%"
                f" >= {ctx.margin_limit_pct:.1f}%",
            )

        return _ALLOWED
