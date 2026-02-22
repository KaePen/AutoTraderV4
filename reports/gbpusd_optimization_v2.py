"""GBPUSD パラメータ最適化 v2（改善周回）

前回v1結果（ADX>22, RSI=33/67, SL/TP=2.5/4.0, PF1.05）を起点に
より広いグリッドで勝率・収益率・DDを同時最適化する。

調整（フィッティング）期間: 2018-2020年（3年間）
検証（アウトオブサンプル）期間: 2021-2023年（3年間）
並列実行: 最大9コア

操作方法:
    python reports/gbpusd_optimization_v2.py
    python reports/gbpusd_optimization_v2.py --workers 4

結果: reports/gbpusd_opt_v2_<日時>.csv に出力（このスクリプトと同ツリー内）
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

# このスクリプトが置かれているツリーのルートを特定
# reports/ → プロジェクトルート
_SCRIPT_DIR = Path(__file__).resolve().parents[1]
_SRC_DIR = _SCRIPT_DIR / "src"
if _SRC_DIR.exists():
    sys.path.insert(0, str(_SRC_DIR))

# data/ は worktree になければメインへフォールバック
if (_SCRIPT_DIR / "data").exists():
    _DATA_ROOT = _SCRIPT_DIR
elif (_SCRIPT_DIR.parents[1] / "data").exists():
    _DATA_ROOT = _SCRIPT_DIR.parents[1]
else:
    _DATA_ROOT = _SCRIPT_DIR

# 出力は必ずこのスクリプトのツリーの reports/ に書く
_REPORTS_DIR = _SCRIPT_DIR / "reports"

# ----------------------------------------------------------------
# 定数
# ----------------------------------------------------------------
SYMBOL = "GBPUSD"
TRAIN_YEARS = (2018, 2020)   # 調整（フィッティング）期間
VALID_YEARS = (2021, 2023)   # 検証（アウトオブサンプル）期間
DATA_DIR = str(_DATA_ROOT / "data")
MAX_WORKERS_DEFAULT = 9


# ----------------------------------------------------------------
# v2 拡張パラメータグリッド（27セット）
# v1ベスト(ADX>22, RSI=33/67, SL2.5/TP4.0, CD4)周辺を系統探索
# ----------------------------------------------------------------
def build_v2_param_grid():
    """v2改善周回パラメータグリッド（27セット）

    探索戦略:
    - ADX閾値: 18〜28（より広い/狭いトレンドフィルター）
    - RSI帯: 3パターン（標準/厳格/超厳格）
    - SL/TP比率: 4パターン（低リスク〜高リスリワード）
    - クールダウン: 3, 4, 6バー
    - v1ベスト周辺の精密探索と新領域を含む

    Returns:
        list[OptimizeConfig]: 27セット
    """
    from autotrader.backtest.optimizer import OptimizeConfig

    return [
        # ---- v1ベスト精密周辺（ADX22, RSI33/67, SL2.5/TP4.0） ----
        # 1: v1ベスト（再現ベースライン）
        OptimizeConfig(
            min_signals=3, adx_threshold=22.0,
            rsi_oversold=33.0, rsi_overbought=67.0,
            sl_atr_mult=2.5, tp_atr_mult=4.0,
            cooldown_bars=4, mtf_bonus=2, volume=1.0,
        ),
        # 2: ADX少し緩める → より多くの設定でエントリー
        OptimizeConfig(
            min_signals=3, adx_threshold=18.0,
            rsi_oversold=33.0, rsi_overbought=67.0,
            sl_atr_mult=2.5, tp_atr_mult=4.0,
            cooldown_bars=4, mtf_bonus=2, volume=1.0,
        ),
        # 3: ADX厳格 → 高品質トレンドのみ
        OptimizeConfig(
            min_signals=3, adx_threshold=26.0,
            rsi_oversold=33.0, rsi_overbought=67.0,
            sl_atr_mult=2.5, tp_atr_mult=4.0,
            cooldown_bars=4, mtf_bonus=2, volume=1.0,
        ),

        # ---- RSI閾値バリエーション（ADX22固定） ----
        # 4: RSI超厳格（深い逆張りのみ）
        OptimizeConfig(
            min_signals=3, adx_threshold=22.0,
            rsi_oversold=28.0, rsi_overbought=72.0,
            sl_atr_mult=2.5, tp_atr_mult=4.0,
            cooldown_bars=4, mtf_bonus=2, volume=1.0,
        ),
        # 5: RSI標準（勝率/頻度のバランス）
        OptimizeConfig(
            min_signals=3, adx_threshold=22.0,
            rsi_oversold=35.0, rsi_overbought=65.0,
            sl_atr_mult=2.5, tp_atr_mult=4.0,
            cooldown_bars=4, mtf_bonus=2, volume=1.0,
        ),
        # 6: RSI広め（より多くのエントリー）
        OptimizeConfig(
            min_signals=3, adx_threshold=22.0,
            rsi_oversold=38.0, rsi_overbought=62.0,
            sl_atr_mult=2.5, tp_atr_mult=4.0,
            cooldown_bars=4, mtf_bonus=2, volume=1.0,
        ),

        # ---- SL/TP比率バリエーション ----
        # 7: 高R/R（TP広め、勝率犠牲でPF向上）
        OptimizeConfig(
            min_signals=3, adx_threshold=22.0,
            rsi_oversold=33.0, rsi_overbought=67.0,
            sl_atr_mult=2.0, tp_atr_mult=5.0,
            cooldown_bars=4, mtf_bonus=2, volume=1.0,
        ),
        # 8: 超高R/R（TP=6×ATR）
        OptimizeConfig(
            min_signals=3, adx_threshold=22.0,
            rsi_oversold=33.0, rsi_overbought=67.0,
            sl_atr_mult=2.0, tp_atr_mult=6.0,
            cooldown_bars=4, mtf_bonus=2, volume=1.0,
        ),
        # 9: バランス型（PF安定重視）
        OptimizeConfig(
            min_signals=3, adx_threshold=22.0,
            rsi_oversold=33.0, rsi_overbought=67.0,
            sl_atr_mult=2.0, tp_atr_mult=3.5,
            cooldown_bars=4, mtf_bonus=2, volume=1.0,
        ),

        # ---- クールダウン最適化（v1ベスト ADX22周辺） ----
        # 10: クールダウン短め（頻繁エントリー）
        OptimizeConfig(
            min_signals=3, adx_threshold=22.0,
            rsi_oversold=33.0, rsi_overbought=67.0,
            sl_atr_mult=2.5, tp_atr_mult=4.0,
            cooldown_bars=2, mtf_bonus=2, volume=1.0,
        ),
        # 11: クールダウン長め（厳選エントリー）
        OptimizeConfig(
            min_signals=3, adx_threshold=22.0,
            rsi_oversold=33.0, rsi_overbought=67.0,
            sl_atr_mult=2.5, tp_atr_mult=4.0,
            cooldown_bars=6, mtf_bonus=2, volume=1.0,
        ),
        # 12: クールダウンさらに長め
        OptimizeConfig(
            min_signals=3, adx_threshold=22.0,
            rsi_oversold=33.0, rsi_overbought=67.0,
            sl_atr_mult=2.5, tp_atr_mult=4.0,
            cooldown_bars=8, mtf_bonus=2, volume=1.0,
        ),

        # ---- min_signals 変化（シグナル品質） ----
        # 13: シグナル数増加（高品質フィルター）
        OptimizeConfig(
            min_signals=4, adx_threshold=22.0,
            rsi_oversold=33.0, rsi_overbought=67.0,
            sl_atr_mult=2.5, tp_atr_mult=4.0,
            cooldown_bars=4, mtf_bonus=2, volume=1.0,
        ),
        # 14: min4 + TP超広め
        OptimizeConfig(
            min_signals=4, adx_threshold=22.0,
            rsi_oversold=30.0, rsi_overbought=70.0,
            sl_atr_mult=2.5, tp_atr_mult=5.0,
            cooldown_bars=4, mtf_bonus=3, volume=1.0,
        ),
        # 15: min4 + ADX高め（Trend質重視）
        OptimizeConfig(
            min_signals=4, adx_threshold=26.0,
            rsi_oversold=30.0, rsi_overbought=70.0,
            sl_atr_mult=2.5, tp_atr_mult=4.0,
            cooldown_bars=4, mtf_bonus=3, volume=1.0,
        ),

        # ---- MTFボーナス強化 ----
        # 16: MTF+3
        OptimizeConfig(
            min_signals=3, adx_threshold=22.0,
            rsi_oversold=33.0, rsi_overbought=67.0,
            sl_atr_mult=2.5, tp_atr_mult=4.0,
            cooldown_bars=4, mtf_bonus=3, volume=1.0,
        ),
        # 17: MTF+4（上位足トレンド重視）
        OptimizeConfig(
            min_signals=3, adx_threshold=22.0,
            rsi_oversold=33.0, rsi_overbought=67.0,
            sl_atr_mult=2.5, tp_atr_mult=4.0,
            cooldown_bars=4, mtf_bonus=4, volume=1.0,
        ),
        # 18: MTF+4 + ADX高め + min4（超保守）
        OptimizeConfig(
            min_signals=4, adx_threshold=25.0,
            rsi_oversold=30.0, rsi_overbought=70.0,
            sl_atr_mult=3.0, tp_atr_mult=5.0,
            cooldown_bars=6, mtf_bonus=4, volume=1.0,
        ),

        # ---- 新領域探索（GBP固有ボラ対応） ----
        # 19: 超広SL（GBPのスパイク吸収）
        OptimizeConfig(
            min_signals=3, adx_threshold=20.0,
            rsi_oversold=33.0, rsi_overbought=67.0,
            sl_atr_mult=3.0, tp_atr_mult=5.0,
            cooldown_bars=4, mtf_bonus=2, volume=1.0,
        ),
        # 20: 超広SL + 高ADX
        OptimizeConfig(
            min_signals=3, adx_threshold=25.0,
            rsi_oversold=30.0, rsi_overbought=70.0,
            sl_atr_mult=3.0, tp_atr_mult=6.0,
            cooldown_bars=6, mtf_bonus=2, volume=1.0,
        ),
        # 21: SL=1.5（タイト）+ TP=4.0（asymmetric R/R）
        OptimizeConfig(
            min_signals=3, adx_threshold=22.0,
            rsi_oversold=33.0, rsi_overbought=67.0,
            sl_atr_mult=1.5, tp_atr_mult=4.0,
            cooldown_bars=4, mtf_bonus=2, volume=1.0,
        ),
        # 22: SL=1.5 + TP=5.0（超高R/R・タイトSL）
        OptimizeConfig(
            min_signals=3, adx_threshold=24.0,
            rsi_oversold=30.0, rsi_overbought=70.0,
            sl_atr_mult=1.5, tp_atr_mult=5.0,
            cooldown_bars=4, mtf_bonus=3, volume=1.0,
        ),
        # 23: ADX低め + RSI厳格 + 長いTP
        OptimizeConfig(
            min_signals=3, adx_threshold=18.0,
            rsi_oversold=28.0, rsi_overbought=72.0,
            sl_atr_mult=2.5, tp_atr_mult=5.0,
            cooldown_bars=6, mtf_bonus=2, volume=1.0,
        ),

        # ---- 複合改善候補 ----
        # 24: v1#3（ADX20, 高勝率・大PF）改良版
        OptimizeConfig(
            min_signals=3, adx_threshold=20.0,
            rsi_oversold=33.0, rsi_overbought=67.0,
            sl_atr_mult=2.5, tp_atr_mult=4.0,
            cooldown_bars=4, mtf_bonus=3, volume=1.0,
        ),
        # 25: ADX20 + TP5.0
        OptimizeConfig(
            min_signals=3, adx_threshold=20.0,
            rsi_oversold=33.0, rsi_overbought=67.0,
            sl_atr_mult=2.5, tp_atr_mult=5.0,
            cooldown_bars=4, mtf_bonus=3, volume=1.0,
        ),
        # 26: 最小シグナル型（低勝率・超高R/R）
        OptimizeConfig(
            min_signals=2, adx_threshold=25.0,
            rsi_oversold=28.0, rsi_overbought=72.0,
            sl_atr_mult=2.0, tp_atr_mult=6.0,
            cooldown_bars=4, mtf_bonus=3, volume=1.0,
        ),
        # 27: バランス最適（ADX22, RSI30/70, SL2/TP4, CD4）
        OptimizeConfig(
            min_signals=3, adx_threshold=22.0,
            rsi_oversold=30.0, rsi_overbought=70.0,
            sl_atr_mult=2.0, tp_atr_mult=4.0,
            cooldown_bars=4, mtf_bonus=2, volume=1.0,
        ),
    ]


def dd_adjusted_score(
    valid: dict,
    train: dict,
    dd_penalty_weight: float = 2.0,
) -> float:
    """DD加味スコア（改善版）

    - 検証PF × 勝率 × 利益 でベーススコア
    - 最大DDが大きいほどペナルティ
    - 訓練→検証のPF劣化が大きいほどペナルティ（過学習防止）

    Args:
        valid: 検証期間結果
        train: 訓練期間結果
        dd_penalty_weight: DD penaltyの重み

    Returns:
        スコア（高いほど優秀）
    """
    if valid["net_profit"] <= 0 or valid["pf"] <= 1.0:
        return 0.0
    if valid["trades"] < 30:
        # サンプル数が少なすぎる場合はペナルティ
        return 0.0

    base = (
        (valid["win_rate"] / 100)
        * valid["pf"]
        * (valid["net_profit"] / 10)
    )

    # DD penalty（DDが大きいほどスコアが下がる）
    dd = max(valid["max_dd"], 0.001)
    dd_penalty = 1.0 / (1.0 + dd * dd_penalty_weight)

    # 過学習ペナルティ（PF劣化率）
    pf_degradation = max(
        0.0, (train["pf"] - valid["pf"]) / max(train["pf"], 0.001)
    )
    overfit_penalty = 1.0 - min(pf_degradation * 0.5, 0.5)

    return base * dd_penalty * overfit_penalty


def save_csv(results: list, out_path: Path) -> None:
    """最適化結果をCSVに保存

    Args:
        results: (score, config, train, valid) タプルリスト
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
        for rank, (score, cfg, tr, vr) in enumerate(
            results, 1
        ):
            writer.writerow({
                "rank": rank,
                "min_signals": cfg.min_signals,
                "adx_threshold": cfg.adx_threshold,
                "rsi_oversold": cfg.rsi_oversold,
                "rsi_overbought": cfg.rsi_overbought,
                "sl_atr_mult": cfg.sl_atr_mult,
                "tp_atr_mult": cfg.tp_atr_mult,
                "cooldown_bars": cfg.cooldown_bars,
                "mtf_bonus": cfg.mtf_bonus,
                "train_trades": tr["trades"],
                "train_win_rate": f"{tr['win_rate']:.1f}",
                "train_pf": f"{tr['pf']:.3f}",
                "train_net_profit": f"{tr['net_profit']:.2f}",
                "train_max_dd": f"{tr['max_dd']:.3f}",
                "valid_trades": vr["trades"],
                "valid_win_rate": f"{vr['win_rate']:.1f}",
                "valid_pf": f"{vr['pf']:.3f}",
                "valid_net_profit": f"{vr['net_profit']:.2f}",
                "valid_max_dd": f"{vr['max_dd']:.3f}",
                "score": f"{score:.6f}",
            })


