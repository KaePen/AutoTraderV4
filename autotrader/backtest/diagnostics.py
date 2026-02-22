"""バックテスト診断・デバッグモジュール

データ品質チェック、シグナル統計、特定時刻のシグナルデバッグ機能を提供。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

from autotrader.backtest.data_loader import DataLoader
from autotrader.backtest.simulator import SimulatorConfig, TradeSimulator
from autotrader.config import DEFAULT_TRADING_PARAMS
from autotrader.core.entities import Candle
from autotrader.core.enums import ExitReason, SignalType, Timeframe
from autotrader.decision.unified import UnifiedBotConfig, UnifiedTradeBot

logger = logging.getLogger(__name__)


class BacktestDiagnostics:
    """バックテスト診断ツール

    データ品質チェック、シグナル統計分析、
    シミュレーション比較を実行する。

    Attributes:
        data_dir: データディレクトリパス
        symbol: 通貨ペア
        market_data: 時間足別DataFrame辞書
    """

    def __init__(
        self,
        data_dir: str | Path = "data",
        symbol: str = "USDJPY",
    ) -> None:
        """初期化

        Args:
            data_dir: データ基底ディレクトリパス（通貨ペアサブディレクトリの親）
            symbol: 通貨ペア
        """
        # 通貨ペア別サブディレクトリに解決
        self.data_dir = Path(data_dir) / symbol
        self.symbol = symbol
        self.market_data: dict[str, pd.DataFrame] = {}

    def load_data(self) -> None:
        """市場データを読み込み"""
        tf_patterns = {
            "M1": f"{self.symbol}_M1_*.csv",
            "M5": f"{self.symbol}_M5_*.csv",
            "M15": f"{self.symbol}_M15_*.csv",
            "H1": f"{self.symbol}_H1_*.csv",
            "H4": f"{self.symbol}_H4_*.csv",
            "D1": f"{self.symbol}_D1_*.csv",
        }

        for tf, pattern in tf_patterns.items():
            files = list(self.data_dir.glob(pattern))
            if files:
                df = DataLoader.load_mt5_csv(files[0])
                self.market_data[tf] = df
                logger.info(f"  {tf}: {len(df):,}本")

        # 日足は別パターンの可能性
        if "D1" not in self.market_data:
            daily_files = list(
                self.data_dir.glob(f"{self.symbol}_Daily_*.csv")
            )
            if daily_files:
                df = DataLoader.load_mt5_csv(daily_files[0])
                self.market_data["D1"] = df
                logger.info(f"  D1: {len(df):,}本")

    def check_data_quality(self) -> dict[str, dict]:
        """データ品質チェック

        各時間足のデータについて、欠損・重複・範囲を検査。

        Returns:
            時間足別の品質レポート
        """
        results: dict[str, dict] = {}

        for tf, df in self.market_data.items():
            report: dict = {
                "records": len(df),
                "date_range": "",
                "missing_values": 0,
                "duplicates": 0,
                "gaps": 0,
            }

            if "time" in df.columns and len(df) > 0:
                report["date_range"] = (
                    f"{df['time'].min()} ~ {df['time'].max()}"
                )

            # 欠損値チェック
            for col in ["open", "high", "low", "close"]:
                if col in df.columns:
                    report["missing_values"] += (
                        df[col].isna().sum()
                    )

            # 重複チェック
            if "time" in df.columns:
                report["duplicates"] = df["time"].duplicated().sum()

            results[tf] = report

        return results

    def run_signal_statistics(
        self,
        year: int,
        base_tf: str = "M5",
    ) -> dict:
        """シグナル統計を実行

        指定年のシグナル生成統計を収集する。

        Args:
            year: 対象年
            base_tf: 基準時間足

        Returns:
            統計結果辞書
        """
        bot_config = UnifiedBotConfig(
            timeframes=["M5", "M15", "H1", "H4", "D1"],

            enable_position_sizing=False,
        )
        bot = UnifiedTradeBot(bot_config)
        bot.set_market_data(self.market_data)

        base_df = self.market_data.get(base_tf)
        if base_df is None:
            return {"error": f"{base_tf}データなし"}

        start_date = datetime(year, 1, 1)
        end_date = datetime(year + 1, 1, 1)
        period_df = base_df[
            (base_df["time"] >= start_date)
            & (base_df["time"] < end_date)
        ].reset_index(drop=True)

        buy_count = 0
        sell_count = 0
        hold_count = 0
        signals: list[dict] = []

        for idx in range(len(period_df)):
            row = period_df.iloc[idx]
            current_time = pd.Timestamp(row["time"])
            consolidated = bot.generate_signal(current_time)

            if consolidated.direction == SignalType.BUY:
                buy_count += 1
                signals.append({
                    "time": current_time,
                    "type": "BUY",
                    "confidence": consolidated.confidence,
                    "sl_pips": consolidated.sl_pips,
                    "tp_pips": consolidated.tp_pips,
                })
            elif consolidated.direction == SignalType.SELL:
                sell_count += 1
                signals.append({
                    "time": current_time,
                    "type": "SELL",
                    "confidence": consolidated.confidence,
                    "sl_pips": consolidated.sl_pips,
                    "tp_pips": consolidated.tp_pips,
                })
            else:
                hold_count += 1

            # 進捗表示
            if (idx + 1) % 5000 == 0:
                progress = (idx + 1) / len(period_df) * 100
                logger.info(f"  進捗: {progress:.1f}%")

        return {
            "year": year,
            "total_bars": len(period_df),
            "buy_signals": buy_count,
            "sell_signals": sell_count,
            "hold_count": hold_count,
            "signal_rate": (
                (buy_count + sell_count) / len(period_df) * 100
                if len(period_df) > 0
                else 0
            ),
            "samples": signals[:10],
        }

    def run_simulation_comparison(
        self,
        year: int,
        volumes: list[float] | None = None,
    ) -> list[dict]:
        """異なるボリュームでシミュレーション比較

        Args:
            year: 対象年
            volumes: テストするボリュームリスト

        Returns:
            各ボリュームの結果リスト
        """
        if volumes is None:
            volumes = [0.1, 0.5, 1.0]

        bot_config = UnifiedBotConfig(
            timeframes=["M5", "M15", "H1", "H4", "D1"],

            enable_position_sizing=False,
        )

        results = []
        initial_balance = 1_000_000.0

        for volume in volumes:
            bot = UnifiedTradeBot(bot_config)
            bot.set_market_data(self.market_data)

            sim_config = SimulatorConfig(
                initial_balance=initial_balance,
                spread_pips=DEFAULT_TRADING_PARAMS.spread_pips,
                pip_value=DEFAULT_TRADING_PARAMS.pip_value,
                max_positions=1,
                default_volume=volume,
            )
            simulator = TradeSimulator(config=sim_config)

            base_df = self.market_data.get("M5")
            if base_df is None:
                continue

            start_date = datetime(year, 1, 1)
            end_date = datetime(year + 1, 1, 1)
            period_df = base_df[
                (base_df["time"] >= start_date)
                & (base_df["time"] < end_date)
            ].reset_index(drop=True)

            last_candle = None
            for idx in range(len(period_df)):
                row = period_df.iloc[idx]
                current_time = pd.Timestamp(row["time"])

                candle = Candle(
                    symbol=self.symbol,
                    timeframe=Timeframe.M5,
                    time=row["time"],
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                )
                last_candle = candle

                signal = None
                if len(simulator.get_open_positions()) == 0:
                    consolidated = bot.generate_signal(current_time)
                    if consolidated.direction != SignalType.HOLD:
                        close = candle.close
                        sl_pips = consolidated.sl_pips
                        tp_pips = consolidated.tp_pips

                        if consolidated.direction == SignalType.BUY:
                            sl_price = close - sl_pips / 100
                            tp_price = close + tp_pips / 100
                        else:
                            sl_price = close + sl_pips / 100
                            tp_price = close - tp_pips / 100

                        from autotrader.core.entities import Signal

                        signal = Signal(
                            symbol=self.symbol,
                            timeframe=Timeframe.M5,
                            signal_type=consolidated.direction,
                            confidence=consolidated.confidence,
                            stop_loss=sl_price,
                            take_profit=tp_price,
                            reasoning=consolidated.rationale,
                        )

                simulator.process_candle(candle, signal)

            if last_candle:
                simulator.force_close_all(
                    last_candle, ExitReason.FORCE_CLOSE
                )

            trades = simulator.get_closed_trades()
            wins = [t for t in trades if (t.profit_loss or 0) > 0]
            losses = [t for t in trades if (t.profit_loss or 0) <= 0]

            results.append({
                "volume": volume,
                "trades": len(trades),
                "wins": len(wins),
                "losses": len(losses),
                "win_rate": (
                    len(wins) / len(trades) * 100
                    if trades
                    else 0
                ),
                "final_balance": simulator.state.balance,
                "net_profit": (
                    simulator.state.balance - initial_balance
                ),
                "return_pct": (
                    (simulator.state.balance - initial_balance)
                    / initial_balance
                    * 100
                ),
            })

        return results


class SignalDebugger:
    """シグナルデバッグツール

    特定時刻のシグナル生成過程を詳細に表示する。

    Attributes:
        market_data: 時間足別DataFrame辞書
        symbol: 通貨ペア
    """

    def __init__(
        self,
        market_data: dict[str, pd.DataFrame],
        symbol: str = "USDJPY",
    ) -> None:
        """初期化

        Args:
            market_data: 時間足別DataFrame辞書
            symbol: 通貨ペア
        """
        self.market_data = market_data
        self.symbol = symbol

    def debug_at_time(
        self,
        target_time: str | datetime,
        timeframes: list[str] | None = None,
    ) -> dict:
        """指定時刻のシグナル詳細を取得

        Args:
            target_time: デバッグ対象時刻
            timeframes: 評価対象時間足リスト

        Returns:
            デバッグ結果辞書
        """
        if isinstance(target_time, str):
            target_time = pd.Timestamp(target_time)

        if timeframes is None:
            timeframes = ["M5", "M15", "H1", "H4", "D1"]

        # ボット初期化
        bot_config = UnifiedBotConfig(
            timeframes=timeframes,

            enable_position_sizing=False,
        )
        bot = UnifiedTradeBot(bot_config)
        bot.set_market_data(self.market_data)

        # シグナル生成
        consolidated = bot.generate_signal(target_time)

        result: dict = {
            "time": str(target_time),
            "direction": consolidated.direction.value,
            "confidence": consolidated.confidence,
            "sl_pips": consolidated.sl_pips,
            "tp_pips": consolidated.tp_pips,
            "rationale": consolidated.rationale,
            "timeframe_details": {},
        }

        # 各TFの詳細
        for tf in timeframes:
            if tf not in bot.evaluators:
                continue

            evaluator = bot.evaluators[tf]
            tf_df = self.market_data.get(tf)
            if tf_df is None:
                continue

            # 対象時刻のデータ取得
            if "time" in tf_df.columns:
                mask = tf_df["time"] <= target_time
            elif isinstance(tf_df.index, pd.DatetimeIndex):
                mask = tf_df.index <= target_time
            else:
                continue

            if not mask.any():
                continue

            if "time" in tf_df.columns:
                row = tf_df[mask].iloc[-1]
            else:
                row = tf_df[mask].iloc[-1]

            # 指標値取得
            indicators: dict[str, float | None] = {}
            indicator_names = [
                "rsi_14", "rsi_7",
                "macd", "macd_signal", "macd_histogram",
                "sma_20", "sma_50", "sma_200",
                "ema_20", "ema_50",
                "adx", "plus_di", "minus_di",
                "stoch_k", "stoch_d",
                "atr_14", "atr_ma_20",
                "bb_upper", "bb_middle", "bb_lower",
            ]
            for ind in indicator_names:
                val = row.get(ind) if hasattr(row, "get") else None
                if val is not None and not pd.isna(val):
                    indicators[ind] = round(float(val), 4)
                else:
                    indicators[ind] = None

            # スコア計算
            strength = evaluator.calculator.calculate(row)
            buy_score, sell_score, reasons = (
                evaluator._calculate_score(row, None, strength)
            )

            result["timeframe_details"][tf] = {
                "close": float(
                    row["close"]
                    if "close" in row.index
                    else 0
                ),
                "indicators": indicators,
                "strength": {
                    "trend": strength.trend,
                    "momentum": strength.momentum,
                    "volatility": strength.volatility,
                    "direction": strength.direction,
                },
                "scores": {
                    "buy": buy_score,
                    "sell": sell_score,
                    "reasons": reasons,
                },
            }

        return result


def run_diagnostics(
    year: int,
    data_dir: str = "data",
    symbol: str = "USDJPY",
) -> None:
    """診断を実行して結果を表示

    Args:
        year: 対象年
        data_dir: データディレクトリ
        symbol: 通貨ペア
    """
    print("=" * 70)
    print("バックテスト診断")
    print("=" * 70)

    diag = BacktestDiagnostics(data_dir=data_dir, symbol=symbol)

    print("\nデータ読み込み中...")
    diag.load_data()

    # データ品質チェック
    print("\n--- データ品質チェック ---")
    quality = diag.check_data_quality()
    for tf, report in quality.items():
        print(
            f"  {tf}: {report['records']:,}本 "
            f"欠損={report['missing_values']} "
            f"重複={report['duplicates']}"
        )
        if report["date_range"]:
            print(f"    期間: {report['date_range']}")

    # シグナル統計
    print(f"\n--- {year}年シグナル統計 ---")
    stats = diag.run_signal_statistics(year)
    if "error" not in stats:
        print(f"  総バー数: {stats['total_bars']:,}")
        print(f"  買いシグナル: {stats['buy_signals']:,}")
        print(f"  売りシグナル: {stats['sell_signals']:,}")
        print(
            f"  シグナル発生率: "
            f"{stats['signal_rate']:.2f}%"
        )

        if stats["samples"]:
            print("\n  サンプルシグナル（最初の5件）:")
            for s in stats["samples"][:5]:
                print(
                    f"    {s['time']} {s['type']} "
                    f"信頼度={s['confidence']:.2f} "
                    f"SL={s['sl_pips']:.1f} "
                    f"TP={s['tp_pips']:.1f}"
                )

    # シミュレーション比較
    print(f"\n--- シミュレーション比較 ---")
    sim_results = diag.run_simulation_comparison(year)
    for r in sim_results:
        print(
            f"  Vol={r['volume']}: "
            f"取引{r['trades']}件 "
            f"勝率{r['win_rate']:.1f}% "
            f"損益¥{r['net_profit']:+,.0f} "
            f"({r['return_pct']:+.2f}%)"
        )


def run_debug_signal(
    target_time: str,
    data_dir: str = "data",
    symbol: str = "USDJPY",
) -> None:
    """特定時刻のシグナルをデバッグ表示

    Args:
        target_time: デバッグ対象時刻（YYYY-MM-DD HH:MM形式）
        data_dir: データディレクトリ
        symbol: 通貨ペア
    """
    print("=" * 70)
    print(f"シグナルデバッグ: {target_time}")
    print("=" * 70)

    # データ読み込み
    diag = BacktestDiagnostics(data_dir=data_dir, symbol=symbol)
    print("\nデータ読み込み中...")
    diag.load_data()

    # デバッグ実行
    debugger = SignalDebugger(
        diag.market_data, symbol=symbol
    )
    result = debugger.debug_at_time(target_time)

    # 結果表示
    print(f"\n判断: {result['direction']}")
    print(f"信頼度: {result['confidence']:.2f}")
    print(f"SL: {result['sl_pips']:.1f}pips")
    print(f"TP: {result['tp_pips']:.1f}pips")
    if result["rationale"]:
        print(f"根拠: {result['rationale']}")

    for tf, detail in result["timeframe_details"].items():
        print(f"\n--- {tf} ---")
        print(f"  close: {detail['close']:.5f}")

        # 主要指標
        inds = detail["indicators"]
        for key in ["rsi_14", "macd", "adx", "atr_14"]:
            val = inds.get(key)
            if val is not None:
                print(f"  {key}: {val}")

        # 強度
        s = detail["strength"]
        print(
            f"  strength: trend={s['trend']} "
            f"momentum={s['momentum']} "
            f"volatility={s['volatility']} "
            f"direction={s['direction']}"
        )

        # スコア
        sc = detail["scores"]
        print(f"  scores: buy={sc['buy']} sell={sc['sell']}")
        if sc["reasons"]:
            print(f"  reasons: {sc['reasons']}")
