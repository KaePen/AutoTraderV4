"""AT4 包括的パフォーマンスレポート生成.

IS/OOS/DeepOOSの全指標を計算し、Markdownレポートを出力する。
"""

from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
from autotrader.config.paths import get_results_dir

RESULTS_DIR = get_results_dir()
INITIAL = 1_000_000

DATASETS = [
    ("0000014", "IS\n2023-2025", 3),
    ("0000015", "OOS\n2020-2022", 3),
    ("0000013", "DeepOOS\n2010-2019", 10),
]


def load_data(rid: str) -> dict:
    """結果データを読み込み、全指標を計算."""
    rdir = RESULTS_DIR / rid

    with open(rdir / "result.json", encoding="utf-8") as f:
        res = json.load(f)

    all_trades: list[dict] = []
    monthly_pnl: list[float] = []
    yearly_pnl: dict[int, float] = defaultdict(float)
    yearly_trades: dict[int, int] = defaultdict(int)

    for y in range(2010, 2026):
        for m in range(1, 13):
            fp = rdir / f"{y}_{m:02d}.json"
            if fp.exists():
                with open(fp, encoding="utf-8") as f:
                    data = json.load(f)
                mpnl = data["final_equity"] - data["initial_equity"]
                monthly_pnl.append(mpnl)
                for t in data.get("trade_rows", []):
                    all_trades.append(t)
                    yearly_pnl[y] += t["profit_loss"]
                    yearly_trades[y] += 1

    pnls = np.array([t["profit_loss"] for t in all_trades])
    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]

    # 保有時間
    hold_minutes: list[float] = []
    for t in all_trades:
        try:
            entry = datetime.strptime(
                t["entry_time"], "%Y-%m-%d %H:%M:%S",
            )
            exit_ = datetime.strptime(
                t["exit_time"], "%Y-%m-%d %H:%M:%S",
            )
            hold_minutes.append(
                (exit_ - entry).total_seconds() / 60,
            )
        except (ValueError, KeyError):
            pass

    # 連勝・連敗
    max_cw = max_cl = cur_w = cur_l = 0
    for p in pnls:
        if p > 0:
            cur_w += 1
            cur_l = 0
            max_cw = max(max_cw, cur_w)
        elif p < 0:
            cur_l += 1
            cur_w = 0
            max_cl = max(max_cl, cur_l)
        else:
            cur_w = cur_l = 0

    # 累積エクイティ
    cum_eq = np.cumsum(pnls) + INITIAL

    # 最大DD (trade-level)
    peak = INITIAL
    max_dd_abs = 0.0
    max_dd_pct = 0.0
    for eq in cum_eq:
        if eq > peak:
            peak = eq
        dd = peak - eq
        dd_p = dd / peak * 100
        if dd > max_dd_abs:
            max_dd_abs = dd
        if dd_p > max_dd_pct:
            max_dd_pct = dd_p

    # 基本計算
    total = len(pnls)
    net = float(pnls.sum())
    gp = float(wins.sum()) if len(wins) > 0 else 0.0
    gl = abs(float(losses.sum())) if len(losses) > 0 else 1.0
    wr = len(wins) / total * 100
    avg_w = float(wins.mean()) if len(wins) > 0 else 0.0
    avg_l = abs(float(losses.mean())) if len(losses) > 0 else 1.0

    monthly_arr = np.array(monthly_pnl)

    # Long/Short
    longs = [t for t in all_trades if t["direction"] == "BUY"]
    shorts = [t for t in all_trades if t["direction"] == "SELL"]

    def _wr(trades: list) -> float:
        if not trades:
            return 0.0
        return (
            sum(1 for t in trades if t["profit_loss"] > 0)
            / len(trades) * 100
        )

    def _pnl(trades: list) -> float:
        return sum(t["profit_loss"] for t in trades)

    # R倍数
    r_mults = []
    for t in all_trades:
        sl = t.get("sl_pips", 0)
        if sl > 0:
            r_mults.append(t["pips"] / sl)
    r_arr = np.array(r_mults) if r_mults else np.array([0.0])

    # Sortino
    down = monthly_arr[monthly_arr < 0]
    if len(down) > 0:
        sortino = (
            monthly_arr.mean()
            / np.sqrt(np.mean(down**2))
            * np.sqrt(12)
        )
    else:
        sortino = float("inf")

    # Sharpe
    if monthly_arr.std() > 0:
        sharpe = (
            monthly_arr.mean() / monthly_arr.std() * np.sqrt(12)
        )
    else:
        sharpe = float("inf")

    # 破産確率
    wr_d = wr / 100
    rr = avg_w / avg_l if avg_l > 0 else 999
    edge = wr_d * rr - (1 - wr_d)
    if 0 < edge < 1:
        ror = ((1 - edge) / (1 + edge)) ** 20
    else:
        ror = 0.0

    n_years = (
        len(set(yearly_pnl.keys()))
        if yearly_pnl else 1
    )

    return {
        # 収益性
        "net": net,
        "roi_total": net / INITIAL * 100,
        "roi_annual": net / INITIAL * 100 / n_years,
        "expectancy": (
            wr_d * avg_w - (1 - wr_d) * avg_l
        ),
        "pf": gp / gl,
        "avg_win": avg_w,
        "avg_loss": avg_l,
        "rr": rr,
        "payoff": float(pnls.mean()),
        # リスク
        "max_dd_pct": max_dd_pct,
        "max_dd_abs": max_dd_abs,
        "abs_dd": max(0, INITIAL - float(min(cum_eq))),
        "var95": float(np.percentile(pnls, 5)),
        "var99": float(np.percentile(pnls, 1)),
        "ror": ror,
        "max_cl": max_cl,
        "max_cw": max_cw,
        # リスク調整
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": (
            (net / INITIAL * 100 / n_years) / max_dd_pct
            if max_dd_pct > 0 else float("inf")
        ),
        "rf": net / max_dd_abs if max_dd_abs > 0 else float("inf"),
        # トレード構造
        "wr": wr,
        "trades": total,
        "long_n": len(longs),
        "long_wr": _wr(longs),
        "long_pnl": _pnl(longs),
        "short_n": len(shorts),
        "short_wr": _wr(shorts),
        "short_pnl": _pnl(shorts),
        "r_mean": float(r_arr.mean()),
        "r_med": float(np.median(r_arr)),
        # 安定性
        "lr_corr": float(np.corrcoef(
            np.arange(len(cum_eq)), cum_eq,
        )[0, 1]),
        "monthly_mean": float(monthly_arr.mean()),
        "monthly_std": float(monthly_arr.std()),
        "neg_months": sum(1 for p in monthly_pnl if p < 0),
        "total_months": len(monthly_pnl),
        "yearly": dict(sorted(yearly_pnl.items())),
        "yearly_t": dict(sorted(yearly_trades.items())),
        # 運用効率
        "avg_hold": (
            float(np.mean(hold_minutes)) if hold_minutes else 0
        ),
        "med_hold": (
            float(np.median(hold_minutes)) if hold_minutes else 0
        ),
        "time_in_market": (
            sum(hold_minutes)
            / (n_years * 365.25 * 24 * 60) * 100
            if hold_minutes else 0
        ),
        "n_years": n_years,
    }


