"""EURUSD 2018-2020 パラメータ最適化スクリプト

調整期間: 2018-2020年（フィッティングに使用）
検証期間: 2021-2023年（参照のみ、フィッティング禁止）
リソース: 最大9コア（3パラメータ×3年の並列を想定）

実行方法:
    python reports/eurusd_optimization.py
"""
from __future__ import annotations

import concurrent.futures
import sys
from datetime import datetime
from pathlib import Path

# パスを解決（worktree対応: git共通ディレクトリからメインリポジトリを探す）
import subprocess

_worktree_root = Path(__file__).parent.parent
_git_common_dir = subprocess.check_output(
    ["git", "rev-parse", "--git-common-dir"],
    cwd=str(_worktree_root),
    text=True,
).strip()
# .git/ の親がメインリポジトリルート
MAIN_ROOT = Path(_git_common_dir).parent
sys.path.insert(0, str(MAIN_ROOT))

from autotrader.backtest.optimizer import (  # noqa: E402
    OptimizeConfig,
    _calculate_indicators,
    _load_mt5_csv,
    get_default_param_grid,
    run_backtest_period,
)

SYMBOL = "EURUSD"
TRAIN_START = 2018
TRAIN_END = 2020
VALID_START = 2021
VALID_END = 2023
MAX_WORKERS = 9
DATA_DIR = MAIN_ROOT / "data"
REPORTS_DIR = MAIN_ROOT / "reports"


def run_config_worker(
    args: tuple,
) -> tuple[int, dict, dict, float]:
    """並列ワーカー：1パラメータ設定のバックテスト実行

    Args:
        args: (config_idx, config, symbol, train_start, train_end,
               valid_start, valid_end, data_dir)

    Returns:
        (config_idx, train_result, valid_result, score)
    """
    (
        config_idx,
        config,
        symbol,
        train_start,
        train_end,
        valid_start,
        valid_end,
        data_dir_str,
    ) = args

    # 各ワーカーでデータをロード（プロセス間コピーを避けるため）
    data_path = Path(data_dir_str) / symbol
    h1_files = list(data_path.glob(f"{symbol}_H1_*.csv"))
    h4_files = list(data_path.glob(f"{symbol}_H4_*.csv"))

    df = _load_mt5_csv(h1_files[0])
    df = _calculate_indicators(df)
    h4_df = _load_mt5_csv(h4_files[0])
    h4_df = _calculate_indicators(h4_df)

    # 訓練期間バックテスト（2018-2020）
    train_result = run_backtest_period(
        df, h4_df, train_start, train_end, config, symbol
    )
    # 検証期間バックテスト（2021-2023、参照のみ）
    valid_result = run_backtest_period(
        df, h4_df, valid_start, valid_end, config, symbol
    )

    # スコア計算（検証期間の結果で評価）
    score = 0.0
    v = valid_result
    if v["net_profit"] > 0 and v["pf"] > 0:
        score = (
            (v["win_rate"] / 100)
            * v["pf"]
            * (v["net_profit"] / 100000)
        )

    return config_idx, train_result, valid_result, score


