"""マルチTF組み合わせ網羅探索スクリプト

8つのベースTF（M1,M5,M15,M30,H1,H4,H8,D1）の全2^8-1=255通りの
組み合わせに対してバックテストを実行し、最適なTF構成を特定する。

Usage:
    python scripts/run_tf_combination_search.py \
        --symbol USDJPY --years 2023-2025 \
        --output reports/tf_combination_results.csv \
        --max-year-workers 3
"""

from __future__ import annotations

import argparse
import csv
import itertools
import logging
import sys
import time
from pathlib import Path

# プロジェクトルートをパスに追加
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# TF優先順（低TF→高TF）
TF_ORDER = ["M1", "M5", "M15", "M30", "H1", "H4", "H8", "D1"]
TF_RANK = {tf: i for i, tf in enumerate(TF_ORDER)}

# 結果CSV列
CSV_COLUMNS = [
    "combo_id",
    "timeframes",
    "tf_count",
    "regime_detection_tf",
    "htf_alignment_tfs",
    "trades",
    "win_rate",
    "non_loss_rate",
    "profit_factor",
    "net_profit",
    "max_drawdown",
    "sharpe_ratio",
    "annual_return",
    "elapsed_sec",
]

# ログ設定
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def resolve_regime_tf(combo: list[str]) -> str:
    """組み合わせからレジーム検出用TFを決定する。

    H1を優先し、なければ含まれる最も近い上位TFを選択。
    1TF単独の場合はそのTFを使用。

    Args:
        combo: TF組み合わせリスト

    Returns:
        str: レジーム検出用TF
    """
    if "H1" in combo:
        return "H1"
    # H1より上位のTFを探す（H4, H8, D1の順）
    for tf in ["H4", "H8", "D1"]:
        if tf in combo:
            return tf
    # 上位TFがない場合、含まれる最上位TFを返す
    sorted_combo = sorted(combo, key=lambda t: TF_RANK.get(t, 0))
    return sorted_combo[-1]


def resolve_htf_alignment(
    combo: list[str],
    regime_tf: str,
) -> list[str]:
    """組み合わせからHTF整合性チェック用TFリストを決定する。

    H4,D1を優先し、なければregime_tf以上のTFから最上位2つを選択。
    1TF単独の場合はそのTFのみ。

    Args:
        combo: TF組み合わせリスト
        regime_tf: レジーム検出用TF

    Returns:
        list[str]: HTF整合性チェック用TFリスト
    """
    # デフォルト候補
    candidates = []
    if "H4" in combo:
        candidates.append("H4")
    if "D1" in combo:
        candidates.append("D1")

    if candidates:
        return candidates

    # H4/D1がない場合、regime_tf以上の上位TFから最大2つ
    regime_rank = TF_RANK.get(regime_tf, 0)
    upper_tfs = sorted(
        [
            t
            for t in combo
            if TF_RANK.get(t, 0) >= regime_rank
        ],
        key=lambda t: TF_RANK.get(t, 0),
        reverse=True,
    )
    return upper_tfs[:2] if upper_tfs else [combo[-1]]


def generate_combo_id(combo: list[str]) -> str:
    """組み合わせからIDを生成する。

    Args:
        combo: TF組み合わせリスト

    Returns:
        str: コンビネーションID（例: "M1+M5+H1"）
    """
    return "+".join(combo)


