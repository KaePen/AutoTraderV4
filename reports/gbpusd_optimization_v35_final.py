"""GBPUSD パラメータ最適化 v3.5 最終版

v3-v3.5c のプロービング結果に基づく最終最適化。
ATR拡大フィルター(閾値1.1) + trend_align + DI方向を軸に
勝率70%以上・PF最大化の最良設定を特定する。

調整期間: 2018-2020年 / 検証期間: 2021-2023年
並列実行: 最大9コア

操作方法:
    cd /d/Projects/AutoTraderV4/tmp/feat_gbpusd-opt-v3
    python reports/gbpusd_optimization_v35_final.py --workers 9
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import sys
from datetime import datetime
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace",
    )
    sys.stderr = io.TextIOWrapper(
        sys.stderr.buffer, encoding="utf-8", errors="replace",
    )
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

_SCRIPT_DIR = Path(__file__).resolve().parents[1]
_SRC_DIR = _SCRIPT_DIR / "src"
if _SRC_DIR.exists():
    sys.path.insert(0, str(_SRC_DIR))

if (_SCRIPT_DIR / "data").exists():
    _DATA_ROOT = _SCRIPT_DIR
elif (_SCRIPT_DIR.parents[1] / "data").exists():
    _DATA_ROOT = _SCRIPT_DIR.parents[1]
else:
    _DATA_ROOT = _SCRIPT_DIR

_REPORTS_DIR = _SCRIPT_DIR / "reports"

SYMBOL = "GBPUSD"
TRAIN_YEARS = (2018, 2020)
VALID_YEARS = (2021, 2023)
DATA_DIR = str(_DATA_ROOT / "data")
MAX_WORKERS = 9


def build_final_grid():
    """v3.5最終グリッド

    プロービング結果に基づく27設定:
    - A群: ATR1.1 + TA基本（SL3-4/TP1.5、WR重視）
    - B群: ATR1.1 + DI追加（シグナル品質向上）
    - C群: ATR1.1 + 複合フィルター最良候補
    - D群: ATR1.05 一貫性重視

    Returns:
        list[OptimizeConfig]: 27パラメータセット
    """
    from autotrader.backtest.optimizer import OptimizeConfig

    TA = True

    return [
        # ==== A: ATR1.1 + TA基本 (9設定) ====
        # A1: ベスト（V:74.1%/PF1.27）
        OptimizeConfig(
            min_signals=3, adx_threshold=22,
            rsi_oversold=33, rsi_overbought=67,
            sl_atr_mult=3.0, tp_atr_mult=1.5,
            cooldown_bars=4, mtf_bonus=2,
            require_trend_align=TA,
            atr_expansion_filter=True,
            atr_expansion_threshold=1.1,
            volume=1.0,
        ),
        # A2: SL3.5（V:76.7%/PF1.25）
        OptimizeConfig(
            min_signals=3, adx_threshold=22,
            rsi_oversold=33, rsi_overbought=67,
            sl_atr_mult=3.5, tp_atr_mult=1.5,
            cooldown_bars=4, mtf_bonus=2,
            require_trend_align=TA,
            atr_expansion_filter=True,
            atr_expansion_threshold=1.1,
            volume=1.0,
        ),
        # A3: SL4（V:78.2%/PF1.18）
        OptimizeConfig(
            min_signals=3, adx_threshold=22,
            rsi_oversold=33, rsi_overbought=67,
            sl_atr_mult=4.0, tp_atr_mult=1.5,
            cooldown_bars=4, mtf_bonus=2,
            require_trend_align=TA,
            atr_expansion_filter=True,
            atr_expansion_threshold=1.1,
            volume=1.0,
        ),
        # A4: SL4.5（更に広SL）
        OptimizeConfig(
            min_signals=3, adx_threshold=22,
            rsi_oversold=33, rsi_overbought=67,
            sl_atr_mult=4.5, tp_atr_mult=1.5,
            cooldown_bars=4, mtf_bonus=2,
            require_trend_align=TA,
            atr_expansion_filter=True,
            atr_expansion_threshold=1.1,
            volume=1.0,
        ),
        # A5: SL3/TP2
        OptimizeConfig(
            min_signals=3, adx_threshold=22,
            rsi_oversold=33, rsi_overbought=67,
            sl_atr_mult=3.0, tp_atr_mult=2.0,
            cooldown_bars=4, mtf_bonus=2,
            require_trend_align=TA,
            atr_expansion_filter=True,
            atr_expansion_threshold=1.1,
            volume=1.0,
        ),
        # A6: SL3.5/TP2
        OptimizeConfig(
            min_signals=3, adx_threshold=22,
            rsi_oversold=33, rsi_overbought=67,
            sl_atr_mult=3.5, tp_atr_mult=2.0,
            cooldown_bars=4, mtf_bonus=2,
            require_trend_align=TA,
            atr_expansion_filter=True,
            atr_expansion_threshold=1.1,
            volume=1.0,
        ),
        # A7: ADX25
        OptimizeConfig(
            min_signals=3, adx_threshold=25,
            rsi_oversold=33, rsi_overbought=67,
            sl_atr_mult=3.5, tp_atr_mult=1.5,
            cooldown_bars=4, mtf_bonus=2,
            require_trend_align=TA,
            atr_expansion_filter=True,
            atr_expansion_threshold=1.1,
            volume=1.0,
        ),
        # A8: min4
        OptimizeConfig(
            min_signals=4, adx_threshold=22,
            rsi_oversold=30, rsi_overbought=70,
            sl_atr_mult=3.0, tp_atr_mult=1.5,
            cooldown_bars=4, mtf_bonus=3,
            require_trend_align=TA,
            atr_expansion_filter=True,
            atr_expansion_threshold=1.1,
            volume=1.0,
        ),
        # A9: min4+SL3.5
        OptimizeConfig(
            min_signals=4, adx_threshold=22,
            rsi_oversold=30, rsi_overbought=70,
            sl_atr_mult=3.5, tp_atr_mult=1.5,
            cooldown_bars=4, mtf_bonus=3,
            require_trend_align=TA,
            atr_expansion_filter=True,
            atr_expansion_threshold=1.1,
            volume=1.0,
        ),

        # ==== B: ATR1.1 + DI方向 (9設定) ====
        # B1: DI+SL3（V:74.1%/PF1.25）
        OptimizeConfig(
            min_signals=3, adx_threshold=22,
            rsi_oversold=33, rsi_overbought=67,
            sl_atr_mult=3.0, tp_atr_mult=1.5,
            cooldown_bars=4, mtf_bonus=2,
            require_trend_align=TA,
            di_direction_filter=True,
            atr_expansion_filter=True,
            atr_expansion_threshold=1.1,
            volume=1.0,
        ),
        # B2: DI+SL3.5（V:77.1%/PF1.28）
        OptimizeConfig(
            min_signals=3, adx_threshold=22,
            rsi_oversold=33, rsi_overbought=67,
            sl_atr_mult=3.5, tp_atr_mult=1.5,
            cooldown_bars=4, mtf_bonus=2,
            require_trend_align=TA,
            di_direction_filter=True,
            atr_expansion_filter=True,
            atr_expansion_threshold=1.1,
            volume=1.0,
        ),
        # B3: DI+SL4
        OptimizeConfig(
            min_signals=3, adx_threshold=22,
            rsi_oversold=33, rsi_overbought=67,
            sl_atr_mult=4.0, tp_atr_mult=1.5,
            cooldown_bars=4, mtf_bonus=2,
            require_trend_align=TA,
            di_direction_filter=True,
            atr_expansion_filter=True,
            atr_expansion_threshold=1.1,
            volume=1.0,
        ),
        # B4: DI+SL4.5
        OptimizeConfig(
            min_signals=3, adx_threshold=22,
            rsi_oversold=33, rsi_overbought=67,
            sl_atr_mult=4.5, tp_atr_mult=1.5,
            cooldown_bars=4, mtf_bonus=2,
            require_trend_align=TA,
            di_direction_filter=True,
            atr_expansion_filter=True,
            atr_expansion_threshold=1.1,
            volume=1.0,
        ),
        # B5: DI+SL3.5/TP2
        OptimizeConfig(
            min_signals=3, adx_threshold=22,
            rsi_oversold=33, rsi_overbought=67,
            sl_atr_mult=3.5, tp_atr_mult=2.0,
            cooldown_bars=4, mtf_bonus=2,
            require_trend_align=TA,
            di_direction_filter=True,
            atr_expansion_filter=True,
            atr_expansion_threshold=1.1,
            volume=1.0,
        ),
        # B6: DI+ADX25+SL3.5
        OptimizeConfig(
            min_signals=3, adx_threshold=25,
            rsi_oversold=33, rsi_overbought=67,
            sl_atr_mult=3.5, tp_atr_mult=1.5,
            cooldown_bars=4, mtf_bonus=2,
            require_trend_align=TA,
            di_direction_filter=True,
            atr_expansion_filter=True,
            atr_expansion_threshold=1.1,
            volume=1.0,
        ),
        # B7: DI+min4+SL3
        OptimizeConfig(
            min_signals=4, adx_threshold=22,
            rsi_oversold=30, rsi_overbought=70,
            sl_atr_mult=3.0, tp_atr_mult=1.5,
            cooldown_bars=4, mtf_bonus=3,
            require_trend_align=TA,
            di_direction_filter=True,
            atr_expansion_filter=True,
            atr_expansion_threshold=1.1,
            volume=1.0,
        ),
        # B8: DI+min4+SL3.5（V:77.3%/PF1.30 TOP候補）
        OptimizeConfig(
            min_signals=4, adx_threshold=22,
            rsi_oversold=30, rsi_overbought=70,
            sl_atr_mult=3.5, tp_atr_mult=1.5,
            cooldown_bars=4, mtf_bonus=3,
            require_trend_align=TA,
            di_direction_filter=True,
            atr_expansion_filter=True,
            atr_expansion_threshold=1.1,
            volume=1.0,
        ),
        # B9: DI+min4+SL4
        OptimizeConfig(
            min_signals=4, adx_threshold=22,
            rsi_oversold=30, rsi_overbought=70,
            sl_atr_mult=4.0, tp_atr_mult=1.5,
            cooldown_bars=4, mtf_bonus=3,
            require_trend_align=TA,
            di_direction_filter=True,
            atr_expansion_filter=True,
            atr_expansion_threshold=1.1,
            volume=1.0,
        ),

        # ==== C: 複合フィルター最良 (5設定) ====
        # C1: DI+STOCH+SL3.5
        OptimizeConfig(
            min_signals=3, adx_threshold=22,
            rsi_oversold=33, rsi_overbought=67,
            sl_atr_mult=3.5, tp_atr_mult=1.5,
            cooldown_bars=4, mtf_bonus=2,
            require_trend_align=TA,
            di_direction_filter=True,
            stoch_confirm=True,
            atr_expansion_filter=True,
            atr_expansion_threshold=1.1,
            volume=1.0,
        ),
        # C2: DI+MACD_H+SL3.5
        OptimizeConfig(
            min_signals=3, adx_threshold=22,
            rsi_oversold=33, rsi_overbought=67,
            sl_atr_mult=3.5, tp_atr_mult=1.5,
            cooldown_bars=4, mtf_bonus=2,
            require_trend_align=TA,
            di_direction_filter=True,
            macd_hist_filter=True,
            atr_expansion_filter=True,
            atr_expansion_threshold=1.1,
            volume=1.0,
        ),
        # C3: min4+DI+STOCH+SL3.5
        OptimizeConfig(
            min_signals=4, adx_threshold=22,
            rsi_oversold=30, rsi_overbought=70,
            sl_atr_mult=3.5, tp_atr_mult=1.5,
            cooldown_bars=4, mtf_bonus=3,
            require_trend_align=TA,
            di_direction_filter=True,
            stoch_confirm=True,
            atr_expansion_filter=True,
            atr_expansion_threshold=1.1,
            volume=1.0,
        ),
        # C4: min4+DI+MACD_H+SL3.5
        OptimizeConfig(
            min_signals=4, adx_threshold=22,
            rsi_oversold=30, rsi_overbought=70,
            sl_atr_mult=3.5, tp_atr_mult=1.5,
            cooldown_bars=4, mtf_bonus=3,
            require_trend_align=TA,
            di_direction_filter=True,
            macd_hist_filter=True,
            atr_expansion_filter=True,
            atr_expansion_threshold=1.1,
            volume=1.0,
        ),
        # C5: ALL filters
        OptimizeConfig(
            min_signals=4, adx_threshold=22,
            rsi_oversold=30, rsi_overbought=70,
            sl_atr_mult=3.5, tp_atr_mult=1.5,
            cooldown_bars=4, mtf_bonus=3,
            require_trend_align=TA,
            bb_filter=True, stoch_confirm=True,
            macd_hist_filter=True,
            di_direction_filter=True,
            atr_expansion_filter=True,
            atr_expansion_threshold=1.1,
            volume=1.0,
        ),

        # ==== D: ATR1.05 一貫性重視 (4設定) ====
        # D1: 基本
        OptimizeConfig(
            min_signals=3, adx_threshold=22,
            rsi_oversold=33, rsi_overbought=67,
            sl_atr_mult=3.0, tp_atr_mult=1.5,
            cooldown_bars=4, mtf_bonus=2,
            require_trend_align=TA,
            atr_expansion_filter=True,
            atr_expansion_threshold=1.05,
            volume=1.0,
        ),
        # D2: SL3.5
        OptimizeConfig(
            min_signals=3, adx_threshold=22,
            rsi_oversold=33, rsi_overbought=67,
            sl_atr_mult=3.5, tp_atr_mult=1.5,
            cooldown_bars=4, mtf_bonus=2,
            require_trend_align=TA,
            atr_expansion_filter=True,
            atr_expansion_threshold=1.05,
            volume=1.0,
        ),
        # D3: DI+SL3.5
        OptimizeConfig(
            min_signals=3, adx_threshold=22,
            rsi_oversold=33, rsi_overbought=67,
            sl_atr_mult=3.5, tp_atr_mult=1.5,
            cooldown_bars=4, mtf_bonus=2,
            require_trend_align=TA,
            di_direction_filter=True,
            atr_expansion_filter=True,
            atr_expansion_threshold=1.05,
            volume=1.0,
        ),
        # D4: min4+DI+SL3.5
        OptimizeConfig(
            min_signals=4, adx_threshold=22,
            rsi_oversold=30, rsi_overbought=70,
            sl_atr_mult=3.5, tp_atr_mult=1.5,
            cooldown_bars=4, mtf_bonus=3,
            require_trend_align=TA,
            di_direction_filter=True,
            atr_expansion_filter=True,
            atr_expansion_threshold=1.05,
            volume=1.0,
        ),
    ]


def v35_score(
    valid: dict,
    train: dict,
) -> float:
    """v3.5スコア: WR70%+PF重視+一貫性

    Args:
        valid: 検証結果
        train: 訓練結果

    Returns:
        スコア（高=優秀）
    """
    if valid["trades"] < 30:
        return -999.0

    wr = valid["win_rate"]
    pf = valid["pf"]
    profit = valid["net_profit"]

    # 勝率ボーナス: 70%以上で加点
    wr_bonus = max(0, (wr - 60) / 10)

    # PFスコア
    pf_score = max(0, pf - 0.8) * 10

    # 利益スコア
    profit_score = max(0, profit) / 10

    # DD penalty
    dd = max(valid["max_dd"], 0.001)
    dd_pen = 1.0 / (1.0 + dd * 2)

    # 一貫性ボーナス（訓練/検証PF差が小さいほど良い）
    pf_gap = abs(train["pf"] - valid["pf"])
    consistency = 1.0 / (1.0 + pf_gap * 3)

    # 過学習ペナルティ
    pf_deg = max(
        0, (train["pf"] - valid["pf"])
        / max(train["pf"], 0.001)
    )
    overfit_pen = 1.0 - min(pf_deg * 0.5, 0.5)

    return (
        wr_bonus * pf_score * dd_pen
        * overfit_pen * consistency
        + profit_score * 0.1
    )


def save_csv(results: list, out_path: Path) -> None:
    """結果CSV保存"""
    fields = [
        "rank", "min_signals", "adx_threshold",
        "rsi_oversold", "rsi_overbought",
        "sl_atr_mult", "tp_atr_mult",
        "cooldown_bars", "mtf_bonus",
        "require_trend_align", "bb_filter",
        "stoch_confirm", "macd_hist_filter",
        "di_direction_filter", "atr_expansion_filter",
        "atr_expansion_threshold",
        "train_trades", "train_win_rate",
        "train_pf", "train_net_profit", "train_max_dd",
        "valid_trades", "valid_win_rate",
        "valid_pf", "valid_net_profit", "valid_max_dd",
        "score",
    ]
    with open(
        out_path, "w", newline="", encoding="utf-8",
    ) as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for rank, (sc, cfg, tr, vr) in enumerate(
            results, 1,
        ):
            w.writerow({
                "rank": rank,
                "min_signals": cfg.min_signals,
                "adx_threshold": cfg.adx_threshold,
                "rsi_oversold": cfg.rsi_oversold,
                "rsi_overbought": cfg.rsi_overbought,
                "sl_atr_mult": cfg.sl_atr_mult,
                "tp_atr_mult": cfg.tp_atr_mult,
                "cooldown_bars": cfg.cooldown_bars,
                "mtf_bonus": cfg.mtf_bonus,
                "require_trend_align":
                    cfg.require_trend_align,
                "bb_filter": cfg.bb_filter,
                "stoch_confirm": cfg.stoch_confirm,
                "macd_hist_filter": cfg.macd_hist_filter,
                "di_direction_filter":
                    cfg.di_direction_filter,
                "atr_expansion_filter":
                    cfg.atr_expansion_filter,
                "atr_expansion_threshold":
                    cfg.atr_expansion_threshold,
                "train_trades": tr["trades"],
                "train_win_rate":
                    f"{tr['win_rate']:.1f}",
                "train_pf": f"{tr['pf']:.3f}",
                "train_net_profit":
                    f"{tr['net_profit']:.2f}",
                "train_max_dd":
                    f"{tr['max_dd']:.3f}",
                "valid_trades": vr["trades"],
                "valid_win_rate":
                    f"{vr['win_rate']:.1f}",
                "valid_pf": f"{vr['pf']:.3f}",
                "valid_net_profit":
                    f"{vr['net_profit']:.2f}",
                "valid_max_dd":
                    f"{vr['max_dd']:.3f}",
                "score": f"{sc:.6f}",
            })


def main() -> None:
    """エントリポイント"""
    parser = argparse.ArgumentParser(
        description="GBPUSD v3.5 最終最適化",
    )
    parser.add_argument(
        "--workers", type=int, default=MAX_WORKERS,
    )
    args = parser.parse_args()

    from autotrader.backtest.optimizer import (
        _calculate_indicators,
        _load_mt5_csv,
        _run_single_config,
    )
    import concurrent.futures

    grid = build_final_grid()
    print(
        f"GBPUSD v3.5 最終最適化\n"
        f"  設定数: {len(grid)}  コア: {args.workers}\n"
        f"  調整: {TRAIN_YEARS}  検証: {VALID_YEARS}"
    )

    data_path = Path(DATA_DIR) / SYMBOL
    h1_f = list(data_path.glob(f"{SYMBOL}_H1_*.csv"))
    h4_f = list(data_path.glob(f"{SYMBOL}_H4_*.csv"))
    if not h1_f or not h4_f:
        print(f"データなし: {data_path}")
        sys.exit(1)

    print("\nデータ読み込み...")
    df = _load_mt5_csv(h1_f[0])
    df = _calculate_indicators(df)
    h4 = _load_mt5_csv(h4_f[0])
    h4 = _calculate_indicators(h4)
    print(f"  H1: {len(df):,}  H4: {len(h4):,}")

    print(
        f"\n{len(grid)}設定を"
        f"{args.workers}コア並列で評価中..."
    )
    task_args = [
        (i, c, df, h4, TRAIN_YEARS, VALID_YEARS, SYMBOL)
        for i, c in enumerate(grid)
    ]

    raw = []
    with concurrent.futures.ProcessPoolExecutor(
        max_workers=args.workers,
    ) as ex:
        for res in ex.map(_run_single_config, task_args):
            raw.append(res)
            idx, tr, vr = res
            c = grid[idx]
            mark = "*" if vr["win_rate"] >= 70 else " "
            filters = ""
            if c.di_direction_filter:
                filters += "DI "
            if c.stoch_confirm:
                filters += "ST "
            if c.macd_hist_filter:
                filters += "MH "
            if c.bb_filter:
                filters += "BB "
            print(
                f"  [{idx+1:2d}/{len(grid)}]{mark}"
                f" min{c.min_signals}"
                f" ADX{c.adx_threshold:.0f}"
                f" SL{c.sl_atr_mult}/TP{c.tp_atr_mult}"
                f" A{c.atr_expansion_threshold}"
                f" {filters}"
                f"-> T:{tr['win_rate']:.1f}%"
                f" PF{tr['pf']:.2f}"
                f"  V:{vr['win_rate']:.1f}%"
                f" PF{vr['pf']:.2f}"
                f" ${vr['net_profit']:+.0f}"
            )

    raw.sort(key=lambda x: x[0])
    scored = []
    for idx, tr, vr in raw:
        c = grid[idx]
        sc = v35_score(vr, tr)
        scored.append((sc, c, tr, vr))
    scored.sort(key=lambda x: x[0], reverse=True)

    # 結果表示
    print("\n" + "=" * 110)
    print("【v3.5 最終 上位15設定】")
    print("=" * 110)
    for rank, (sc, c, tr, vr) in enumerate(
        scored[:15], 1,
    ):
        filters = ""
        if c.di_direction_filter:
            filters += "DI "
        if c.stoch_confirm:
            filters += "ST "
        if c.macd_hist_filter:
            filters += "MH "
        if c.bb_filter:
            filters += "BB "
        gap = abs(tr["pf"] - vr["pf"])
        ok = "OK" if gap < 0.15 else "!!"
        label = (
            f"min{c.min_signals}"
            f" ADX{c.adx_threshold:.0f}"
            f" RSI{c.rsi_oversold:.0f}/"
            f"{c.rsi_overbought:.0f}"
            f" SL{c.sl_atr_mult}/TP{c.tp_atr_mult}"
            f" A{c.atr_expansion_threshold}"
            f" {filters}"
        )
        print(
            f"#{rank:2d} {label:<55}"
            f" T:{tr['trades']:>4}"
            f" {tr['win_rate']:>5.1f}%"
            f" PF{tr['pf']:.2f}"
            f"  V:{vr['trades']:>4}"
            f" {vr['win_rate']:>5.1f}%"
            f" PF{vr['pf']:.2f}"
            f" ${vr['net_profit']:>+6.0f}"
            f" {ok}"
            f"  sc={sc:.2f}"
        )

    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = (
        _REPORTS_DIR / f"gbpusd_opt_v35_{ts}.csv"
    )
    save_csv(scored, csv_path)
    print(f"\nCSV: {csv_path}")


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
