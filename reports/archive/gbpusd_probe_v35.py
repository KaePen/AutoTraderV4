"""GBPUSD v3.5 プロービング: DI方向+ATR拡大フィルターの効果確認

新フィルターの効果を確認し、PF1.50+を目指す。

操作方法:
    cd /d/Projects/AutoTraderV4/tmp/feat_gbpusd-opt-v3
    python reports/gbpusd_probe_v35.py --workers 9
"""

from __future__ import annotations

import argparse
import io
import os
import sys
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

SYMBOL = "GBPUSD"
TRAIN_YEARS = (2018, 2020)
VALID_YEARS = (2021, 2023)
DATA_DIR = str(_DATA_ROOT / "data")


def build_probe_grid():
    """v3.5プローブ設定

    Returns:
        list[tuple[str, OptimizeConfig]]: (ラベル, 設定)
    """
    from autotrader.backtest.optimizer import OptimizeConfig

    TA = True

    return [
        # --- 1: ベースライン（v3 best） ---
        ("BASE TA SL3/TP1.5", OptimizeConfig(
            min_signals=3, adx_threshold=22,
            rsi_oversold=33, rsi_overbought=67,
            sl_atr_mult=3.0, tp_atr_mult=1.5,
            cooldown_bars=4, mtf_bonus=2,
            require_trend_align=TA, volume=1.0,
        )),
        # --- 2: DI方向フィルター単体 ---
        ("DI SL3/TP1.5", OptimizeConfig(
            min_signals=3, adx_threshold=22,
            rsi_oversold=33, rsi_overbought=67,
            sl_atr_mult=3.0, tp_atr_mult=1.5,
            cooldown_bars=4, mtf_bonus=2,
            require_trend_align=TA,
            di_direction_filter=True, volume=1.0,
        )),
        # --- 3: ATR拡大フィルター単体 ---
        ("ATR SL3/TP1.5", OptimizeConfig(
            min_signals=3, adx_threshold=22,
            rsi_oversold=33, rsi_overbought=67,
            sl_atr_mult=3.0, tp_atr_mult=1.5,
            cooldown_bars=4, mtf_bonus=2,
            require_trend_align=TA,
            atr_expansion_filter=True, volume=1.0,
        )),
        # --- 4: DI+ATR両方 ---
        ("DI+ATR SL3/TP1.5", OptimizeConfig(
            min_signals=3, adx_threshold=22,
            rsi_oversold=33, rsi_overbought=67,
            sl_atr_mult=3.0, tp_atr_mult=1.5,
            cooldown_bars=4, mtf_bonus=2,
            require_trend_align=TA,
            di_direction_filter=True,
            atr_expansion_filter=True, volume=1.0,
        )),
        # --- 5-8: DI+各種SL/TP比率 ---
        ("DI SL2/TP2", OptimizeConfig(
            min_signals=3, adx_threshold=22,
            rsi_oversold=33, rsi_overbought=67,
            sl_atr_mult=2.0, tp_atr_mult=2.0,
            cooldown_bars=4, mtf_bonus=2,
            require_trend_align=TA,
            di_direction_filter=True, volume=1.0,
        )),
        ("DI SL2.5/TP2", OptimizeConfig(
            min_signals=3, adx_threshold=22,
            rsi_oversold=33, rsi_overbought=67,
            sl_atr_mult=2.5, tp_atr_mult=2.0,
            cooldown_bars=4, mtf_bonus=2,
            require_trend_align=TA,
            di_direction_filter=True, volume=1.0,
        )),
        ("DI SL3/TP2", OptimizeConfig(
            min_signals=3, adx_threshold=22,
            rsi_oversold=33, rsi_overbought=67,
            sl_atr_mult=3.0, tp_atr_mult=2.0,
            cooldown_bars=4, mtf_bonus=2,
            require_trend_align=TA,
            di_direction_filter=True, volume=1.0,
        )),
        ("DI SL2/TP3", OptimizeConfig(
            min_signals=3, adx_threshold=22,
            rsi_oversold=33, rsi_overbought=67,
            sl_atr_mult=2.0, tp_atr_mult=3.0,
            cooldown_bars=4, mtf_bonus=2,
            require_trend_align=TA,
            di_direction_filter=True, volume=1.0,
        )),
        # --- 9-12: DI+ATR+各種SL/TP ---
        ("DI+ATR SL2/TP2", OptimizeConfig(
            min_signals=3, adx_threshold=22,
            rsi_oversold=33, rsi_overbought=67,
            sl_atr_mult=2.0, tp_atr_mult=2.0,
            cooldown_bars=4, mtf_bonus=2,
            require_trend_align=TA,
            di_direction_filter=True,
            atr_expansion_filter=True, volume=1.0,
        )),
        ("DI+ATR SL2.5/TP2", OptimizeConfig(
            min_signals=3, adx_threshold=22,
            rsi_oversold=33, rsi_overbought=67,
            sl_atr_mult=2.5, tp_atr_mult=2.0,
            cooldown_bars=4, mtf_bonus=2,
            require_trend_align=TA,
            di_direction_filter=True,
            atr_expansion_filter=True, volume=1.0,
        )),
        ("DI+ATR SL3/TP2", OptimizeConfig(
            min_signals=3, adx_threshold=22,
            rsi_oversold=33, rsi_overbought=67,
            sl_atr_mult=3.0, tp_atr_mult=2.0,
            cooldown_bars=4, mtf_bonus=2,
            require_trend_align=TA,
            di_direction_filter=True,
            atr_expansion_filter=True, volume=1.0,
        )),
        ("DI+ATR SL2/TP3", OptimizeConfig(
            min_signals=3, adx_threshold=22,
            rsi_oversold=33, rsi_overbought=67,
            sl_atr_mult=2.0, tp_atr_mult=3.0,
            cooldown_bars=4, mtf_bonus=2,
            require_trend_align=TA,
            di_direction_filter=True,
            atr_expansion_filter=True, volume=1.0,
        )),
        # --- 13-16: 高品質シグナル + DI ---
        ("DI min5 ADX28 SL2/TP2", OptimizeConfig(
            min_signals=5, adx_threshold=28,
            rsi_oversold=28, rsi_overbought=72,
            sl_atr_mult=2.0, tp_atr_mult=2.0,
            cooldown_bars=6, mtf_bonus=4,
            require_trend_align=TA,
            di_direction_filter=True, volume=1.0,
        )),
        ("DI min5 ADX28 SL2.5/TP2.5", OptimizeConfig(
            min_signals=5, adx_threshold=28,
            rsi_oversold=28, rsi_overbought=72,
            sl_atr_mult=2.5, tp_atr_mult=2.5,
            cooldown_bars=6, mtf_bonus=4,
            require_trend_align=TA,
            di_direction_filter=True, volume=1.0,
        )),
        ("DI+ATR min5 ADX28 SL2/TP2", OptimizeConfig(
            min_signals=5, adx_threshold=28,
            rsi_oversold=28, rsi_overbought=72,
            sl_atr_mult=2.0, tp_atr_mult=2.0,
            cooldown_bars=6, mtf_bonus=4,
            require_trend_align=TA,
            di_direction_filter=True,
            atr_expansion_filter=True, volume=1.0,
        )),
        ("DI+ATR min4 ADX25 SL2/TP2.5", OptimizeConfig(
            min_signals=4, adx_threshold=25,
            rsi_oversold=30, rsi_overbought=70,
            sl_atr_mult=2.0, tp_atr_mult=2.5,
            cooldown_bars=4, mtf_bonus=3,
            require_trend_align=TA,
            di_direction_filter=True,
            atr_expansion_filter=True, volume=1.0,
        )),
    ]