def print_results(
    sorted_results: list,
    param_grid: list,
) -> None:
    """結果を表示

    Args:
        sorted_results: (score, config, train, valid)タプルリスト
        param_grid: 全パラメータグリッド
    """
    print("\n" + "=" * 90)
    print(
        f"【GBPUSD最適化 v2 完了】"
        f"  調整: {TRAIN_YEARS[0]}-{TRAIN_YEARS[1]}  "
        f"  検証: {VALID_YEARS[0]}-{VALID_YEARS[1]}  "
        f"  全{len(param_grid)}設定"
    )
    print("=" * 90)
    print(
        f"\n{'順位':<5} {'設定概要':<44} "
        f"{'訓練':>24} {'検証':>24}"
    )
    print(
        f"{'':5} {'':44} "
        f"{'回/勝率/PF/損益':>24} {'回/勝率/PF/損益':>24}"
    )
    print("-" * 100)

    for rank, (score, cfg, tr, vr) in enumerate(
        sorted_results[:10], 1
    ):
        label = (
            f"min{cfg.min_signals}"
            f" ADX{cfg.adx_threshold:.0f}"
            f" RSI{cfg.rsi_oversold:.0f}/{cfg.rsi_overbought:.0f}"
            f" SL{cfg.sl_atr_mult}/TP{cfg.tp_atr_mult}"
            f" CD{cfg.cooldown_bars}"
            f" M+{cfg.mtf_bonus}"
        )
        print(
            f"#{rank:<4} {label:<44} "
            f"{tr['trades']:>4}回"
            f" {tr['win_rate']:>5.1f}%"
            f" {tr['pf']:>5.2f}"
            f" ${tr['net_profit']:>+8.0f}"
            f"  |  "
            f"{vr['trades']:>4}回"
            f" {vr['win_rate']:>5.1f}%"
            f" {vr['pf']:>5.2f}"
            f" ${vr['net_profit']:>+8.0f}"
            f"  score={score:.4f}"
        )


