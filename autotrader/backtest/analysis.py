"""バックテスト・ライブトレード分析ユーティリティ

BT が出力する trades.csv とライブDB(tmp/autotrader.db)のtradesテーブルを
共通スキーマで読み込み、exit_reason/symbol/月別/方向別の集計と
分布を返す。改善活動の課題抽出フェーズで使用する。

Usage:
    from autotrader.backtest.analysis import (
        load_bt_trades, load_live_trades, analyze_trades, compare_summaries,
    )

    df = load_bt_trades("results/bt_xxx/trades.csv")
    report = analyze_trades(df)
    print(report.to_markdown())

    # マルチペアBT結果(JSON)の A/B 比較
    diff = compare_summaries("tmp/mp_q3.json", "tmp/mp_q3_htfblock.json")
    print(diff)
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd


# ============================================================
# 読み込み
# ============================================================

def load_bt_trades(csv_path: str | Path) -> pd.DataFrame:
    """BT trades.csv を共通スキーマに正規化"""
    df = pd.read_csv(csv_path)
    # 想定カラム: symbol, signal_type, entry_price, exit_price,
    #   profit_loss, profit_loss_pips, exit_reason, opened_at, closed_at
    if "opened_at" in df.columns:
        df["opened_at"] = pd.to_datetime(df["opened_at"])
    if "closed_at" in df.columns:
        df["closed_at"] = pd.to_datetime(df["closed_at"])
    return df


def load_live_trades(
    db_path: str | Path = "tmp/autotrader.db",
    only_closed: bool = True,
) -> pd.DataFrame:
    """ライブDBの trades テーブルを共通スキーマで読み込み"""
    con = sqlite3.connect(str(db_path))
    where = "WHERE is_open = 0" if only_closed else ""
    df = pd.read_sql_query(
        f"SELECT * FROM trades {where} ORDER BY opened_at",
        con,
        parse_dates=["opened_at", "closed_at"],
    )
    con.close()
    return df


# ============================================================
# 集計
# ============================================================

@dataclass
class GroupStats:
    """グループ別統計"""
    name: str
    n: int
    wins: int
    losses: int
    win_rate: float
    pnl_total: float
    pnl_mean: float
    pnl_pips_mean: float
    pf: float
    avg_win: float
    avg_loss: float


def _stats(df: pd.DataFrame, name: str) -> GroupStats:
    n = len(df)
    if n == 0:
        return GroupStats(
            name=name, n=0, wins=0, losses=0, win_rate=0.0,
            pnl_total=0.0, pnl_mean=0.0, pnl_pips_mean=0.0,
            pf=0.0, avg_win=0.0, avg_loss=0.0,
        )
    pnl = df["profit_loss"].fillna(0)
    pips = (
        df["profit_loss_pips"].fillna(0)
        if "profit_loss_pips" in df.columns
        else pd.Series([0.0] * n)
    )
    wins = int((pnl > 0).sum())
    losses = int((pnl < 0).sum())
    gw = float(pnl[pnl > 0].sum())
    gl = float(-pnl[pnl < 0].sum())
    pf = gw / gl if gl > 0 else (float("inf") if gw > 0 else 0.0)
    return GroupStats(
        name=name,
        n=n,
        wins=wins,
        losses=losses,
        win_rate=wins / n,
        pnl_total=float(pnl.sum()),
        pnl_mean=float(pnl.mean()),
        pnl_pips_mean=float(pips.mean()),
        pf=pf,
        avg_win=float(pnl[pnl > 0].mean()) if wins else 0.0,
        avg_loss=float(pnl[pnl < 0].mean()) if losses else 0.0,
    )


@dataclass
class TradeReport:
    overall: GroupStats
    by_symbol: list[GroupStats] = field(default_factory=list)
    by_signal_type: list[GroupStats] = field(default_factory=list)
    by_exit_reason: list[GroupStats] = field(default_factory=list)
    by_month: list[GroupStats] = field(default_factory=list)
    worst_trades: pd.DataFrame = field(default_factory=pd.DataFrame)

    def to_markdown(self, max_worst: int = 10) -> str:
        lines: list[str] = []

        def fmt(g: GroupStats) -> str:
            return (
                f"| {g.name} | {g.n} | {g.wins} | {g.losses} | "
                f"{g.win_rate:.1%} | {g.pnl_total:>12,.0f} | "
                f"{g.pnl_mean:>10,.0f} | {g.pnl_pips_mean:>6.1f} | "
                f"{g.pf:>5.2f} |"
            )

        header = (
            "| 区分 | n | 勝 | 負 | 勝率 | PnL合計 | "
            "PnL平均 | pips平均 | PF |"
        )
        sep = "|---|--:|--:|--:|--:|--:|--:|--:|--:|"

        lines.append("## 全体")
        lines.extend([header, sep, fmt(self.overall)])

        if self.by_symbol:
            lines.append("\n## ペア別")
            lines.extend([header, sep])
            lines.extend(fmt(g) for g in self.by_symbol)

        if self.by_signal_type:
            lines.append("\n## 方向別 (BUY/SELL)")
            lines.extend([header, sep])
            lines.extend(fmt(g) for g in self.by_signal_type)

        if self.by_exit_reason:
            lines.append("\n## Exit Reason別")
            lines.extend([header, sep])
            lines.extend(fmt(g) for g in self.by_exit_reason)

        if self.by_month:
            lines.append("\n## 月別")
            lines.extend([header, sep])
            lines.extend(fmt(g) for g in self.by_month)

        if not self.worst_trades.empty:
            lines.append(f"\n## ワースト {max_worst} トレード")
            cols = [
                c for c in [
                    "symbol", "signal_type", "entry_price", "exit_price",
                    "profit_loss", "profit_loss_pips", "exit_reason",
                    "opened_at", "closed_at",
                ] if c in self.worst_trades.columns
            ]
            tbl = self.worst_trades.head(max_worst)[cols]
            lines.append("| " + " | ".join(cols) + " |")
            lines.append("|" + "|".join(["---"] * len(cols)) + "|")
            for _, r in tbl.iterrows():
                vals: list[str] = []
                for c in cols:
                    v = r[c]
                    if isinstance(v, float):
                        vals.append(f"{v:,.2f}" if abs(v) < 100 else f"{v:,.0f}")
                    else:
                        vals.append(str(v))
                lines.append("| " + " | ".join(vals) + " |")

        return "\n".join(lines)


def compare_summaries(
    baseline_path: str | Path,
    improved_path: str | Path,
) -> str:
    """マルチペア BT サマリ JSON 2件を比較して markdown 差分レポートを返す"""
    base = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
    imp = json.loads(Path(improved_path).read_text(encoding="utf-8"))

    def _delta(a: float, b: float) -> str:
        diff = b - a
        sign = "+" if diff >= 0 else ""
        return f"{a:.4f} → {b:.4f} ({sign}{diff:.4f})"

    lines: list[str] = []
    lines.append(f"## A/B 比較: {Path(baseline_path).name} vs {Path(improved_path).name}\n")
    lines.append("### 全体")
    lines.append("| 指標 | Baseline → Improved | Δ |")
    lines.append("|---|---|---|")
    metrics = [
        ("trades", "trades", 0),
        ("win_rate", "勝率", 4),
        ("profit_factor", "PF", 3),
        ("max_drawdown_pct", "Max DD %", 3),
        ("net_pnl", "Net PnL", 0),
    ]
    for key, label, decimals in metrics:
        a = float(base.get(key, 0))
        b = float(imp.get(key, 0))
        d = b - a
        sign = "+" if d >= 0 else ""
        a_s = f"{a:,.{decimals}f}"
        b_s = f"{b:,.{decimals}f}"
        d_s = f"{sign}{d:,.{decimals}f}"
        lines.append(f"| {label} | {a_s} → {b_s} | {d_s} |")

    lines.append("\n### ブロック発動")
    for k in ("blocked_global", "blocked_per_pair", "blocked_direction", "blocked_exposure"):
        a = base.get(k, 0)
        b = imp.get(k, 0)
        lines.append(f"- {k}: {a} → {b} ({'+' if (b - a) >= 0 else ''}{b - a})")

    lines.append("\n### ペア別 (PnL 差分)")
    lines.append("| Pair | A trades / WR / PnL | B trades / WR / PnL | Δ PnL |")
    lines.append("|---|---|---|---|")
    pairs_a = base.get("per_pair", {})
    pairs_b = imp.get("per_pair", {})
    all_pairs = sorted(set(pairs_a.keys()) | set(pairs_b.keys()))
    for sym in all_pairs:
        pa = pairs_a.get(sym, {})
        pb = pairs_b.get(sym, {})
        a_pnl = pa.get("pnl", 0)
        b_pnl = pb.get("pnl", 0)
        delta = b_pnl - a_pnl
        lines.append(
            f"| {sym} "
            f"| {pa.get('trades',0)} / {pa.get('win_rate',0):.1%} / {a_pnl:,.0f} "
            f"| {pb.get('trades',0)} / {pb.get('win_rate',0):.1%} / {b_pnl:,.0f} "
            f"| {'+' if delta >= 0 else ''}{delta:,.0f} |"
        )

    return "\n".join(lines)


def match_bt_live_trades(
    bt_csv: str | Path,
    live_db: str | Path = "tmp/autotrader.db",
    time_tolerance_min: int = 60,
) -> dict[str, "pd.DataFrame"]:
    """BT trades.csv とライブ DB を時刻+シンボル+方向で突合

    Args:
        bt_csv: multi_pair_runner.export_trades_csv() の出力
        live_db: ライブ DB (default tmp/autotrader.db)
        time_tolerance_min: 同一エントリー判定の時刻許容(分)

    Returns:
        {
          "matched": pd.DataFrame,   # BTにもLiveにもあったペア
          "bt_only": pd.DataFrame,   # BTのみ (Liveでブロック/障害?)
          "live_only": pd.DataFrame, # Liveのみ (BTで生成されず)
        }
    """
    bt = pd.read_csv(bt_csv)
    bt["opened_at"] = pd.to_datetime(bt["opened_at"], errors="coerce", utc=True)
    bt["closed_at"] = pd.to_datetime(bt["closed_at"], errors="coerce", utc=True)
    live = load_live_trades(live_db)
    if "opened_at" in live.columns and live["opened_at"].dt.tz is None:
        live["opened_at"] = live["opened_at"].dt.tz_localize("UTC")
    if "closed_at" in live.columns and not live["closed_at"].isna().all():
        if live["closed_at"].dt.tz is None:
            live["closed_at"] = live["closed_at"].dt.tz_localize("UTC")

    tol = pd.Timedelta(minutes=time_tolerance_min)
    matched_rows = []
    bt_used: set[int] = set()
    live_used: set[int] = set()

    # ライブ側を主軸に: 各ライブトレードに対し、許容時間内の同シンボル・同方向 BT を探す
    for li, lr in live.iterrows():
        sym = lr["symbol"]
        sig = lr["signal_type"]
        l_open = lr["opened_at"]
        if pd.isna(l_open):
            continue
        candidates = bt[
            (bt["symbol"] == sym)
            & (bt["signal_type"] == sig)
            & (~bt.index.isin(bt_used))
            & ((bt["opened_at"] - l_open).abs() <= tol)
        ]
        if candidates.empty:
            continue
        # 最も時刻が近い候補
        best_idx = (candidates["opened_at"] - l_open).abs().idxmin()
        bt_used.add(best_idx)
        live_used.add(li)
        br = candidates.loc[best_idx]
        matched_rows.append({
            "symbol": sym,
            "signal_type": sig,
            "live_opened_at": l_open,
            "bt_opened_at": br["opened_at"],
            "live_entry": lr["entry_price"],
            "bt_entry": br["entry_price"],
            "live_exit": lr["exit_price"],
            "bt_exit": br["exit_price"],
            "live_sl": lr["stop_loss"],
            "bt_sl": br["stop_loss"],
            "live_tp": lr["take_profit"],
            "bt_tp": br["take_profit"],
            "live_pnl": lr["profit_loss"],
            "bt_pnl": br["profit_loss"],
            "live_pnl_pips": lr["profit_loss_pips"],
            "bt_pnl_pips": br["profit_loss_pips"],
            "live_exit_reason": lr["exit_reason"],
            "bt_exit_reason": br["exit_reason"],
            "live_score": lr.get("entry_own_score"),
            "bt_score": br.get("consensus_score"),
        })

    matched = pd.DataFrame(matched_rows)
    bt_only = bt[~bt.index.isin(bt_used)].copy()
    live_only = live[~live.index.isin(live_used)].copy()
    return {"matched": matched, "bt_only": bt_only, "live_only": live_only}


def analyze_trades(df: pd.DataFrame, max_worst: int = 10) -> TradeReport:
    """トレード DataFrame を多軸集計"""
    overall = _stats(df, "全体")

    by_symbol = [
        _stats(g, sym) for sym, g in df.groupby("symbol")
    ] if "symbol" in df.columns else []

    by_signal_type = [
        _stats(g, str(sig)) for sig, g in df.groupby("signal_type")
    ] if "signal_type" in df.columns else []

    by_exit_reason = [
        _stats(g, str(r)) for r, g in df.groupby("exit_reason")
    ] if "exit_reason" in df.columns else []

    by_month: list[GroupStats] = []
    if "closed_at" in df.columns:
        d = df.dropna(subset=["closed_at"]).copy()
        d["_ym"] = d["closed_at"].dt.to_period("M").astype(str)
        by_month = [_stats(g, ym) for ym, g in d.groupby("_ym")]

    worst = pd.DataFrame()
    if "profit_loss" in df.columns:
        worst = df.sort_values("profit_loss").head(max_worst)

    return TradeReport(
        overall=overall,
        by_symbol=by_symbol,
        by_signal_type=by_signal_type,
        by_exit_reason=by_exit_reason,
        by_month=by_month,
        worst_trades=worst,
    )
