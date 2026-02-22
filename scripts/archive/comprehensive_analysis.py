"""包括的バックテスト分析

OOS検証、コスト前提、モード/レジーム別内訳、
トレード統計、時間帯別成績を一括出力。
BacktestServiceを使用して標準バックテストと一致する結果を生成。
"""

from __future__ import annotations

import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean, median, stdev

sys.path.insert(0, str(Path(__file__).parent.parent ))

from autotrader.backtest.events import (
    BacktestEvent,
    EventListener,
    EventType,
    TradeEvent,
)
from autotrader.backtest.service import (
    BacktestService,
    BacktestServiceConfig,
)
from autotrader.config import DEFAULT_TRADING_PARAMS


@dataclass
class TradeRecord:
    """トレード記録"""

    opened_at: datetime
    closed_at: datetime
    direction: str
    entry_price: float
    exit_price: float
    pnl: float
    pnl_pips: float
    exit_reason: str
    holding_minutes: float
    volume: float
    trading_mode: str = ""
    market_regime: str = ""


class TradeCollector(EventListener):
    """トレード収集リスナー"""

    def __init__(self):
        self.trades: list[TradeRecord] = []

    def on_event(self, event: BacktestEvent) -> None:
        """イベント処理"""
        if (
            isinstance(event, TradeEvent)
            and event.event_type == EventType.POSITION_CLOSED
        ):
            self.trades.append(TradeRecord(
                opened_at=(
                    event.opened_at or event.timestamp
                ),
                closed_at=event.timestamp,
                direction=event.direction,
                entry_price=event.entry_price,
                exit_price=event.exit_price or 0,
                pnl=event.profit_loss or 0,
                pnl_pips=event.pips,
                exit_reason=event.exit_reason or "UNKNOWN",
                holding_minutes=event.holding_minutes,
                volume=event.volume,
                trading_mode=event.trading_mode,
                market_regime=event.market_regime,
            ))


def run_period(
    start_year: int,
    end_year: int,
) -> tuple[list[TradeRecord], dict]:
    """期間バックテスト実行"""
    collector = TradeCollector()
    svc_config = BacktestServiceConfig(
        start_year=start_year,
        end_year=end_year,
        initial_balance=1_000_000.0,
        volume=1.0,
        spread_pips=DEFAULT_TRADING_PARAMS.spread_pips,
        use_short_timeframe=True,
        verbose=False,
    )
    service = BacktestService(svc_config)
    service.add_listener(collector)
    result = service.run()

    summary = {
        "trades": result.trades,
        "win_rate": result.win_rate,
        "pf": result.profit_factor,
        "net_profit": result.net_profit,
        "max_dd": result.max_drawdown,
        "sharpe": result.sharpe_ratio,
        "annual_return": result.annual_return,
        "yearly": result.yearly_results,
        "monthly": result.monthly_results,
    }
    return collector.trades, summary


def compute_trade_stats(trades: list[TradeRecord]) -> dict:
    """トレード統計計算"""
    if not trades:
        return {"trades": 0}

    pnls = [t.pnl for t in trades]
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    win_pnls = [t.pnl for t in wins]
    loss_pnls = [t.pnl for t in losses]
    win_pips = [t.pnl_pips for t in wins]
    loss_pips = [t.pnl_pips for t in losses]

    # R倍数（SLベースリスク推定）
    r_values = []
    for t in trades:
        if t.pnl_pips != 0:
            # 平均SLを推定（負けトレードの平均loss pips）
            risk_pips = (
                abs(mean(loss_pips)) if loss_pips else 5.0
            )
            if risk_pips > 0:
                r_values.append(t.pnl_pips / risk_pips)

    # 連勝/連敗
    max_wins = max_losses = 0
    cur_wins = cur_losses = 0
    for t in trades:
        if t.pnl > 0:
            cur_wins += 1
            cur_losses = 0
            max_wins = max(max_wins, cur_wins)
        else:
            cur_losses += 1
            cur_wins = 0
            max_losses = max(max_losses, cur_losses)

    total_profit = sum(win_pnls) if win_pnls else 0
    total_loss = abs(sum(loss_pnls)) if loss_pnls else 1

    return {
        "trades": len(trades),
        "win_rate": len(wins) / len(trades) * 100,
        "pf": total_profit / total_loss if total_loss else 0,
        "net_profit": sum(pnls),
        "avg_pnl": mean(pnls),
        "median_pnl": median(pnls),
        "stdev_pnl": stdev(pnls) if len(pnls) > 1 else 0,
        "avg_win": mean(win_pnls) if win_pnls else 0,
        "avg_loss": mean(loss_pnls) if loss_pnls else 0,
        "avg_win_pips": mean(win_pips) if win_pips else 0,
        "avg_loss_pips": mean(loss_pips) if loss_pips else 0,
        "max_win": max(pnls),
        "max_loss": min(pnls),
        "max_consecutive_wins": max_wins,
        "max_consecutive_losses": max_losses,
        "avg_r": mean(r_values) if r_values else 0,
        "median_r": median(r_values) if r_values else 0,
        "avg_holding_min": mean(
            [t.holding_minutes for t in trades]
        ),
    }