def main() -> None:
    """エントリポイント"""
    parser = argparse.ArgumentParser(
        description="GBPUSD パラメータ最適化 v2",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=MAX_WORKERS_DEFAULT,
        help=f"並列コア数（デフォルト: {MAX_WORKERS_DEFAULT}）",
    )
    args = parser.parse_args()

    from autotrader.backtest.optimizer import (
        _calculate_indicators,
        _load_mt5_csv,
        _run_single_config,
    )
    import concurrent.futures
    from pathlib import Path

    param_grid = build_v2_param_grid()
    print(
        f"GBPUSD最適化 v2 開始\n"
        f"  パラメータ数: {len(param_grid)}\n"
        f"  並列コア数: {args.workers}\n"
        f"  調整期間: {TRAIN_YEARS[0]}-{TRAIN_YEARS[1]}年\n"
        f"  検証期間: {VALID_YEARS[0]}-{VALID_YEARS[1]}年"
    )

    # データ読み込み・指標計算
    data_path = Path(DATA_DIR) / SYMBOL
    h1_files = list(data_path.glob(f"{SYMBOL}_H1_*.csv"))
    h4_files = list(data_path.glob(f"{SYMBOL}_H4_*.csv"))
    if not h1_files or not h4_files:
        print(f"データが見つかりません: {data_path}")
        sys.exit(1)

    print("\nデータ読み込み・指標計算中...")
    df = _load_mt5_csv(h1_files[0])
    df = _calculate_indicators(df)
    h4_df = _load_mt5_csv(h4_files[0])
    h4_df = _calculate_indicators(h4_df)
    print(f"  H1: {len(df):,}本, H4: {len(h4_df):,}本")

    # 並列バックテスト実行
    print(f"\n{len(param_grid)}設定を{args.workers}コア並列で評価中...")
    task_args = [
        (
            i, cfg, df, h4_df,
            TRAIN_YEARS, VALID_YEARS, SYMBOL,
        )
        for i, cfg in enumerate(param_grid)
    ]

    raw: list[tuple[int, dict, dict]] = []
    with concurrent.futures.ProcessPoolExecutor(
        max_workers=args.workers
    ) as executor:
        for res in executor.map(_run_single_config, task_args):
            raw.append(res)
            idx, tr, vr = res
            cfg = param_grid[idx]
            indicator = (
                "✓" if vr["pf"] >= 1.0
                and vr["net_profit"] > 0 else " "
            )
            print(
                f"  [{idx+1:2d}/{len(param_grid)}]{indicator}"
                f" ADX{cfg.adx_threshold:.0f}"
                f" RSI{cfg.rsi_oversold:.0f}/{cfg.rsi_overbought:.0f}"
                f" SL{cfg.sl_atr_mult}/TP{cfg.tp_atr_mult}"
                f" CD{cfg.cooldown_bars}"
                f" → 訓練PF{tr['pf']:.2f}"
                f" 検証PF{vr['pf']:.2f}"
                f" 損益${vr['net_profit']:+.0f}"
            )

    # スコア計算・ソート
    raw.sort(key=lambda x: x[0])
    scored = []
    for idx, tr, vr in raw:
        cfg = param_grid[idx]
        score = dd_adjusted_score(vr, tr)
        scored.append((score, cfg, tr, vr))
    scored.sort(key=lambda x: x[0], reverse=True)

    print_results(scored, param_grid)

    # CSV保存
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = _REPORTS_DIR / f"gbpusd_opt_v2_{timestamp}.csv"
    save_csv(scored, csv_path)
    print(f"\nCSV保存: {csv_path}")


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
