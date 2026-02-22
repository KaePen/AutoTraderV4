#!/usr/bin/env python3
"""トレードフロー分析スクリプト

シグナル生成パイプラインの各段階でのフィルタリング状況を
分析し、勝率改善のボトルネックを特定する。

使用例:
    uv run python scripts/run_trade_flow_analysis.py --years 2023
    uv run python scripts/run_trade_flow_analysis.py --years 2023 --use-m1
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

# プロジェクトルートをパスに追加
try:
    project_root = Path(__file__).parent.parent
except NameError:
    project_root = Path("D:/Projects/AutoTraderV4")
sys.path.insert(0, str(project_root ))


def parse_args() -> argparse.Namespace:
    """引数パース"""
    parser = argparse.ArgumentParser(
        description="トレードフロー分析",
    )
    parser.add_argument(
        "--years",
        type=str,
        default="2023",
        help="分析対象年（例: 2023）",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default="data/csv",
        help="データディレクトリ",
    )
    parser.add_argument(
        "--symbol",
        default="USDJPY",
        help="シンボル",
    )
    parser.add_argument(
        "--use-m1",
        action="store_true",
        default=True,
        help="M1/M5データを使用（デフォルト: True）",
    )
    parser.add_argument(
        "--initial-balance",
        type=float,
        default=1_000_000.0,
        help="初期残高",
    )
    parser.add_argument(
        "--volume",
        type=float,
        default=1.0,
        help="取引ボリューム",
    )
    return parser.parse_args()


def parse_years(years_str: str) -> int:
    """年をパース"""
    if "-" in years_str:
        return int(years_str.split("-")[0])
    return int(years_str)


def main() -> None:
    """メイン関数"""
    args = parse_args()
    year = parse_years(args.years)

    import pandas as pd

    from autotrader.backtest.candle_arrays import CandleArrays
    from autotrader.backtest.runner import BacktestConfig, BacktestRunner
    from autotrader.backtest.simulator import SimulatorConfig, TradeSimulator
    from autotrader.backtest.trade_flow_analyzer import TradeFlowAnalyzer
    from autotrader.config import DEFAULT_TRADING_PARAMS
    from autotrader.core.entities import Signal
    from autotrader.core.enums import ExitReason, SignalType, Timeframe
    from autotrader.decision.unified import UnifiedBotConfig, UnifiedTradeBot

    data_dir = Path(args.data_dir)
    print(f"=== トレードフロー分析 {year}年 ===")

    # データ読み込み（BacktestRunnerを利用）
    print("データ読み込み中...")
    bt_config = BacktestConfig(
        symbol=args.symbol,
        timeframe="M15",
        initial_balance=args.initial_balance,
        volume=args.volume,
        max_positions=1,
    )
    runner = BacktestRunner(
        data_dir=str(data_dir),
        config=bt_config,
        verbose=False,
        log_to_file=False,
    )
    runner.load_data()
    market_data = runner._load_all_timeframes(include_m1=args.use_m1)

    for tf_name, df in market_data.items():
        if df is not None:
            print(f"  {tf_name}: {len(df):,}本")

    # ボット初期化
    bot_config = UnifiedBotConfig(
        timeframes=["M5", "M15", "H1", "H4", "D1"],
        enable_position_sizing=False,
    )
    bot = UnifiedTradeBot(bot_config)
    bot.set_market_data(market_data)

    # フロー分析器を設定
    analyzer = TradeFlowAnalyzer()
    bot.set_flow_analyzer(analyzer)

    # シミュレーター初期化
    sim_config = SimulatorConfig(
        initial_balance=args.initial_balance,
        spread_pips=DEFAULT_TRADING_PARAMS.spread_pips,
        pip_value=DEFAULT_TRADING_PARAMS.pip_value,
        max_positions=1,
        default_volume=args.volume,
    )
    simulator = TradeSimulator(config=sim_config)

    # 基準TF選択（M5優先）
    if "M5" in market_data and market_data["M5"] is not None:
        base_df = market_data["M5"]
        base_tf = Timeframe.M5
    elif "M15" in market_data and market_data["M15"] is not None:
        base_df = market_data["M15"]
        base_tf = Timeframe.M15
    else:
        print("基準データなし")
        return

    # 期間フィルタ
    start_date = datetime(year, 1, 1)
    end_date = datetime(year + 1, 1, 1)

    if "time" in base_df.columns:
        period_df = base_df[
            (base_df["time"] >= start_date)
            & (base_df["time"] < end_date)
        ].reset_index(drop=True)
    elif isinstance(base_df.index, pd.DatetimeIndex):
        period_df = base_df[
            (base_df.index >= pd.Timestamp(start_date))
            & (base_df.index < pd.Timestamp(end_date))
        ].reset_index()
    else:
        print("時刻カラムなし")
        return

    total_rows = len(period_df)
    print(f"期間データ: {total_rows:,}本 ({base_tf.value})")

    # トレード発生時のメタデータ保持
    trade_modes: list[str] = []
    trade_regimes: list[str] = []
    trade_consensus_scores: list[float] = []

    # メインループ
    print("バックテスト実行中（フロー分析付き）...")
    t0 = time.time()
    last_candle = None

    arrays = CandleArrays.from_dataframe(period_df)
    for idx in range(arrays.n_rows):
        candle = arrays.get_candle(idx, args.symbol, base_tf)
        last_candle = candle
        current_time = pd.Timestamp(arrays.get_time(idx))

        # シグナル生成（フロー分析器が内部で記録）
        consolidated = bot.generate_signal(current_time, candle)

        # Signalに変換
        signal = None
        if consolidated.direction != SignalType.HOLD:
            if consolidated.confidence >= 0.5:
                sl_price = None
                tp_price = None
                if consolidated.sl_pips > 0:
                    sl_pips = consolidated.sl_pips
                    tp_pips = consolidated.tp_pips
                    if consolidated.direction == SignalType.BUY:
                        sl_price = candle.close - sl_pips / 100
                        tp_price = candle.close + tp_pips / 100
                    else:
                        sl_price = candle.close + sl_pips / 100
                        tp_price = candle.close - tp_pips / 100

                signal = Signal(
                    symbol=args.symbol,
                    timeframe=base_tf,
                    signal_type=consolidated.direction,
                    confidence=min(consolidated.confidence, 1.0),
                    stop_loss=sl_price,
                    take_profit=tp_price,
                    reasoning=consolidated.rationale,
                )

        prev_trade_count = len(simulator.get_closed_trades())
        simulator.process_candle(candle, signal)

        # 新規トレード検出 → メタデータ保存
        closed_trades = simulator.get_closed_trades()
        if len(closed_trades) > prev_trade_count:
            # 直近のフローレコードからメタ取得
            records = analyzer.records
            # シグナル発生レコードを探す（末尾から）
            mode = ""
            regime = ""
            score = 0.0
            for rec in reversed(records):
                if rec.final_direction not in ("HOLD", ""):
                    mode = rec.mode
                    regime = rec.regime
                    score = rec.consensus_score
                    break
            trade_modes.append(mode)
            trade_regimes.append(regime)
            trade_consensus_scores.append(score)

        # 進捗表示
        if idx % 10000 == 0 and idx > 0:
            elapsed = time.time() - t0
            pct = idx / total_rows * 100
            trades_so_far = len(simulator.get_closed_trades())
            records_count = len(analyzer.records)
            print(
                f"  {pct:5.1f}% ({idx:,}/{total_rows:,}) "
                f"トレード: {trades_so_far} "
                f"レコード: {records_count:,} "
                f"経過: {elapsed:.0f}秒"
            )

    # 強制決済
    if last_candle:
        simulator.force_close_all(last_candle, ExitReason.FORCE_CLOSE)

    elapsed = time.time() - t0
    print(f"完了: {elapsed:.1f}秒")

    # トレード結果
    trades = simulator.get_closed_trades()
    print(f"総レコード: {len(analyzer.records):,}")
    print(f"総トレード: {len(trades)}")

    # レポート生成
    report = analyzer.generate_report(
        closed_trades=trades,
        trade_modes=trade_modes,
        trade_regimes=trade_regimes,
        trade_consensus_scores=trade_consensus_scores,
    )

    # 出力
    reports_dir = project_root / "reports"
    reports_dir.mkdir(exist_ok=True)
    output_path = reports_dir / f"trade_flow_analysis_{year}.txt"
    output_path.write_text(report, encoding="utf-8")
    print(f"\nレポート出力: {output_path}")

    # コンソールにも表示
    print()
    print(report)


if __name__ == "__main__":
    main()
