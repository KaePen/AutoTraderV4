"""通年コンパウンドBT リプレイエンジン

既存のtrades.csv（固定エクイティBT結果）から
複利エクイティでロットを再計算し、通年パフォーマンスを算出する。

エントリー/エグジット判定は変えず、ロットだけを
compound_equity / initial_equity の比率でスケーリングする。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CompoundReplayConfig:
    """リプレイ設定

    Attributes:
        initial_equity: 初期資金
        min_lot: 最小ロット
        max_lot: 最大ロット（1トレードあたり）
        global_max_exposure_lot: グローバル最大ロット
    """

    initial_equity: float = 1_000_000.0
    min_lot: float = 0.01
    max_lot: float = 100.0
    global_max_exposure_lot: float = 12.0


@dataclass
class CompoundReplayResult:
    """リプレイ結果

    Attributes:
        initial_equity: 初期資金
        final_equity: 最終資金
        total_trades: 総トレード数
        net_profit: 純利益
        win_rate: 勝率（%）
        profit_factor: プロフィットファクター
        max_drawdown_pct: 最大ドローダウン（%）
        sharpe_ratio: シャープレシオ（月次）
        monthly_plus_rate: 月間勝率（%）
        yearly_details: 年別サマリ
        pair_details: ペア別サマリ
        equity_curve: エクイティカーブ
        trades_df: リプレイ済みトレード
    """

    initial_equity: float = 0.0
    final_equity: float = 0.0
    total_trades: int = 0
    net_profit: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    monthly_plus_rate: float = 0.0
    yearly_details: list[dict] = field(
        default_factory=list,
    )
    pair_details: list[dict] = field(
        default_factory=list,
    )
    equity_curve: list[dict] = field(
        default_factory=list,
    )
    trades_df: pd.DataFrame | None = None


def replay_compound(
    trades_df: pd.DataFrame,
    config: CompoundReplayConfig | None = None,
) -> CompoundReplayResult:
    """トレード履歴をコンパウンドエクイティでリプレイ

    Args:
        trades_df: trades.csv相当のDataFrame
            必須列: entry_time, exit_time, symbol,
            lot, pips, profit_loss, sl_pips
        config: リプレイ設定

    Returns:
        CompoundReplayResult: リプレイ結果
    """
    if config is None:
        config = CompoundReplayConfig()

    if trades_df.empty:
        return CompoundReplayResult(
            initial_equity=config.initial_equity,
            final_equity=config.initial_equity,
        )

    df = trades_df.copy()

    # 必須列チェック
    required = [
        "entry_time", "exit_time", "lot",
        "pips", "profit_loss",
    ]
    for col in required:
        if col not in df.columns:
            raise ValueError(
                f"必須列 '{col}' がありません"
            )

    # 型変換
    df["entry_time"] = pd.to_datetime(df["entry_time"])
    df["exit_time"] = pd.to_datetime(df["exit_time"])
    df["lot"] = pd.to_numeric(df["lot"], errors="coerce")
    df["pips"] = pd.to_numeric(
        df["pips"], errors="coerce",
    )
    df["profit_loss"] = pd.to_numeric(
        df["profit_loss"], errors="coerce",
    )

    # exit_timeでソート（決済順にリプレイ）
    df = df.sort_values("exit_time").reset_index(
        drop=True,
    )

    # リプレイ実行
    equity = config.initial_equity
    peak_equity = equity
    max_dd_pct = 0.0

    # 月次PnL追跡
    monthly_pnl: dict[str, float] = {}
    # 年次追跡
    yearly_tracker: dict[int, dict] = {}
    # ペア追跡
    pair_tracker: dict[str, dict] = {}
    # エクイティカーブ
    equity_points: list[dict] = []

    # リプレイ結果列
    replay_lots: list[float] = []
    replay_pnls: list[float] = []
    equity_befores: list[float] = []
    equity_afters: list[float] = []

    gross_profit = 0.0
    gross_loss = 0.0
    wins = 0

    for _, row in df.iterrows():
        orig_lot = row["lot"]
        orig_pnl = row["profit_loss"]

        # ロットスケーリング
        if orig_lot > 0:
            scale = equity / config.initial_equity
            replay_lot = orig_lot * scale
            # ロット制限
            replay_lot = max(
                config.min_lot,
                min(replay_lot, config.max_lot),
            )
            replay_lot = min(
                replay_lot,
                config.global_max_exposure_lot,
            )
            # PnLスケーリング
            replay_pnl = (
                orig_pnl * (replay_lot / orig_lot)
            )
        else:
            replay_lot = 0.0
            replay_pnl = 0.0

        eq_before = equity
        equity += replay_pnl
        # 最低エクイティ保護
        equity = max(equity, config.initial_equity * 0.01)

        replay_lots.append(replay_lot)
        replay_pnls.append(replay_pnl)
        equity_befores.append(eq_before)
        equity_afters.append(equity)

        # DD計算
        if equity > peak_equity:
            peak_equity = equity
        dd_pct = (
            (peak_equity - equity) / peak_equity * 100
            if peak_equity > 0
            else 0.0
        )
        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct

        # 勝敗
        if replay_pnl > 0:
            gross_profit += replay_pnl
            wins += 1
        elif replay_pnl < 0:
            gross_loss += abs(replay_pnl)

        # 月次PnL
        exit_time = row["exit_time"]
        month_key = exit_time.strftime("%Y-%m")
        monthly_pnl[month_key] = (
            monthly_pnl.get(month_key, 0.0) + replay_pnl
        )

        # 年次追跡
        yr = exit_time.year
        if yr not in yearly_tracker:
            yearly_tracker[yr] = {
                "year": yr,
                "initial_equity": eq_before,
                "trades": 0,
                "pnl": 0.0,
                "wins": 0,
                "gross_profit": 0.0,
                "gross_loss": 0.0,
                "peak": eq_before,
                "max_dd": 0.0,
            }
        yt = yearly_tracker[yr]
        yt["trades"] += 1
        yt["pnl"] += replay_pnl
        if replay_pnl > 0:
            yt["wins"] += 1
            yt["gross_profit"] += replay_pnl
        elif replay_pnl < 0:
            yt["gross_loss"] += abs(replay_pnl)
        _yr_eq = yt["initial_equity"] + yt["pnl"]
        if _yr_eq > yt["peak"]:
            yt["peak"] = _yr_eq
        _yr_dd = (
            (yt["peak"] - _yr_eq) / yt["peak"] * 100
            if yt["peak"] > 0
            else 0.0
        )
        if _yr_dd > yt["max_dd"]:
            yt["max_dd"] = _yr_dd

        # ペア追跡
        sym = row.get("symbol", "UNKNOWN")
        if sym not in pair_tracker:
            pair_tracker[sym] = {
                "symbol": sym,
                "trades": 0,
                "wins": 0,
                "gross_profit": 0.0,
                "gross_loss": 0.0,
            }
        pt = pair_tracker[sym]
        pt["trades"] += 1
        if replay_pnl > 0:
            pt["wins"] += 1
            pt["gross_profit"] += replay_pnl
        elif replay_pnl < 0:
            pt["gross_loss"] += abs(replay_pnl)

        # エクイティカーブ（100トレードごと + 最後）
        if len(equity_points) == 0 or (
            len(replay_lots) % 100 == 0
        ):
            equity_points.append({
                "time": str(exit_time),
                "equity": round(equity, 2),
                "trades": len(replay_lots),
            })

    # 最終ポイント
    if df.shape[0] > 0:
        equity_points.append({
            "time": str(df.iloc[-1]["exit_time"]),
            "equity": round(equity, 2),
            "trades": len(replay_lots),
        })

    # リプレイ列をDFに追加
    df["replay_lot"] = replay_lots
    df["replay_pnl"] = replay_pnls
    df["replay_equity_before"] = equity_befores
    df["replay_equity_after"] = equity_afters

    # メトリクス算出
    total_trades = len(df)
    net_profit = equity - config.initial_equity
    win_rate = (
        wins / total_trades * 100
        if total_trades > 0
        else 0.0
    )
    profit_factor = (
        gross_profit / gross_loss
        if gross_loss > 0
        else float("inf")
    )

    # 月間勝率
    positive_months = sum(
        1 for v in monthly_pnl.values() if v > 0
    )
    total_months = len(monthly_pnl)
    monthly_plus_rate = (
        positive_months / total_months * 100
        if total_months > 0
        else 0.0
    )

    # シャープレシオ（月次）
    sharpe = _calc_monthly_sharpe(monthly_pnl)

    # 年別details
    yearly_details = []
    for yr in sorted(yearly_tracker.keys()):
        yt = yearly_tracker[yr]
        _final = yt["initial_equity"] + yt["pnl"]
        _wr = (
            yt["wins"] / yt["trades"] * 100
            if yt["trades"] > 0
            else 0.0
        )
        _pf = (
            yt["gross_profit"] / yt["gross_loss"]
            if yt["gross_loss"] > 0
            else float("inf")
        )
        _ret = (
            yt["pnl"] / yt["initial_equity"] * 100
            if yt["initial_equity"] > 0
            else 0.0
        )
        yearly_details.append({
            "year": yr,
            "trades": yt["trades"],
            "pnl": round(yt["pnl"], 2),
            "initial_equity": round(
                yt["initial_equity"], 2,
            ),
            "final_equity": round(_final, 2),
            "return_pct": round(_ret, 2),
            "win_rate": round(_wr, 2),
            "profit_factor": round(_pf, 3),
            "max_drawdown": round(yt["max_dd"], 2),
        })

    # ペア別details
    pair_details = []
    for sym in sorted(pair_tracker.keys()):
        pt = pair_tracker[sym]
        _wr = (
            pt["wins"] / pt["trades"] * 100
            if pt["trades"] > 0
            else 0.0
        )
        _pf = (
            pt["gross_profit"] / pt["gross_loss"]
            if pt["gross_loss"] > 0
            else float("inf")
        )
        _net = pt["gross_profit"] - pt["gross_loss"]
        pair_details.append({
            "symbol": sym,
            "trades": pt["trades"],
            "net_profit": round(_net, 2),
            "win_rate": round(_wr, 2),
            "profit_factor": round(_pf, 3),
        })

    logger.info(
        "Compound replay完了: %d trades, "
        "equity %.0f → %.0f (%.1f%%)",
        total_trades,
        config.initial_equity,
        equity,
        net_profit / config.initial_equity * 100,
    )

    return CompoundReplayResult(
        initial_equity=config.initial_equity,
        final_equity=round(equity, 2),
        total_trades=total_trades,
        net_profit=round(net_profit, 2),
        win_rate=round(win_rate, 2),
        profit_factor=round(profit_factor, 3),
        max_drawdown_pct=round(max_dd_pct, 2),
        sharpe_ratio=round(sharpe, 3),
        monthly_plus_rate=round(monthly_plus_rate, 1),
        yearly_details=yearly_details,
        pair_details=pair_details,
        equity_curve=equity_points,
        trades_df=df,
    )


def _calc_monthly_sharpe(
    monthly_pnl: dict[str, float],
) -> float:
    """月次シャープレシオを計算

    Args:
        monthly_pnl: 月キー→PnLの辞書

    Returns:
        float: 年率シャープレシオ
    """
    if len(monthly_pnl) < 2:
        return 0.0

    returns = list(monthly_pnl.values())
    avg = sum(returns) / len(returns)
    var = sum((r - avg) ** 2 for r in returns) / (
        len(returns) - 1
    )
    std = var**0.5
    if std == 0:
        return 0.0

    # 年率化: √12
    return (avg / std) * (12**0.5)
