"""GBPUSD パラメータ最適化 v4（トレーリングストップ比較）

v3ベスト設定を起点に、トレーリングストップON/OFFの効果を比較。
固定TP/SLのみ vs PositionManager（トレーリング+建値移動+部分決済）
でPF改善を検証する。

調整（フィッティング）期間: 2018-2020年（3年間）
検証（アウトオブサンプル）期間: 2021-2023年（3年間）
並列実行: 最大9コア

操作方法:
    python reports/gbpusd_optimization_v4.py
    python reports/gbpusd_optimization_v4.py --workers 4

結果: reports/gbpusd_opt_v4_<日時>.csv に出力
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
TRAIN_YEARS = (2018, 2020)
VALID_YEARS = (2021, 2023)
DATA_DIR = str(_DATA_ROOT / "data")
MAX_WORKERS_DEFAULT = 9


# ----------------------------------------------------------------
# v4 パラメータグリッド（トレーリングストップ比較）
# ----------------------------------------------------------------
def build_v4_param_grid():
    """v4パラメータグリッド: 固定TP vs トレーリングストップ比較

    v3ベスト設定をベースに:
    - 固定TP/SL（ベースライン）
    - トレーリングON + 各種パラメータ組み合わせ

    Returns:
        list[OptimizeConfig]: パラメータ設定リスト
    """
    from autotrader.backtest.optimizer import OptimizeConfig

    grid = []

    # --- ベースライン（固定TP/SL、トレーリングOFF） ---
    base_configs = [
        # v3#1: ADX22, RSI33/67, SL2.5/TP4.0
        dict(
            adx_threshold=22.0,
            rsi_oversold=33.0, rsi_overbought=67.0,
            sl_atr_mult=2.5, tp_atr_mult=4.0,
            cooldown_bars=4, mtf_bonus=2,
        ),
        # v3#2: ADX20, RSI33/67, SL2.5/TP4.0
        dict(
            adx_threshold=20.0,
            rsi_oversold=33.0, rsi_overbought=67.0,
            sl_atr_mult=2.5, tp_atr_mult=4.0,
            cooldown_bars=4, mtf_bonus=3,
        ),
        # v3#3: ADX22, RSI33/67, SL2.0/TP5.0（高RR）
        dict(
            adx_threshold=22.0,
            rsi_oversold=33.0, rsi_overbought=67.0,
            sl_atr_mult=2.0, tp_atr_mult=5.0,
            cooldown_bars=4, mtf_bonus=2,
        ),
        # v3#4: ADX22, RSI30/70, SL2.0/TP4.0
        dict(
            adx_threshold=22.0,
            rsi_oversold=30.0, rsi_overbought=70.0,
            sl_atr_mult=2.0, tp_atr_mult=4.0,
            cooldown_bars=4, mtf_bonus=2,
        ),
    ]

    # ベースライン（トレーリングOFF）
    for bc in base_configs:
        grid.append(OptimizeConfig(
            min_signals=3, volume=1.0,
            use_position_manager=False,
            **bc,
        ))

    # --- トレーリングストップON ---
    trailing_params = [
        # (start_r, atr_mult, breakeven)
        (1.0, 1.5, True),   # 早期開始 + タイト
        (1.0, 2.0, True),   # 早期開始 + 標準
        (1.5, 1.5, True),   # 標準開始 + タイト
        (1.5, 2.0, True),   # 標準開始 + 標準
        (1.5, 2.5, True),   # 標準開始 + ワイド
        (2.0, 2.0, True),   # 遅延開始 + 標準
        (2.0, 3.0, True),   # 遅延開始 + ワイド
        (1.5, 2.0, False),  # 標準開始 + BE移動なし
    ]

    for bc in base_configs:
        for start_r, atr_mult, be in trailing_params:
            grid.append(OptimizeConfig(
                min_signals=3, volume=1.0,
                use_position_manager=True,
                trailing_start_r=start_r,
                trailing_atr_multiplier=atr_mult,
                breakeven_at_1r=be,
                **bc,
            ))

    return grid


def dd_adjusted_score(
    valid: dict,
    train: dict,
    dd_penalty_weight: float = 2.0,
) -> float:
    """DD加味スコア

    Args:
        valid: 検証期間結果
        train: 訓練期間結果
        dd_penalty_weight: DD penaltyの重み

    Returns:
        スコア（高いほど優秀）
    """
    if valid["net_profit"] <= 0 or valid["pf"] <= 1.0:
        return 0.0
    if valid["trades"] < 20:
        return 0.0

    base = (
        (valid["win_rate"] / 100)
        * valid["pf"]
        * (valid["net_profit"] / 10)
    )

    dd = max(valid["max_dd"], 0.001)
    dd_penalty = 1.0 / (1.0 + dd * dd_penalty_weight)

    pf_degradation = max(
        0.0,
        (train["pf"] - valid["pf"]) / max(train["pf"], 0.001),
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
        "use_pm", "trail_start_r",
        "trail_atr_mult", "breakeven_1r",
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
            results, 1,
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
                "use_pm": cfg.use_position_manager,
                "trail_start_r": cfg.trailing_start_r,
                "trail_atr_mult": cfg.trailing_atr_multiplier,
                "breakeven_1r": cfg.breakeven_at_1r,
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
    print("\n" + "=" * 110)
    print(
        f"【GBPUSD最適化 v4 完了】"
        f"  調整: {TRAIN_YEARS[0]}-{TRAIN_YEARS[1]}  "
        f"  検証: {VALID_YEARS[0]}-{VALID_YEARS[1]}  "
        f"  全{len(param_grid)}設定"
    )
    print("=" * 110)

    baselines = [
        (s, c, t, v) for s, c, t, v in sorted_results
        if not c.use_position_manager
    ]
    trailings = [
        (s, c, t, v) for s, c, t, v in sorted_results
        if c.use_position_manager
    ]

    print("\n--- ベースライン（固定TP/SL）TOP 5 ---")
    _print_top(baselines[:5])

    print("\n--- トレーリングストップON TOP 10 ---")
    _print_top(trailings[:10])

    if baselines and trailings:
        best_base_pf = baselines[0][3]["pf"]
        best_trail_pf = trailings[0][3]["pf"]
        pf_delta = best_trail_pf - best_base_pf
        print(
            f"\n--- PF改善: ベスト固定={best_base_pf:.2f}"
            f" -> ベストトレーリング={best_trail_pf:.2f}"
            f" (差分={pf_delta:+.2f}) ---"
        )


def _print_top(results: list) -> None:
    """上位結果を表示

    Args:
        results: (score, config, train, valid)タプルリスト
    """
    for rank, (score, cfg, tr, vr) in enumerate(results, 1):
        pm_label = ""
        if cfg.use_position_manager:
            pm_label = (
                f" TRAIL(R{cfg.trailing_start_r}"
                f"/ATR{cfg.trailing_atr_multiplier}"
                f"/BE={'Y' if cfg.breakeven_at_1r else 'N'})"
            )
        label = (
            f"ADX{cfg.adx_threshold:.0f}"
            f" RSI{cfg.rsi_oversold:.0f}/{cfg.rsi_overbought:.0f}"
            f" SL{cfg.sl_atr_mult}/TP{cfg.tp_atr_mult}"
            f" CD{cfg.cooldown_bars}"
            f" M+{cfg.mtf_bonus}"
            f"{pm_label}"
        )
        print(
            f"#{rank:<3} {label:<65} "
            f"訓練: {tr['trades']:>3}回"
            f" {tr['win_rate']:>5.1f}%"
            f" PF{tr['pf']:>5.2f}"
            f" ${tr['net_profit']:>+8.0f}"
            f"  |  "
            f"検証: {vr['trades']:>3}回"
            f" {vr['win_rate']:>5.1f}%"
            f" PF{vr['pf']:>5.2f}"
            f" ${vr['net_profit']:>+8.0f}"
        )


def main() -> None:
    """エントリポイント"""
    parser = argparse.ArgumentParser(
        description="GBPUSD パラメータ最適化 v4（トレーリング比較）",
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

    param_grid = build_v4_param_grid()
    n_base = sum(
        1 for c in param_grid if not c.use_position_manager
    )
    n_trail = sum(
        1 for c in param_grid if c.use_position_manager
    )
    print(
        f"GBPUSD最適化 v4 開始\n"
        f"  パラメータ数: {len(param_grid)}"
        f" (ベースライン{n_base} + トレーリング{n_trail})\n"
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
    print(
        f"\n{len(param_grid)}設定を"
        f"{args.workers}コア並列で評価中..."
    )
    task_args = [
        (
            i, cfg, df, h4_df,
            TRAIN_YEARS, VALID_YEARS, SYMBOL,
        )
        for i, cfg in enumerate(param_grid)
    ]

    raw: list[tuple[int, dict, dict]] = []
    with concurrent.futures.ProcessPoolExecutor(
        max_workers=args.workers,
    ) as executor:
        for res in executor.map(
            _run_single_config, task_args,
        ):
            raw.append(res)
            idx, tr, vr = res
            cfg = param_grid[idx]
            pm_tag = (
                "TRAIL" if cfg.use_position_manager
                else "FIXED"
            )
            indicator = (
                "+" if vr["pf"] >= 1.0
                and vr["net_profit"] > 0 else " "
            )
            print(
                f"  [{idx+1:2d}/{len(param_grid)}]"
                f"{indicator} {pm_tag:5}"
                f" ADX{cfg.adx_threshold:.0f}"
                f" SL{cfg.sl_atr_mult}/TP{cfg.tp_atr_mult}"
                f" -> 検証PF{vr['pf']:.2f}"
                f" ${vr['net_profit']:+.0f}"
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
    csv_path = (
        _REPORTS_DIR / f"gbpusd_opt_v4_{timestamp}.csv"
    )
    save_csv(scored, csv_path)
    print(f"\nCSV保存: {csv_path}")


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
