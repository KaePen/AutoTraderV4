"""GBPUSD パラメータ最適化スクリプト

調整（フィッティング）期間: 2018-2020年（3年間）
検証（アウトオブサンプル）期間: 2021-2023年（3年間）
並列実行: 最大9コア

操作方法:
    python reports/gbpusd_optimization.py
    python reports/gbpusd_optimization.py --workers 4

結果は reports/gbpusd_opt_<日時>.csv に出力。
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import sys
from datetime import datetime
from pathlib import Path

# Windows cp932 回避
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace",
    )
    sys.stderr = io.TextIOWrapper(
        sys.stderr.buffer, encoding="utf-8", errors="replace",
    )
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# プロジェクトルートをパスに追加
# このスクリプトは worktree 内の reports/ にある想定だが、
# メインプロジェクトの reports/ から実行される場合もある。
_SCRIPT_DIR = Path(__file__).resolve().parents[1]

# src はこのスクリプトが置かれているツリーのものを優先使用
_SRC_DIR = _SCRIPT_DIR / "src"
if _SRC_DIR.exists():
    sys.path.insert(0, str(_SRC_DIR))

# data はメインプロジェクトへフォールバック
if (_SCRIPT_DIR / "data").exists():
    _DATA_ROOT = _SCRIPT_DIR
elif (_SCRIPT_DIR.parents[1] / "data").exists():
    # tmp/<branch>/ → tmp/ → メインプロジェクト
    _DATA_ROOT = _SCRIPT_DIR.parents[1]
else:
    _DATA_ROOT = _SCRIPT_DIR

# ----------------------------------------------------------------
# 定数
# ----------------------------------------------------------------
SYMBOL = "GBPUSD"
TRAIN_YEARS = (2018, 2020)   # 調整（フィッティング）期間
VALID_YEARS = (2021, 2023)   # 検証（アウトオブサンプル）期間
DATA_DIR = str(_DATA_ROOT / "data")
REPORTS_DIR = _DATA_ROOT / "reports"
MAX_WORKERS_DEFAULT = 9


def build_gbpusd_param_grid():
    """GBPUSD向けパラメータグリッドを構築

    GBPUSDの特性（値動き大きめ）に合わせて9セット用意。
    各セットは1コアに割り当てられる。

    Returns:
        list[OptimizeConfig]: 9セットのパラメータリスト
    """
    from autotrader.backtest.optimizer import OptimizeConfig

    return [
        # 1: 標準設定（ベースライン）
        OptimizeConfig(
            min_signals=3, adx_threshold=20.0,
            rsi_oversold=35.0, rsi_overbought=65.0,
            sl_atr_mult=2.0, tp_atr_mult=3.0,
            cooldown_bars=4, volume=1.0,
        ),
        # 2: 勝率重視（TP短め・SL短め）
        OptimizeConfig(
            min_signals=3, adx_threshold=20.0,
            rsi_oversold=35.0, rsi_overbought=65.0,
            sl_atr_mult=1.5, tp_atr_mult=2.0,
            cooldown_bars=4, volume=1.0,
        ),
        # 3: リスクリワード重視（TP広め）
        OptimizeConfig(
            min_signals=3, adx_threshold=20.0,
            rsi_oversold=35.0, rsi_overbought=65.0,
            sl_atr_mult=1.5, tp_atr_mult=3.5,
            cooldown_bars=4, volume=1.0,
        ),
        # 4: 高品質シグナル（min_signals=4・ADX厳格）
        OptimizeConfig(
            min_signals=4, adx_threshold=25.0,
            rsi_oversold=30.0, rsi_overbought=70.0,
            sl_atr_mult=2.0, tp_atr_mult=3.0,
            cooldown_bars=6, volume=1.0,
        ),
        # 5: GBP特化（値動き大→SL広め・TP広め）
        OptimizeConfig(
            min_signals=3, adx_threshold=22.0,
            rsi_oversold=33.0, rsi_overbought=67.0,
            sl_atr_mult=2.5, tp_atr_mult=4.0,
            cooldown_bars=4, volume=1.0,
        ),
        # 6: RSI厳格（過売・過買の深い水準のみ）
        OptimizeConfig(
            min_signals=3, adx_threshold=20.0,
            rsi_oversold=28.0, rsi_overbought=72.0,
            sl_atr_mult=2.0, tp_atr_mult=3.0,
            cooldown_bars=4, volume=1.0,
        ),
        # 7: クールダウン短め（頻繁エントリー）
        OptimizeConfig(
            min_signals=3, adx_threshold=20.0,
            rsi_oversold=35.0, rsi_overbought=65.0,
            sl_atr_mult=2.0, tp_atr_mult=3.0,
            cooldown_bars=2, volume=1.0,
        ),
        # 8: MTFボーナス強化
        OptimizeConfig(
            min_signals=3, adx_threshold=20.0,
            rsi_oversold=35.0, rsi_overbought=65.0,
            sl_atr_mult=2.0, tp_atr_mult=3.0,
            cooldown_bars=4, mtf_bonus=4, volume=1.0,
        ),
        # 9: 保守型（ADX高め・MTF重視・SL広め）
        OptimizeConfig(
            min_signals=4, adx_threshold=28.0,
            rsi_oversold=32.0, rsi_overbought=68.0,
            sl_atr_mult=2.5, tp_atr_mult=3.5,
            cooldown_bars=6, mtf_bonus=3, volume=1.0,
        ),
    ]


def save_csv(
    results: list,
    out_path: Path,
) -> None:
    """最適化結果をCSVに保存

    Args:
        results: OptimizeResultリスト（スコア降順）
        out_path: 出力CSVパス
    """
    fieldnames = [
        "rank",
        "min_signals", "adx_threshold",
        "rsi_oversold", "rsi_overbought",
        "sl_atr_mult", "tp_atr_mult",
        "cooldown_bars", "mtf_bonus",
        "train_trades", "train_win_rate",
        "train_pf", "train_net_profit", "train_max_dd",
        "valid_trades", "valid_win_rate",
        "valid_pf", "valid_net_profit", "valid_max_dd",
        "score",
    ]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for rank, r in enumerate(results, 1):
            c = r.config
            t = r.train
            v = r.valid
            writer.writerow({
                "rank": rank,
                "min_signals": c.min_signals,
                "adx_threshold": c.adx_threshold,
                "rsi_oversold": c.rsi_oversold,
                "rsi_overbought": c.rsi_overbought,
                "sl_atr_mult": c.sl_atr_mult,
                "tp_atr_mult": c.tp_atr_mult,
                "cooldown_bars": c.cooldown_bars,
                "mtf_bonus": c.mtf_bonus,
                "train_trades": t["trades"],
                "train_win_rate": f"{t['win_rate']:.1f}",
                "train_pf": f"{t['pf']:.2f}",
                "train_net_profit": f"{t['net_profit']:.2f}",
                "train_max_dd": f"{t['max_dd']:.2f}",
                "valid_trades": v["trades"],
                "valid_win_rate": f"{v['win_rate']:.1f}",
                "valid_pf": f"{v['pf']:.2f}",
                "valid_net_profit": f"{v['net_profit']:.2f}",
                "valid_max_dd": f"{v['max_dd']:.2f}",
                "score": f"{r.score:.4f}",
            })


def print_summary(results: list) -> None:
    """結果サマリーを出力

    Args:
        results: OptimizeResultリスト（スコア降順）
    """
    print("\n" + "=" * 80)
    print(f"【GBPUSD最適化 完了】")
    print(
        f"  調整期間: {TRAIN_YEARS[0]}-{TRAIN_YEARS[1]}年"
        f"（3年）"
    )
    print(
        f"  検証期間: {VALID_YEARS[0]}-{VALID_YEARS[1]}年"
        f"（3年）"
    )
    print(f"  評価設定数: {len(results)}")
    print("=" * 80)

    print("\n--- アウトオブサンプル（検証期間）上位5設定 ---\n")
    for i, r in enumerate(results[:5]):
        c = r.config
        t = r.train
        v = r.valid
        label = (
            f"  min_sig={c.min_signals}  ADX>{c.adx_threshold:.0f}"
            f"  RSI={c.rsi_oversold:.0f}/{c.rsi_overbought:.0f}"
            f"  SL/TP={c.sl_atr_mult}/{c.tp_atr_mult}"
            f"  CD={c.cooldown_bars}  MTF+{c.mtf_bonus}"
        )
        print(f"#{i+1} {label}")
        print(
            f"     訓練: "
            f"{t['trades']}回  勝率{t['win_rate']:.1f}%"
            f"  PF{t['pf']:.2f}"
            f"  損益${t['net_profit']:+,.0f}"
            f"  DD{t['max_dd']:.1f}%"
        )
        print(
            f"     検証: "
            f"{v['trades']}回  勝率{v['win_rate']:.1f}%"
            f"  PF{v['pf']:.2f}"
            f"  損益${v['net_profit']:+,.0f}"
            f"  DD{v['max_dd']:.1f}%"
            f"  スコア{r.score:.4f}"
        )
        print()


def main() -> None:
    """エントリポイント"""
    parser = argparse.ArgumentParser(
        description="GBPUSD パラメータ最適化",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=MAX_WORKERS_DEFAULT,
        help=f"並列コア数（デフォルト: {MAX_WORKERS_DEFAULT}）",
    )
    args = parser.parse_args()

    from autotrader.backtest.optimizer import run_optimization

    param_grid = build_gbpusd_param_grid()

    print(f"GBPUSD最適化開始")
    print(f"  パラメータ数: {len(param_grid)}")
    print(f"  並列コア数: {args.workers}")

    results = run_optimization(
        data_dir=DATA_DIR,
        symbol=SYMBOL,
        train_years=TRAIN_YEARS,
        valid_years=VALID_YEARS,
        param_grid=param_grid,
        max_workers=args.workers,
    )

    if not results:
        print("結果なし（データ確認を）")
        sys.exit(1)

    print_summary(results)

    # CSV保存
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = REPORTS_DIR / f"gbpusd_opt_{timestamp}.csv"
    save_csv(results, csv_path)
    print(f"\nCSV保存: {csv_path}")


if __name__ == "__main__":
    # Windows の ProcessPoolExecutor は spawn を使うため
    # このガードが必須（なければ子プロセスでも main() が実行される）
    import multiprocessing
    multiprocessing.freeze_support()
    main()