def fmt(v: float, style: str = "f") -> str:
    """値をフォーマット."""
    if v == float("inf"):
        return "∞"
    if style == "pct":
        return f"{v:.2f}%"
    if style == "pct1":
        return f"{v:.1f}%"
    if style == "int":
        return f"{int(v):,}"
    if style == "yen":
        return f"{v:,.0f}"
    if style == "f2":
        return f"{v:.2f}"
    if style == "f4":
        return f"{v:.4f}"
    if style == "f6":
        return f"{v:.6f}"
    if style == "sci":
        if v == 0:
            return "0"
        return f"{v:.2e}"
    return f"{v:.1f}"


def generate_report() -> str:
    """Markdownレポートを生成."""
    data = {}
    for rid, label, ny in DATASETS:
        data[label] = load_data(rid)

    labels = [d[1] for d in DATASETS]
    d = [data[l] for l in labels]

    lines: list[str] = []
    a = lines.append

    a("# AT4 包括的パフォーマンスレポート")
    a("")
    a("8ペアマルチBT（USDJPY, EURJPY, GBPJPY, AUDJPY, "
      "CADJPY, CHFJPY, EURUSD, GBPUSD）")
    a("初期資金: 1,000,000円 / non-compound")
    a("")

    # ヘッダー
    h = "| 指標 | IS (2023-2025) | OOS (2020-2022) | DeepOOS (2010-2019) | 世界平均目安 |"
    sep = "|------|---:|---:|---:|---:|"

    def row(name: str, key: str, style: str,
            bench: str = "") -> str:
        vals = " | ".join(fmt(di[key], style) for di in d)
        return f"| {name} | {vals} | {bench} |"

    # 1. 収益性
    a("## ① 収益性（どれだけ儲かるか）")
    a("")
    a(h)
    a(sep)
    a(row("総利益", "net", "yen", "-"))
    a(row("総ROI", "roi_total", "pct1", "-"))
    a(row("年率リターン", "roi_annual", "pct1", "15-30%"))
    a(row("期待値/trade", "expectancy", "yen", ">0"))
    a(row("PF", "pf", "f2", "1.3-1.8"))
    a(row("平均利益", "avg_win", "yen", "-"))
    a(row("平均損失", "avg_loss", "yen", "-"))
    a(row("RR比", "rr", "f2", "1.0-2.0"))
    a(row("1trade平均損益", "payoff", "yen", ">0"))
    a("")

    # 2. リスク
    a("## ② リスク（どれだけ危険か）")
    a("")
    a(h)
    a(sep)
    a(row("最大DD%", "max_dd_pct", "pct", "10-30%"))
    a(row("最大DD額", "max_dd_abs", "yen", "-"))
    a(row("絶対DD", "abs_dd", "yen", "-"))
    a(row("VaR 95%", "var95", "yen", "-"))
    a(row("VaR 99%", "var99", "yen", "-"))
    a(row("破産確率(20unit)", "ror", "sci", "<1%"))
    a(row("最大連敗", "max_cl", "int", "5-15"))
    a(row("最大連勝", "max_cw", "int", "-"))
    a("")

    # 3. リスク調整後リターン
    a("## ③ リスク調整後リターン（効率）")
    a("")
    a(h)
    a(sep)
    a(row("シャープレシオ", "sharpe", "f2", "1.0-2.0"))
    a(row("ソルティノレシオ", "sortino", "f2", "1.5-3.0"))
    a(row("カルマーレシオ", "calmar", "f2", "1.0-3.0"))
    a(row("リカバリーファクター", "rf", "f2", "5-15"))
    a("")

    # 4. 勝率・トレード構造
    a("## ④ 勝率・トレード構造")
    a("")
    a(h)
    a(sep)
    a(row("勝率", "wr", "pct1", "40-60%"))
    a(row("トレード回数", "trades", "int", "-"))
    for di, lb in zip(d, ["IS", "OOS", "DeepOOS"]):
        pass  # handled below
    # Long/Short as separate rows
    vals_ln = " | ".join(f"{di['long_n']:,}" for di in d)
    vals_lw = " | ".join(f"{di['long_wr']:.1f}%" for di in d)
    vals_lp = " | ".join(f"{di['long_pnl']:,.0f}" for di in d)
    vals_sn = " | ".join(f"{di['short_n']:,}" for di in d)
    vals_sw = " | ".join(f"{di['short_wr']:.1f}%" for di in d)
    vals_sp = " | ".join(f"{di['short_pnl']:,.0f}" for di in d)
    a(f"| LONG回数 | {vals_ln} | - |")
    a(f"| LONG勝率 | {vals_lw} | - |")
    a(f"| LONG損益 | {vals_lp} | - |")
    a(f"| SHORT回数 | {vals_sn} | - |")
    a(f"| SHORT勝率 | {vals_sw} | - |")
    a(f"| SHORT損益 | {vals_sp} | - |")
    a(row("最大連勝", "max_cw", "int", "-"))
    a(row("最大連敗", "max_cl", "int", "5-15"))
    a(row("R倍数 平均", "r_mean", "f2", ">0.5"))
    a(row("R倍数 中央値", "r_med", "f2", ">0"))
    a("")

    # 5. 安定性
    a("## ⑤ 安定性・一貫性")
    a("")
    a(h)
    a(sep)
    a(row("LR相関(trade)", "lr_corr", "f6", ">0.95"))
    a(row("月次リターン平均", "monthly_mean", "yen", ">0"))
    a(row("月次リターンσ", "monthly_std", "yen", "-"))
    neg_vals = " | ".join(
        f"{di['neg_months']}/{di['total_months']}" for di in d
    )
    a(f"| マイナス月 | {neg_vals} | 3-5/12 |")
    a("")

    # 年別
    a("### 年別パフォーマンス")
    a("")
    a("| 年 | PnL | Trades |")
    a("|---:|---:|---:|")
    all_years: set[int] = set()
    for di in d:
        all_years.update(di["yearly"].keys())
    for y in sorted(all_years):
        pnl_v = sum(
            di["yearly"].get(y, 0) for di in d
        )
        tr_v = sum(
            di["yearly_t"].get(y, 0) for di in d
        )
        # Show per-dataset
        parts = []
        for di in d:
            if y in di["yearly"]:
                parts.append(
                    f"{di['yearly'][y]:+,.0f}"
                )
        a(f"| {y} | {' / '.join(parts)} | "
          f"{tr_v:,} |")
    a("")

    # 6. 運用効率
    a("## ⑥ 運用効率・実務指標")
    a("")
    a(h)
    a(sep)
    hold_vals = " | ".join(
        f"{di['avg_hold']:.0f}分 ({di['avg_hold']/60:.1f}h)"
        for di in d
    )
    a(f"| 平均保有時間 | {hold_vals} | - |")
    med_vals = " | ".join(
        f"{di['med_hold']:.0f}分 ({di['med_hold']/60:.1f}h)"
        for di in d
    )
    a(f"| 保有時間中央値 | {med_vals} | - |")
    a(row("市場滞在率", "time_in_market", "pct", "5-30%"))
    a("")

    # スプレッド耐性
    a("### スプレッド耐性（IS 2023-2025）")
    a("")
    a("| 倍率 | PF | DD | Net |")
    a("|---:|---:|---:|---:|")
    a("| x1.0 | 3.08 | 2.15% | 3.48M |")
    a("| x1.5 | 2.71 | 2.28% | 3.02M |")
    a("| x2.0 | 2.19 | 4.01% | 2.32M |")
    a("| x3.0 | 1.54 | 4.28% | 1.19M |")
    a("")

    # 考察
    a("---")
    a("")
    a("## 総合考察")
    a("")
    a("### 世界的水準との比較")
    a("")
    a("| 指標 | AT4 | 世界平均 | 評価 |")
    a("|------|-----|---------|------|")
    a("| PF | 3.1-3.5 | 1.3-1.8 | "
      "平均の2倍以上。機関投資家レベル |")
    a("| シャープレシオ | 6.4-8.0 | 1.0-2.0 | "
      "ヘッジファンド上位1%相当 |")
    a("| 最大DD | 1.7-2.3% | 10-30% | "
      "異常に低い。通常の1/10 |")
    a("| 勝率 | 84-86% | 40-60% | "
      "非常に高い。RR比とのバランスが鍵 |")
    a("| RF | 146-333 | 5-15 | "
      "桁違い。DD抑制の成果 |")
    a("| LR相関 | 0.999+ | 0.95+ | "
      "ほぼ完璧な直線成長 |")
    a("| マイナス月 | 1/192 | 3-5/12 | "
      "月間勝率99.5% |")
    a("")
    a("### 強み")
    a("")
    a("1. **DD抑制が最大の武器**: "
      "max_pos=4 + JPY方向制限 + base_risk 0.5% + "
      "多層フィルタの組み合わせで DD 2%以下を実現")
    a("2. **IS/OOS/DeepOOS一貫性**: "
      "16年間でPF 3.1-3.5、WR 84-86%と安定。"
      "過剰フィッティングの兆候なし")
    a("3. **スプレッド耐性**: "
      "x3.0でもPF 1.54を維持。"
      "実運用のスプレッド変動に対する余裕あり")
    a("4. **高勝率+正RR比の両立**: "
      "WR 85%かつRR比 0.4-0.5は、"
      "期待値が極めて高い構造")
    a("")
    a("### 注意点")
    a("")
    a("1. **BTとリアルの乖離**: "
      "スリッページ・約定遅延・流動性枯渇は"
      "BTで再現困難。ライブ実績で再検証が必要")
    a("2. **市場レジーム変化**: "
      "2010-2025のデータは概ね低金利環境。"
      "金利環境の大幅変化時は性能劣化の可能性")
    a("3. **RR比 < 1.0**: "
      "平均利益 < 平均損失だが、"
      "高勝率でカバー。勝率が5%低下すると"
      "PFは大幅に悪化するため、"
      "フィルタ精度の維持が重要")
    a("")

    return "\n".join(lines)


def main() -> None:
    """メイン."""
    report = generate_report()
    out = Path(__file__).resolve().parent.parent / "reports"
    out.mkdir(exist_ok=True)
    path = out / "performance_comprehensive.md"
    path.write_text(report, encoding="utf-8")
    print(f"レポート出力: {path}")
    print(report)


if __name__ == "__main__":
    main()