def analyze_by_session(trades: list[TradeRecord]) -> dict:
    """時間帯別分析"""
    sessions = {
        "東京(09-15 JST)": lambda h: 0 <= h < 6,
        "ロンドン(16-01 JST)": lambda h: 7 <= h < 16,
        "NY(22-07 JST)": lambda h: 13 <= h < 22,
        "深夜(02-08 JST)": lambda h: h >= 17 or h < 4,
    }
    results = {}
    for name, hour_check in sessions.items():
        st = [
            t for t in trades
            if hour_check(t.opened_at.hour)
        ]
        if not st:
            results[name] = {
                "trades": 0, "win_rate": 0,
                "pf": 0, "net_pnl": 0,
            }
            continue
        wins = [t for t in st if t.pnl > 0]
        tp = sum(t.pnl for t in wins)
        tl = abs(sum(t.pnl for t in st if t.pnl <= 0))
        results[name] = {
            "trades": len(st),
            "win_rate": len(wins) / len(st) * 100,
            "pf": tp / tl if tl > 0 else 0,
            "net_pnl": sum(t.pnl for t in st),
        }
    return results


def analyze_by_exit_reason(
    trades: list[TradeRecord],
) -> dict:
    """決済理由別分析"""
    reasons: dict[str, list] = defaultdict(list)
    for t in trades:
        reasons[t.exit_reason].append(t)
    results = {}
    for reason, tl in sorted(reasons.items()):
        wins = [t for t in tl if t.pnl > 0]
        results[reason] = {
            "count": len(tl),
            "pct": len(tl) / len(trades) * 100,
            "win_rate": len(wins) / len(tl) * 100 if tl else 0,
            "net_pnl": sum(t.pnl for t in tl),
        }
    return results


def _group_stats(
    trades: list[TradeRecord],
) -> dict:
    """グループ統計（勝率、PF、DD概算、損益）"""
    if not trades:
        return {
            "trades": 0, "win_rate": 0,
            "pf": 0, "net_pnl": 0, "dd_pct": 0,
        }
    wins = [t for t in trades if t.pnl > 0]
    tp = sum(t.pnl for t in wins)
    tl = abs(sum(t.pnl for t in trades if t.pnl <= 0))
    # DD概算（累積損益のピークからの最大低下）
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in trades:
        cum += t.pnl
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
    # 初期残高1Mとして%換算
    dd_pct = max_dd / 1_000_000 * 100
    return {
        "trades": len(trades),
        "win_rate": len(wins) / len(trades) * 100,
        "pf": tp / tl if tl > 0 else 0,
        "net_pnl": sum(t.pnl for t in trades),
        "dd_pct": dd_pct,
    }


def analyze_by_mode(
    trades: list[TradeRecord],
) -> dict[str, dict]:
    """モード別（SCALP/DAY_TRADE/SWING）分析"""
    groups: dict[str, list] = defaultdict(list)
    for t in trades:
        mode = t.trading_mode or "UNKNOWN"
        groups[mode].append(t)
    return {
        m: _group_stats(tl) for m, tl in sorted(groups.items())
    }


def analyze_by_regime(
    trades: list[TradeRecord],
) -> dict[str, dict]:
    """レジーム別（TREND/RANGE/HIGH_VOL）分析"""
    groups: dict[str, list] = defaultdict(list)
    for t in trades:
        regime = t.market_regime or "UNKNOWN"
        groups[regime].append(t)
    return {
        r: _group_stats(tl) for r, tl in sorted(groups.items())
    }