def main() -> None:
    """エントリポイント"""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--workers", type=int, default=9,
    )
    args = parser.parse_args()

    from autotrader.backtest.optimizer import (
        _calculate_indicators,
        _load_mt5_csv,
        _run_single_config,
    )
    import concurrent.futures

    probes = build_probe_grid()
    configs = [c for _, c in probes]
    labels = [l for l, _ in probes]

    print(
        f"GBPUSD v3.5 プローブ"
        f" ({len(probes)}設定, {args.workers}コア)"
    )

    data_path = Path(DATA_DIR) / SYMBOL
    h1_f = list(data_path.glob(f"{SYMBOL}_H1_*.csv"))
    h4_f = list(data_path.glob(f"{SYMBOL}_H4_*.csv"))
    if not h1_f or not h4_f:
        print(f"データなし: {data_path}")
        sys.exit(1)

    print("データ読み込み...")
    df = _load_mt5_csv(h1_f[0])
    df = _calculate_indicators(df)
    h4 = _load_mt5_csv(h4_f[0])
    h4 = _calculate_indicators(h4)
    print(f"  H1: {len(df):,}  H4: {len(h4):,}\n")

    task_args = [
        (
            i, c, df, h4,
            TRAIN_YEARS, VALID_YEARS, SYMBOL,
        )
        for i, c in enumerate(configs)
    ]

    results = [None] * len(probes)
    with concurrent.futures.ProcessPoolExecutor(
        max_workers=args.workers,
    ) as ex:
        for res in ex.map(_run_single_config, task_args):
            idx, tr, vr = res
            results[idx] = (tr, vr)
            lbl = labels[idx]
            mark = "*" if vr["win_rate"] >= 70 else " "
            pfmark = "!" if vr["pf"] >= 1.3 else " "
            print(
                f"  [{idx+1:2d}/{len(probes)}]"
                f"{mark}{pfmark}"
                f" {lbl:<30}"
                f" T:{tr['trades']:>4}"
                f" {tr['win_rate']:>5.1f}%"
                f" PF{tr['pf']:.2f}"
                f"  V:{vr['trades']:>4}"
                f" {vr['win_rate']:>5.1f}%"
                f" PF{vr['pf']:.2f}"
                f" ${vr['net_profit']:>+6.0f}"
            )

    print("\n" + "=" * 90)
    print("結果サマリ（検証PF降順）")
    print("=" * 90)
    ranked = sorted(
        range(len(probes)),
        key=lambda i: results[i][1]["pf"]
        if results[i] else 0,
        reverse=True,
    )
    for r, i in enumerate(ranked, 1):
        tr, vr = results[i]
        lbl = labels[i]
        print(
            f"#{r:2d} {lbl:<30}"
            f" T:{tr['win_rate']:>5.1f}%"
            f" PF{tr['pf']:.2f}"
            f"  V:{vr['win_rate']:>5.1f}%"
            f" PF{vr['pf']:.2f}"
            f" ${vr['net_profit']:>+6.0f}"
            f"  ({vr['trades']}trades)"
        )


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