def load_completed(output_path: Path) -> set[str]:
    """完了済みの組み合わせIDをCSVから読み込む。

    Args:
        output_path: 結果CSVパス

    Returns:
        set[str]: 完了済みcombo_idの集合
    """
    completed = set()
    if not output_path.exists():
        return completed

    with open(output_path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if "combo_id" in row:
                completed.add(row["combo_id"])

    return completed


def append_result(
    output_path: Path,
    row: dict[str, str | int | float],
    write_header: bool = False,
) -> None:
    """結果を1行CSVに追記する。

    Args:
        output_path: 出力CSVパス
        row: 結果辞書
        write_header: ヘッダー書き込みフラグ
    """
    mode = "w" if write_header else "a"
    with open(
        output_path, mode, encoding="utf-8", newline=""
    ) as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def run_single_combination(
    combo: list[str],
    symbol: str,
    start_year: int,
    end_year: int,
    data_dir: str,
    max_year_workers: int,
) -> dict[str, str | int | float]:
    """単一TF組み合わせのバックテストを実行する。

    Args:
        combo: TF組み合わせリスト
        symbol: 通貨ペア
        start_year: 開始年
        end_year: 終了年
        data_dir: データディレクトリ
        max_year_workers: 年単位並列ワーカー数

    Returns:
        dict: 結果辞書
    """
    from autotrader.backtest.runner import (
        BacktestConfig,
        BacktestRunner,
    )
    from autotrader.decision.unified import UnifiedBotConfig

    combo_id = generate_combo_id(combo)
    regime_tf = resolve_regime_tf(combo)
    htf_tfs = resolve_htf_alignment(combo, regime_tf)

    logger.info(
        "開始: %s (regime=%s, htf=%s)",
        combo_id,
        regime_tf,
        htf_tfs,
    )

    t0 = time.time()

    config = BacktestConfig.from_preset(symbol)
    runner = BacktestRunner(
        data_dir=data_dir,
        config=config,
        verbose=False,
        log_to_file=False,
    )

    bot_config = UnifiedBotConfig(
        timeframes=combo,
        regime_detection_tf=regime_tf,
        htf_alignment_tfs=htf_tfs,
    )

    result = runner.run_unified(
        start_year=start_year,
        end_year=end_year,
        config=bot_config,
        max_year_workers=max_year_workers,
    )

    elapsed = time.time() - t0

    return {
        "combo_id": combo_id,
        "timeframes": ",".join(combo),
        "tf_count": len(combo),
        "regime_detection_tf": regime_tf,
        "htf_alignment_tfs": ",".join(htf_tfs),
        "trades": result.trades,
        "win_rate": round(result.win_rate, 2),
        "non_loss_rate": round(result.non_loss_rate, 2),
        "profit_factor": round(result.profit_factor, 2),
        "net_profit": round(result.net_profit, 0),
        "max_drawdown": round(result.max_drawdown, 2),
        "sharpe_ratio": round(result.sharpe_ratio, 2),
        "annual_return": round(result.annual_return, 2),
        "elapsed_sec": round(elapsed, 1),
    }


def generate_all_combinations() -> list[list[str]]:
    """全255通りのTF組み合わせを生成する。

    Returns:
        list[list[str]]: TF組み合わせリスト（TF数昇順）
    """
    combos = []
    for size in range(1, len(TF_ORDER) + 1):
        for combo in itertools.combinations(TF_ORDER, size):
            combos.append(list(combo))
    return combos


def parse_args() -> argparse.Namespace:
    """CLI引数をパースする。

    Returns:
        argparse.Namespace: パース結果
    """
    parser = argparse.ArgumentParser(
        description="マルチTF組み合わせ網羅探索"
    )
    parser.add_argument(
        "--symbol",
        default="USDJPY",
        help="通貨ペア（デフォルト: USDJPY）",
    )
    parser.add_argument(
        "--years",
        default="2023-2025",
        help="対象年範囲（例: 2023-2025）",
    )
    parser.add_argument(
        "--output",
        default="reports/tf_combination_results.csv",
        help="出力CSVパス",
    )
    parser.add_argument(
        "--data-dir",
        default="data",
        help="データディレクトリ",
    )
    parser.add_argument(
        "--max-year-workers",
        type=int,
        default=3,
        help="年単位並列ワーカー数（デフォルト: 3）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="実行せずに組み合わせ一覧を表示",
    )
    parser.add_argument(
        "--filter-min-tf",
        type=int,
        default=1,
        help="最小TF数フィルター（デフォルト: 1）",
    )
    parser.add_argument(
        "--filter-max-tf",
        type=int,
        default=8,
        help="最大TF数フィルター（デフォルト: 8）",
    )
    return parser.parse_args()


def main() -> None:
    """メインエントリーポイント"""
    args = parse_args()

    # 年パース
    parts = args.years.split("-")
    start_year = int(parts[0])
    end_year = int(parts[1]) if len(parts) > 1 else start_year

    # 出力ディレクトリ作成
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 全組み合わせ生成
    all_combos = generate_all_combinations()

    # TF数フィルター
    all_combos = [
        c
        for c in all_combos
        if args.filter_min_tf <= len(c) <= args.filter_max_tf
    ]

    logger.info(
        "対象: %s %d-%d, %d組み合わせ",
        args.symbol,
        start_year,
        end_year,
        len(all_combos),
    )

    if args.dry_run:
        for i, combo in enumerate(all_combos, 1):
            cid = generate_combo_id(combo)
            rtf = resolve_regime_tf(combo)
            htf = resolve_htf_alignment(combo, rtf)
            print(
                f"{i:3d}. {cid:<40s} "
                f"regime={rtf} htf={htf}"
            )
        print(f"\n合計: {len(all_combos)}組み合わせ")
        return

    # 完了済みチェック（中断再開対応）
    completed = load_completed(output_path)
    remaining = [
        c
        for c in all_combos
        if generate_combo_id(c) not in completed
    ]

    if completed:
        logger.info(
            "既完了: %d件, 残り: %d件",
            len(completed),
            len(remaining),
        )

    if not remaining:
        logger.info("全組み合わせ完了済み")
        return

    # CSVヘッダー（新規ファイルの場合のみ）
    write_header = not output_path.exists()

    total = len(remaining)
    t_start = time.time()

    for idx, combo in enumerate(remaining, 1):
        combo_id = generate_combo_id(combo)
        logger.info(
            "=== [%d/%d] %s ===",
            idx,
            total,
            combo_id,
        )

        try:
            row = run_single_combination(
                combo=combo,
                symbol=args.symbol,
                start_year=start_year,
                end_year=end_year,
                data_dir=args.data_dir,
                max_year_workers=args.max_year_workers,
            )
            append_result(output_path, row, write_header)
            write_header = False

            # 進捗表示
            elapsed_total = time.time() - t_start
            avg_per_combo = elapsed_total / idx
            eta = avg_per_combo * (total - idx)
            logger.info(
                "完了: %s | 取引=%d 勝率=%.1f%% PF=%.2f "
                "利益=¥%s DD=%.2f%% | "
                "%.1fs (ETA: %.0f分)",
                combo_id,
                row["trades"],
                row["win_rate"],
                row["profit_factor"],
                f"{row['net_profit']:,.0f}",
                row["max_drawdown"],
                row["elapsed_sec"],
                eta / 60,
            )

        except KeyboardInterrupt:
            logger.info(
                "\n中断（%d/%d完了）。再実行で続行可能。",
                idx - 1,
                total,
            )
            sys.exit(0)

        except Exception:
            logger.exception(
                "エラー: %s（スキップ）", combo_id
            )

    # 完了サマリー
    elapsed_total = time.time() - t_start
    logger.info(
        "\n全%d組み合わせ完了（%.1f分）",
        total,
        elapsed_total / 60,
    )
    logger.info("結果: %s", output_path)


if __name__ == "__main__":
    main()