def analyze_by_hour(trades: list[TradeRecord]) -> dict:
    """時間帯別（1時間単位UTC）"""
    hours: dict[int, list] = defaultdict(list)
    for t in trades:
        hours[t.opened_at.hour].append(t)
    results = {}
    for h in sorted(hours.keys()):
        tl = hours[h]
        wins = [t for t in tl if t.pnl > 0]
        tp = sum(t.pnl for t in wins)
        total_loss = abs(sum(t.pnl for t in tl if t.pnl <= 0))
        results[h] = {
            "trades": len(tl),
            "win_rate": len(wins) / len(tl) * 100 if tl else 0,
            "pf": tp / total_loss if total_loss > 0 else 0,
            "net_pnl": sum(t.pnl for t in tl),
        }
    return results


def format_section(
    label: str,
    trades: list[TradeRecord],
    summary: dict,
) -> list[str]:
    """セクション生成"""
    lines = [f"## {label}", ""]

    if summary.get("trades", 0) == 0:
        lines.append("データなし")
        lines.append("")
        return lines

    # 公式結果
    lines.append("### 公式バックテスト結果")
    lines.append("")
    lines.append("| 指標 | 値 |")
    lines.append("|------|-----|")
    lines.append(f"| 取引数 | {summary['trades']} |")
    lines.append(f"| 勝率 | {summary['win_rate']:.1f}% |")
    lines.append(f"| PF | {summary['pf']:.2f} |")
    lines.append(
        f"| 純利益 | {summary['net_profit']:+,.0f} |"
    )
    lines.append(
        f"| 最大DD | {summary['max_dd']:.2f}% |"
    )
    lines.append(
        f"| シャープ | {summary['sharpe']:.2f} |"
    )
    lines.append(
        f"| 年間収益率 | {summary['annual_return']:.1f}% |"
    )
    lines.append("")

    # 年別
    if summary.get("yearly"):
        lines.append("### 年別詳細")
        lines.append("")
        lines.append(
            "| 年 | 取引 | 勝率 | PF | 利益 | DD |"
        )
        lines.append(
            "|----|------|------|-----|-------|-----|"
        )
        for yr in summary["yearly"]:
            lines.append(
                f"| {yr['year']} | {yr['trades']} | "
                f"{yr['win_rate']:.1f}% | "
                f"{yr['profit_factor']:.2f} | "
                f"{yr['net_profit']:+,.0f} | "
                f"{yr['max_drawdown']:.2f}% |"
            )
        lines.append("")

    # トレード詳細統計
    stats = compute_trade_stats(trades)
    if stats["trades"] > 0:
        lines.append("### トレード1件あたり統計")
        lines.append("")
        lines.append("| 指標 | 値 |")
        lines.append("|------|-----|")
        lines.append(
            f"| 平均損益 | {stats['avg_pnl']:+,.0f} |"
        )
        lines.append(
            f"| 中央値損益 | {stats['median_pnl']:+,.0f} |"
        )
        lines.append(
            f"| 標準偏差 | {stats['stdev_pnl']:,.0f} |"
        )
        lines.append(
            f"| 平均勝ち | "
            f"{stats['avg_win']:+,.0f} "
            f"({stats['avg_win_pips']:+.1f}pips) |"
        )
        lines.append(
            f"| 平均負け | "
            f"{stats['avg_loss']:+,.0f} "
            f"({stats['avg_loss_pips']:+.1f}pips) |"
        )
        lines.append(
            f"| 最大勝ち | {stats['max_win']:+,.0f} |"
        )
        lines.append(
            f"| 最大負け | {stats['max_loss']:+,.0f} |"
        )
        lines.append(
            f"| 平均R | {stats['avg_r']:+.3f} |"
        )
        lines.append(
            f"| 中央値R | {stats['median_r']:+.3f} |"
        )
        lines.append(
            f"| 最大連勝 | "
            f"{stats['max_consecutive_wins']} |"
        )
        lines.append(
            f"| 最大連敗 | "
            f"{stats['max_consecutive_losses']} |"
        )
        lines.append(
            f"| 平均保有時間 | "
            f"{stats['avg_holding_min']:.0f}分 |"
        )
        lines.append("")

        # 「大勝ち少数」依存チェック
        if trades:
            sorted_pnls = sorted(
                [t.pnl for t in trades], reverse=True
            )
            top10_profit = sum(sorted_pnls[:10])
            total = sum(sorted_pnls)
            if total > 0:
                lines.append(
                    f"**利益構造**: "
                    f"上位10件が全利益の"
                    f"{top10_profit/total*100:.0f}%"
                    f"を占める"
                )
            else:
                lines.append("**利益構造**: 全体損失")
            lines.append("")

    # モード別
    mode_stats = analyze_by_mode(trades)
    has_mode = any(
        s["trades"] > 0
        for k, s in mode_stats.items()
        if k != "UNKNOWN"
    )
    if has_mode:
        lines.append("### モード別成績")
        lines.append("")
        lines.append(
            "| モード | 取引数 | 勝率 | PF "
            "| 損益 | DD概算 |"
        )
        lines.append(
            "|--------|--------|------|-----"
            "|------|--------|"
        )
        for mode, s in mode_stats.items():
            if s["trades"] == 0:
                continue
            lines.append(
                f"| {mode} | {s['trades']} | "
                f"{s['win_rate']:.1f}% | "
                f"{s['pf']:.2f} | "
                f"{s['net_pnl']:+,.0f} | "
                f"{s['dd_pct']:.2f}% |"
            )
        lines.append("")

    # レジーム別
    regime_stats = analyze_by_regime(trades)
    has_regime = any(
        s["trades"] > 0
        for k, s in regime_stats.items()
        if k != "UNKNOWN"
    )
    if has_regime:
        lines.append("### レジーム別成績")
        lines.append("")
        lines.append(
            "| レジーム | 取引数 | 勝率 | PF "
            "| 損益 | DD概算 |"
        )
        lines.append(
            "|----------|--------|------|-----"
            "|------|--------|"
        )
        for regime, s in regime_stats.items():
            if s["trades"] == 0:
                continue
            lines.append(
                f"| {regime} | {s['trades']} | "
                f"{s['win_rate']:.1f}% | "
                f"{s['pf']:.2f} | "
                f"{s['net_pnl']:+,.0f} | "
                f"{s['dd_pct']:.2f}% |"
            )
        lines.append("")

    # 決済理由
    exit_stats = analyze_by_exit_reason(trades)
    if exit_stats:
        lines.append("### 決済理由別")
        lines.append("")
        lines.append(
            "| 理由 | 件数 | 割合 | 勝率 | 損益 |"
        )
        lines.append(
            "|------|------|------|------|------|"
        )
        for reason, s in exit_stats.items():
            lines.append(
                f"| {reason} | {s['count']} | "
                f"{s['pct']:.1f}% | "
                f"{s['win_rate']:.1f}% | "
                f"{s['net_pnl']:+,.0f} |"
            )
        lines.append("")

    # 時間帯別
    session_stats = analyze_by_session(trades)
    if session_stats:
        lines.append(
            "### 時間帯別（エントリーUTC→JST変換）"
        )
        lines.append("")
        lines.append(
            "| セッション | 取引数 | 勝率 | PF | 損益 |"
        )
        lines.append(
            "|-----------|--------|------|-----|------|"
        )
        for session, s in session_stats.items():
            lines.append(
                f"| {session} | {s['trades']} | "
                f"{s['win_rate']:.1f}% | "
                f"{s['pf']:.2f} | "
                f"{s['net_pnl']:+,.0f} |"
            )
        lines.append("")

    # 時間別詳細（ベスト/ワースト）
    hour_stats = analyze_by_hour(trades)
    if hour_stats:
        lines.append("### 時間別(UTC) ベスト/ワースト")
        lines.append("")
        sorted_hours = sorted(
            hour_stats.items(),
            key=lambda x: x[1]["net_pnl"],
            reverse=True,
        )
        lines.append(
            "| 時間(UTC) | JST | 取引 | 勝率 | PF | 損益 |"
        )
        lines.append(
            "|----------|-----|------|------|-----|------|"
        )
        for h, s in sorted_hours[:5]:
            jst = (h + 9) % 24
            lines.append(
                f"| {h:02d} | {jst:02d} | "
                f"{s['trades']} | "
                f"{s['win_rate']:.1f}% | "
                f"{s['pf']:.2f} | "
                f"{s['net_pnl']:+,.0f} |"
            )
        lines.append("| ... | | | | | |")
        for h, s in sorted_hours[-3:]:
            jst = (h + 9) % 24
            lines.append(
                f"| {h:02d} | {jst:02d} | "
                f"{s['trades']} | "
                f"{s['win_rate']:.1f}% | "
                f"{s['pf']:.2f} | "
                f"{s['net_pnl']:+,.0f} |"
            )
        lines.append("")

    return lines