def main() -> None:
    """EURUSD最適化メイン処理"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = REPORTS_DIR / f"eurusd_opt_{timestamp}.txt"

    param_grid = get_default_param_grid()

    header_lines = [
        "=" * 80,
        "EURUSD パラメータ最適化",
        f"実行日時: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"シンボル: {SYMBOL}",
        f"訓練期間: {TRAIN_START}-{TRAIN_END}年（最適化に使用）",
        f"検証期間: {VALID_START}-{VALID_END}年（参照のみ、フィッティング禁止）",
        f"並列ワーカー数: {MAX_WORKERS}",
        f"パラメータ設定数: {len(param_grid)}",
        "=" * 80,
    ]
    for line in header_lines:
        print(line)

    # 並列実行用引数を準備
    worker_args = [
        (
            i,
            config,
            SYMBOL,
            TRAIN_START,
            TRAIN_END,
            VALID_START,
            VALID_END,
            str(DATA_DIR),
        )
        for i, config in enumerate(param_grid)
    ]

    print(
        f"\n{len(param_grid)}設定を最大{MAX_WORKERS}コアで並列実行中..."
    )

    results_raw: list[tuple[int, dict, dict, float]] = []
    with concurrent.futures.ProcessPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:
        futures = {
            executor.submit(run_config_worker, args): args[0]
            for args in worker_args
        }
        for future in concurrent.futures.as_completed(futures):
            idx = futures[future]
            try:
                result = future.result()
                results_raw.append(result)
                config_idx, train_r, valid_r, score = result
                print(
                    f"  #{config_idx+1:02d} 完了 "
                    f"| 訓練: 勝率{train_r['win_rate']:.1f}%"
                    f" PF={train_r['pf']:.2f}"
                    f" ¥{train_r['net_profit']:+,.0f}"
                    f" | 検証: 勝率{valid_r['win_rate']:.1f}%"
                    f" PF={valid_r['pf']:.2f}"
                    f" ¥{valid_r['net_profit']:+,.0f}"
                )
            except Exception as e:
                print(f"  #{idx+1:02d} エラー: {e}")

    if not results_raw:
        print("エラー: 全設定が失敗しました")
        return

    # インデックス順にソート後、スコア降順でソート
    results_raw.sort(key=lambda x: x[0])
    results: list[tuple[OptimizeConfig, dict, dict, float]] = [
        (param_grid[idx], train_r, valid_r, score)
        for idx, train_r, valid_r, score in results_raw
    ]
    results_sorted = sorted(
        results, key=lambda x: x[3], reverse=True
    )

    # 結果テーブル出力
    sep = "-" * 120
    col_header = (
        f"{'#':<4} {'min_sig':<8} {'ADX':<6} "
        f"{'RSI':<10} {'SL/TP':<10} {'CD':<4} | "
        f"{'勝率':>8} {'PF':>8} {'利益':>14} | "
        f"{'勝率':>8} {'PF':>8} {'利益':>14}"
    )
    col_sub = (
        f"{'':4} {'':8} {'':6} {'':10} {'':10} {'':4}"
        f" | {'(訓練)':>8} {'':>8} {'':>14}"
        f" | {'(検証)':>8} {'':>8} {'':>14}"
    )

    result_lines = [
        "",
        "全設定の結果（検証スコア降順）",
        sep,
        col_header,
        col_sub,
        sep,
    ]

    for i, (config, train_r, valid_r, score) in enumerate(
        results_sorted
    ):
        result_lines.append(
            f"{i+1:<4} "
            f"{config.min_signals:<8} "
            f"{config.adx_threshold:<6.0f} "
            f"{config.rsi_oversold:.0f}"
            f"/{config.rsi_overbought:.0f}  "
            f"{config.sl_atr_mult:.1f}"
            f"/{config.tp_atr_mult:.1f}  "
            f"{config.cooldown_bars:<4} | "
            f"{train_r['win_rate']:>7.1f}% "
            f"{train_r['pf']:>7.2f} "
            f"¥{train_r['net_profit']:>+12,.0f} | "
            f"{valid_r['win_rate']:>7.1f}% "
            f"{valid_r['pf']:>7.2f} "
            f"¥{valid_r['net_profit']:>+12,.0f}"
        )

    result_lines += [
        "",
        "=" * 80,
        "ベスト設定 TOP3（検証期間スコア基準）:",
        "-" * 80,
    ]

    for i, (config, train_r, valid_r, score) in enumerate(
        results_sorted[:3]
    ):
        result_lines += [
            f"#{i+1}: min_sig={config.min_signals}, "
            f"ADX>{config.adx_threshold}, "
            f"RSI={config.rsi_oversold}/{config.rsi_overbought}, "
            f"SL/TP={config.sl_atr_mult}/{config.tp_atr_mult}, "
            f"CD={config.cooldown_bars}",
            f"     訓練: 勝率{train_r['win_rate']:.1f}%, "
            f"PF={train_r['pf']:.2f}, "
            f"利益¥{train_r['net_profit']:+,.0f}, "
            f"最大DD={train_r['max_dd']:.2f}%",
            f"     検証: 勝率{valid_r['win_rate']:.1f}%, "
            f"PF={valid_r['pf']:.2f}, "
            f"利益¥{valid_r['net_profit']:+,.0f}, "
            f"最大DD={valid_r['max_dd']:.2f}%",
            f"     スコア: {score:.4f}",
            "",
        ]

    for line in result_lines:
        print(line)

    # ファイルに保存
    all_lines = header_lines + result_lines
    output_path.write_text(
        "\n".join(all_lines), encoding="utf-8"
    )
    print(f"結果を保存: {output_path}")


if __name__ == "__main__":
    main()
