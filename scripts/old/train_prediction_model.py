"""予測モデル Walk-Forward 訓練・検証スクリプト

H4データでLightGBM方向分類器を訓練し、OOS精度を検証する。

Walk-Forward方式:
  IS期間(12ヶ月) → 訓練 → OOS期間(3ヶ月) → 評価
  ウィンドウをスライドして複数回検証

使い方:
  uv run python scripts/train_prediction_model.py
  uv run python scripts/train_prediction_model.py --symbol USDJPY --years 2015-2024
  uv run python scripts/train_prediction_model.py --direction-tf H4 --horizon 6
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autotrader.backtest.data_loader import DataLoader
from autotrader.calculator.precompute import PrecomputeEngine
from autotrader.config.paths import get_data_dir
from autotrader.core.enums import Timeframe
from autotrader.prediction.config import PredictionConfig
from autotrader.prediction.direction_predictor import (
    DirectionPredictor,
    evaluate_oos,
)
from autotrader.prediction.feature_builder import FeatureBuilder

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _find_data_file(
    data_dir: str, symbol: str, timeframe: Timeframe
) -> Path:
    """データファイルを検索（Parquet優先）

    ディレクトリ構造:
      {data_dir}/{symbol}/chart/cache/{symbol}_{tf}.parquet  (優先)
      {data_dir}/{symbol}/chart/csv/{symbol}_{tf}_*.csv
      {data_dir}/{symbol}_{tf}*.parquet  (フラット構造)
    """
    base = Path(data_dir)
    tf_name = timeframe.value

    # 1. {symbol}/chart/cache/ Parquet
    pq = base / symbol / "chart" / "cache" / f"{symbol}_{tf_name}.parquet"
    if pq.exists():
        return pq

    # 2. {symbol}/chart/csv/ CSV
    csv_dir = base / symbol / "chart" / "csv"
    if csv_dir.exists():
        csvs = sorted(csv_dir.glob(f"{symbol}_{tf_name}_*.csv"))
        if csvs:
            return csvs[0]

    # 3. フラット構造
    flat_pq = list(base.glob(f"{symbol}_{tf_name}*.parquet"))
    if flat_pq:
        return flat_pq[0]
    flat_csv = list(base.glob(f"{symbol}_{tf_name}*.csv"))
    if flat_csv:
        return flat_csv[0]

    raise FileNotFoundError(
        f"データファイル未検出: {symbol}_{tf_name} in {data_dir}"
    )


def load_and_precompute(
    symbol: str,
    timeframe: Timeframe,
    start_year: int,
    end_year: int,
    data_dir: str,
) -> pd.DataFrame:
    """データ読み込み + テクニカル指標事前計算

    Args:
        symbol: 通貨ペア
        timeframe: 時間足
        start_year: 開始年
        end_year: 終了年（含む）
        data_dir: データディレクトリ

    Returns:
        pd.DataFrame: テクニカル指標+特徴量付きDataFrame
    """
    file_path = _find_data_file(data_dir, symbol, timeframe)
    start = datetime(start_year, 1, 1)
    end = datetime(end_year + 1, 1, 1)

    logger.info(
        f"データ読み込み: {symbol} {timeframe.value} "
        f"{start_year}-{end_year} from {file_path.name}"
    )

    if file_path.suffix == ".parquet":
        df = pd.read_parquet(file_path)
    else:
        df = DataLoader.load_mt5_csv(file_path)

    # 日時フィルタリング
    if "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"])
        df = df[(df["time"] >= start) & (df["time"] < end)]
        df = df.set_index("time")
    elif df.index.name == "time" or isinstance(
        df.index, pd.DatetimeIndex
    ):
        df = df[(df.index >= start) & (df.index < end)]

    logger.info(f"読み込み完了: {len(df)}行")

    # 事前計算
    engine = PrecomputeEngine()
    df = engine.precompute(df, symbol, timeframe, use_cache=True)
    logger.info(f"事前計算完了: {len(df.columns)}列")

    return df


def run_walk_forward(
    df: pd.DataFrame,
    config: PredictionConfig,
    is_months: int = 12,
    oos_months: int = 3,
    step_months: int = 3,
) -> list[dict]:
    """Walk-Forward検証を実行

    Args:
        df: テクニカル指標+特徴量付きDataFrame
        config: 予測設定
        is_months: In-Sample期間（月）
        oos_months: Out-of-Sample期間（月）
        step_months: ウィンドウスライド幅（月）

    Returns:
        list[dict]: 各ウィンドウのOOS評価結果
    """
    feature_builder = FeatureBuilder()
    predictor = DirectionPredictor(config)

    # 特徴量とラベルを構築
    logger.info("特徴量構築中...")
    features_df = feature_builder.build(df)
    labels = predictor.build_labels(df)

    # 有効行のみ（NaN除去後）
    valid_mask = (
        ~features_df.isna().any(axis=1)
        & (labels >= 0)
    )
    logger.info(
        f"有効サンプル: {valid_mask.sum()} / {len(df)} "
        f"({valid_mask.sum() / len(df) * 100:.1f}%)"
    )

    # 時間ベースのウィンドウ分割
    if hasattr(df.index, 'to_series'):
        times = df.index.to_series()
    else:
        times = pd.Series(df.index)

    min_date = times.min()
    max_date = times.max()

    results = []
    window_start = min_date
    window_idx = 0

    while True:
        is_end = window_start + pd.DateOffset(months=is_months)
        oos_end = is_end + pd.DateOffset(months=oos_months)

        if oos_end > max_date:
            break

        # IS / OOS 分割
        is_mask = (times >= window_start) & (times < is_end)
        oos_mask = (times >= is_end) & (times < oos_end)

        is_valid = is_mask & valid_mask
        oos_valid = oos_mask & valid_mask

        n_is = is_valid.sum()
        n_oos = oos_valid.sum()

        if n_is < config.min_training_samples:
            logger.warning(
                f"Window {window_idx}: IS samples {n_is} < "
                f"{config.min_training_samples}, skip"
            )
            window_start += pd.DateOffset(months=step_months)
            window_idx += 1
            continue

        if n_oos < 50:
            logger.warning(
                f"Window {window_idx}: OOS samples {n_oos} < 50, skip"
            )
            window_start += pd.DateOffset(months=step_months)
            window_idx += 1
            continue

        X_is = features_df[is_valid].values
        y_is = labels[is_valid].values
        X_oos = features_df[oos_valid].values
        y_oos = labels[oos_valid].values

        # 訓練
        version = (
            f"wf{window_idx}_"
            f"{window_start.strftime('%Y%m')}-"
            f"{is_end.strftime('%Y%m')}"
        )
        logger.info(
            f"\n--- Window {window_idx} ---\n"
            f"  IS:  {window_start.strftime('%Y-%m')} → "
            f"{is_end.strftime('%Y-%m')} ({n_is} samples)\n"
            f"  OOS: {is_end.strftime('%Y-%m')} → "
            f"{oos_end.strftime('%Y-%m')} ({n_oos} samples)"
        )

        train_metrics = predictor.train(
            X_is, y_is,
            feature_names=feature_builder.feature_names,
            version=version,
        )
        logger.info(
            f"  IS accuracy: {train_metrics.accuracy:.3f}"
        )

        # OOS評価
        oos_metrics = evaluate_oos(predictor, X_oos, y_oos)
        logger.info(
            f"  OOS accuracy: {oos_metrics['accuracy']:.3f} "
            f"(random baseline: {oos_metrics['random_baseline']:.3f}, "
            f"edge: {oos_metrics['edge_over_random']:+.3f})"
        )
        if "per_class_accuracy" in oos_metrics:
            for cls, acc in oos_metrics["per_class_accuracy"].items():
                logger.info(f"    {cls}: {acc:.3f}")

        result = {
            "window": window_idx,
            "is_start": window_start.strftime("%Y-%m"),
            "is_end": is_end.strftime("%Y-%m"),
            "oos_start": is_end.strftime("%Y-%m"),
            "oos_end": oos_end.strftime("%Y-%m"),
            "n_is": int(n_is),
            "n_oos": int(n_oos),
            "is_accuracy": train_metrics.accuracy,
            "oos_accuracy": oos_metrics["accuracy"],
            "random_baseline": oos_metrics["random_baseline"],
            "edge_over_random": oos_metrics["edge_over_random"],
            "per_class_accuracy": oos_metrics.get("per_class_accuracy", {}),
            "label_distribution": train_metrics.label_distribution,
            "top_features": [
                (n, v) for n, v in train_metrics.feature_importance[:10]
            ],
        }
        results.append(result)

        window_start += pd.DateOffset(months=step_months)
        window_idx += 1

    return results


def print_summary(results: list[dict]) -> None:
    """Walk-Forward結果サマリーを出力"""
    if not results:
        logger.warning("結果なし")
        return

    accuracies = [r["oos_accuracy"] for r in results]
    edges = [r["edge_over_random"] for r in results]
    baselines = [r["random_baseline"] for r in results]

    print("\n" + "=" * 70)
    print("WALK-FORWARD VALIDATION SUMMARY")
    print("=" * 70)

    for r in results:
        edge_marker = "✓" if r["edge_over_random"] > 0 else "✗"
        acc_marker = "✓" if r["oos_accuracy"] > 0.55 else "✗"
        print(
            f"  Window {r['window']:2d}: "
            f"IS {r['is_start']}→{r['is_end']} | "
            f"OOS {r['oos_start']}→{r['oos_end']} | "
            f"IS={r['is_accuracy']:.3f} "
            f"OOS={r['oos_accuracy']:.3f} "
            f"baseline={r['random_baseline']:.3f} "
            f"edge={r['edge_over_random']:+.3f} "
            f"{edge_marker}{acc_marker}"
        )

    print("-" * 70)
    print(f"  OOS Accuracy:  mean={np.mean(accuracies):.3f} "
          f"std={np.std(accuracies):.3f} "
          f"min={np.min(accuracies):.3f} max={np.max(accuracies):.3f}")
    print(f"  Random Base:   mean={np.mean(baselines):.3f}")
    print(f"  Edge over Rand: mean={np.mean(edges):+.3f} "
          f"std={np.std(edges):.3f}")
    print(f"  Windows: {len(results)} total, "
          f"{sum(1 for e in edges if e > 0)} positive edge, "
          f"{sum(1 for a in accuracies if a > 0.55)} above 55%")

    # ゲート判定
    mean_acc = np.mean(accuracies)
    mean_edge = np.mean(edges)
    pass_acc = mean_acc > 0.55
    pass_edge = mean_edge > 0
    passed = pass_acc and pass_edge

    print("\n" + "=" * 70)
    print("GATE CHECK")
    print(f"  OOS accuracy > 55%:       {'PASS' if pass_acc else 'FAIL'} "
          f"({mean_acc:.3f})")
    print(f"  Edge over random > 0:     {'PASS' if pass_edge else 'FAIL'} "
          f"({mean_edge:+.3f})")
    print(f"  OVERALL:                  {'✓ PASS' if passed else '✗ FAIL'}")
    print("=" * 70)

    # 特徴量重要度集約
    feat_scores: dict[str, list[float]] = {}
    for r in results:
        for name, score in r["top_features"]:
            feat_scores.setdefault(name, []).append(score)

    if feat_scores:
        print("\nTOP FEATURES (mean importance across windows):")
        top = sorted(
            feat_scores.items(),
            key=lambda x: np.mean(x[1]),
            reverse=True,
        )[:15]
        for name, scores in top:
            print(f"  {name:30s} {np.mean(scores):8.1f} "
                  f"(appeared in {len(scores)}/{len(results)} windows)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="予測モデル Walk-Forward 訓練・検証"
    )
    parser.add_argument(
        "--symbol", default="USDJPY", help="通貨ペア"
    )
    parser.add_argument(
        "--years", default="2015-2024",
        help="年範囲 (YYYY-YYYY)"
    )
    parser.add_argument(
        "--direction-tf", default="H4",
        help="方向予測の時間足 (H1/H4/D1)"
    )
    parser.add_argument(
        "--horizon", type=int, default=6,
        help="予測ホライズン（足数）"
    )
    parser.add_argument(
        "--is-months", type=int, default=12,
        help="IS期間（月）"
    )
    parser.add_argument(
        "--oos-months", type=int, default=3,
        help="OOS期間（月）"
    )
    parser.add_argument(
        "--step-months", type=int, default=3,
        help="ウィンドウスライド幅（月）"
    )
    parser.add_argument(
        "--threshold", type=float, default=0.6,
        help="方向判定の確率閾値"
    )
    parser.add_argument(
        "--atr-mult", type=float, default=1.0,
        help="ラベル構築のATR倍率"
    )
    parser.add_argument(
        "--save-model", action="store_true",
        help="最後のウィンドウのモデルを保存"
    )
    parser.add_argument(
        "--save-results", type=str, default=None,
        help="結果をJSONファイルに保存"
    )

    args = parser.parse_args()

    # 年範囲パース
    if "-" in args.years:
        start_year, end_year = map(int, args.years.split("-"))
    else:
        start_year = end_year = int(args.years)

    # 設定
    config = PredictionConfig(
        direction_tf=args.direction_tf,
        direction_horizon_bars=args.horizon,
        direction_threshold=args.threshold,
        direction_atr_label_mult=args.atr_mult,
    )

    tf = Timeframe(args.direction_tf)
    data_dir = get_data_dir()

    logger.info(
        f"設定: {args.symbol} {tf.value} "
        f"{start_year}-{end_year}, "
        f"horizon={args.horizon}bars, "
        f"threshold={args.threshold}, "
        f"atr_mult={args.atr_mult}"
    )

    # データ読み込み + 事前計算
    df = load_and_precompute(
        args.symbol, tf, start_year, end_year, data_dir
    )

    # Walk-Forward検証
    results = run_walk_forward(
        df, config,
        is_months=args.is_months,
        oos_months=args.oos_months,
        step_months=args.step_months,
    )

    # サマリー出力
    print_summary(results)

    # 結果保存
    if args.save_results:
        out_path = Path(args.save_results)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(
                {
                    "symbol": args.symbol,
                    "timeframe": args.direction_tf,
                    "horizon_bars": args.horizon,
                    "threshold": args.threshold,
                    "atr_mult": args.atr_mult,
                    "is_months": args.is_months,
                    "oos_months": args.oos_months,
                    "windows": results,
                },
                f,
                indent=2,
                ensure_ascii=False,
            )
        logger.info(f"結果保存: {out_path}")

    # モデル保存
    if args.save_model and results:
        predictor = DirectionPredictor(config)
        feature_builder = FeatureBuilder()
        features_df = feature_builder.build(df)
        labels = predictor.build_labels(df)

        valid_mask = ~features_df.isna().any(axis=1) & (labels >= 0)
        predictor.train(
            features_df[valid_mask].values,
            labels[valid_mask].values,
            feature_names=feature_builder.feature_names,
            version=f"full_{args.symbol}_{start_year}-{end_year}",
        )
        model_dir = Path(config.model_dir) / args.symbol
        predictor.save(model_dir)
        logger.info(f"モデル保存完了: {model_dir}")


if __name__ == "__main__":
    main()