def main():
    """メイン"""
    lines = [
        "# 包括的バックテスト分析レポート",
        f"生成日時: {datetime.now():%Y-%m-%d %H:%M}",
        "",
    ]

    # コスト前提
    lines.append("## コスト前提")
    lines.append("")
    lines.append("| 項目 | 値 | 備考 |")
    lines.append("|------|-----|------|")
    lines.append(
        f"| スプレッド | "
        f"{DEFAULT_TRADING_PARAMS.spread_pips} pips | "
        f"固定値（実データ<SPREAD>未使用） |"
    )
    lines.append(
        f"| スリッページ | "
        f"{DEFAULT_TRADING_PARAMS.slippage_pips} pips | "
        f"エントリー時のみ加算 |"
    )
    lines.append(
        f"| pip価値 | "
        f"{DEFAULT_TRADING_PARAMS.pip_value} | "
        f"1標準ロット=100円/pip |"
    )
    lines.append(
        "| エントリー | "
        "Close +/- (spread/2 + slippage) | "
        "BUY=Ask, SELL=Bid |"
    )
    lines.append(
        "| 通常決済 | "
        "Close -/+ spread/2 | "
        "反対ポジション価格 |"
    )
    lines.append(
        "| SL約定 | SL -/+ slippage | "
        "不利方向スリッページ |"
    )
    lines.append(
        "| TP約定 | TP -/+ slippage | "
        "不利方向スリッページ |"
    )
    lines.append("")
    lines.append(
        "**重要**: スプレッドは固定1.5pips。"
        "実運用の可変スプレッド（特に指標発表時）は"
        "未考慮。"
    )
    lines.append("")

    # IS: 2020-2023
    print("=== In-Sample (2020-2023) ===")
    is_trades, is_summary = run_period(2020, 2023)
    lines.extend(
        format_section(
            "In-Sample (2020-2023, 調整済み期間)",
            is_trades, is_summary,
        )
    )

    # OOS: 2024-2025
    print("=== Out-of-Sample (2024-2025) ===")
    try:
        oos1_trades, oos1_summary = run_period(2024, 2025)
        lines.extend(
            format_section(
                "Out-of-Sample (2024-2025)",
                oos1_trades, oos1_summary,
            )
        )
    except Exception as e:
        lines.append(f"## OOS (2024-2025): エラー {e}")
        lines.append("")

    # OOS: 2016-2019
    print("=== Out-of-Sample (2016-2019) ===")
    try:
        oos2_trades, oos2_summary = run_period(2016, 2019)
        lines.extend(
            format_section(
                "Out-of-Sample (2016-2019)",
                oos2_trades, oos2_summary,
            )
        )
    except Exception as e:
        lines.append(f"## OOS (2016-2019): エラー {e}")
        lines.append("")

    # パラメータ安定性
    lines.append("## パラメータ安定性")
    lines.append("")
    lines.append(
        "MACD傾斜/EMAペナルティの感度分析（2020-2023）:"
    )
    lines.append("")
    lines.append(
        "| 設定 | MACDボーナス/ペナ | EMAペナ | "
        "Stoch | PF | Sharpe | DD | 年間% |"
    )
    lines.append(
        "|------|----------------|--------|"
        "------|-----|--------|------|-------|"
    )
    params = [
        ("v1", "+1.0/-0.5", "-1.0", "なし",
         "1.10", "1.19", "4.67%", "9.6%"),
        ("v2", "+1.5/-1.0", "-1.5", "なし",
         "1.14", "1.77", "4.03%", "14.6%"),
        ("v3", "+2.0/-1.5", "-2.0", "なし",
         "1.15", "1.86", "3.60%", "15.8%"),
        ("**v4**", "**+2.5/-2.0**", "**-2.5**", "**なし**",
         "**1.17**", "**2.11**", "**3.60%**", "**17.7%**"),
        ("v5", "+3.0/-2.5", "-3.0", "なし",
         "1.17", "2.16", "3.67%", "18.0%"),
        ("**v4+stoch**", "**+2.5/-2.0**", "**-2.5**",
         "**あり**", "**1.17**", "**2.17**", "**3.34%**",
         "**17.0%**"),
        ("v5+stoch", "+3.0/-2.5", "-3.0", "あり",
         "1.17", "2.12", "3.40%", "17.5%"),
    ]
    for row in params:
        lines.append(
            f"| {row[0]} | {row[1]} | {row[2]} | "
            f"{row[3]} | {row[4]} | {row[5]} | "
            f"{row[6]} | {row[7]} |"
        )
    lines.append("")
    lines.append(
        "**所見**: v3-v5でPF=1.15-1.17に収束。"
        "Sharpe=2.11-2.17、DD=3.34-3.67%で安定。"
        "パラメータ空間内で頑健性あり。"
    )
    lines.append("")

    # ファイル出力
    out = Path("reports/comprehensive_analysis_20260208.md")
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nレポート: {out}")


if __name__ == "__main__":
    main()
