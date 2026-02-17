"""高速バックテストエンジン

並列処理による高速バックテスト実行モジュール。
指標計算とトレードシミュレーションを並列化して高速化。
Parquetファイルパス渡しでプロセス間データ転送を最適化。
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

from autotrader.backtest.metrics import MetricsCalculator
from autotrader.backtest.simulator import (
    SimulatorConfig,
    SimulatorState,
    TradeSimulator,
)
from autotrader.core.entities import Candle, Signal, Trade
from autotrader.core.enums import ExitReason, SignalType, Timeframe

if TYPE_CHECKING:
    from autotrader.decision.unified import UnifiedTradeBot

logger = logging.getLogger(__name__)

# ウォームアップバー数（シグナル生成器の状態安定化用）
WARMUP_BARS = 50


@dataclass
class FastBacktestConfig:
    """高速バックテスト設定

    Attributes:
        symbol: 通貨ペア
        start_date: 開始日
        end_date: 終了日
        chunk_months: チャンクサイズ（月単位）
        max_workers: 最大ワーカー数（None=CPU数）
        warmup_bars: ウォームアップバー数
        initial_balance: 初期残高
        default_volume: デフォルトロット数
        base_timeframe: 基準タイムフレーム
        confidence_threshold: シグナル信頼度閾値
    """

    symbol: str = "USDJPY"
    start_date: datetime = field(
        default_factory=lambda: datetime(2020, 1, 1)
    )
    end_date: datetime = field(
        default_factory=lambda: datetime(2024, 1, 1)
    )
    chunk_months: int = 3
    max_workers: int | None = None
    warmup_bars: int = WARMUP_BARS
    initial_balance: float = 1_000_000.0
    default_volume: float = 0.1
    base_timeframe: Timeframe = Timeframe.M15
    confidence_threshold: float = 0.3


@dataclass
class ChunkResult:
    """チャンク処理結果

    Attributes:
        chunk_id: チャンクID
        start_date: チャンク担当開始日
        end_date: チャンク担当終了日
        trades: 決済済みトレードリスト
        processing_time: 処理時間（秒）
        error: エラーメッセージ（あれば）
    """

    chunk_id: int
    start_date: datetime
    end_date: datetime
    trades: list[dict[str, Any]] = field(default_factory=list)
    processing_time: float = 0.0
    error: str | None = None


@dataclass
class FastBacktestResult:
    """高速バックテスト最終結果

    Attributes:
        total_trades: 総トレード数
        winning_trades: 勝ちトレード数
        losing_trades: 負けトレード数
        win_rate: 勝率
        total_pnl: 総損益
        profit_factor: プロフィットファクター
        max_drawdown_pct: 最大ドローダウン（%）
        return_pct: 総収益率（%）
        trades: 全トレードリスト（時系列順）
        chunk_results: チャンク別結果
        total_processing_time: 総処理時間（秒）
    """

    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    profit_factor: float = 0.0
    max_drawdown_pct: float = 0.0
    return_pct: float = 0.0
    trades: list[dict[str, Any]] = field(default_factory=list)
    chunk_results: list[ChunkResult] = field(default_factory=list)
    total_processing_time: float = 0.0


def _process_chunk_worker(
    chunk_id: int,
    chunk_start: datetime,
    chunk_end: datetime,
    base_parquet_path: str,
    tf_parquet_paths: dict[str, str],
    sim_config_dict: dict[str, Any],
    config_dict: dict[str, Any],
    warmup_bars: int,
) -> dict[str, Any]:
    """チャンク処理ワーカー関数

    ProcessPoolExecutorで実行される。
    Parquetファイルからデータを読み込み、
    担当期間内にエントリーしたトレードを処理する。

    Args:
        chunk_id: チャンクID
        chunk_start: 担当期間開始
        chunk_end: 担当期間終了
        base_parquet_path: 基準データParquetパス
        tf_parquet_paths: TF別ParquetパスのDict
        sim_config_dict: シミュレーター設定
        config_dict: 全体設定
        warmup_bars: ウォームアップバー数

    Returns:
        ChunkResult相当のdict
    """
    import time

    from autotrader.backtest.candle_arrays import CandleArrays
    from autotrader.backtest.simulator import (
        SimulatorConfig,
        TradeSimulator,
    )
    from autotrader.core.entities import Candle, Signal
    from autotrader.core.enums import ExitReason, SignalType, Timeframe
    from autotrader.decision.unified import UnifiedTradeBot

    start_time = time.time()

    try:
        # Parquetからデータ読み込み（高速）
        df = pd.read_parquet(base_parquet_path)

        # 市場データを読み込み
        market_data: dict[str, pd.DataFrame] = {}
        for tf_str, pq_path in tf_parquet_paths.items():
            tf_df = pd.read_parquet(pq_path)
            if "time" in tf_df.columns:
                tf_df = tf_df.set_index("time")
            market_data[tf_str] = tf_df

        # 設定を復元
        symbol = config_dict["symbol"]
        base_tf = Timeframe(config_dict["base_timeframe"])
        confidence_threshold = config_dict[
            "confidence_threshold"
        ]

        sim_config = SimulatorConfig(
            initial_balance=sim_config_dict["initial_balance"],
            default_volume=sim_config_dict["default_volume"],
            max_positions=sim_config_dict.get(
                "max_positions", 1
            ),
        )

        # ボットをデフォルト設定で初期化
        bot = UnifiedTradeBot()
        bot.set_market_data(market_data)

        # シミュレーター初期化
        simulator = TradeSimulator(config=sim_config)

        trades_in_chunk: list[dict[str, Any]] = []
        has_open_position = False

        # numpy配列ベースのループ
        arrays = CandleArrays.from_dataframe(df)
        for i in range(arrays.n_rows):
            candle_time = arrays.get_time(i)
            candle = arrays.get_candle(i, symbol, base_tf)

            # ウォームアップ期間: シグナル生成のみ
            if candle_time < chunk_start:
                current_time = pd.Timestamp(candle_time)
                bot.generate_signal(current_time, candle)
                continue

            # シグナル生成
            current_time = pd.Timestamp(candle_time)
            consolidated = bot.generate_signal(
                current_time, candle
            )

            # Signalオブジェクト変換
            signal = None
            if consolidated.direction != SignalType.HOLD:
                if (
                    consolidated.confidence
                    >= confidence_threshold
                ):
                    sl_price = None
                    tp_price = None
                    if consolidated.sl_pips > 0:
                        if (
                            consolidated.direction
                            == SignalType.BUY
                        ):
                            sl_price = (
                                candle.close
                                - consolidated.sl_pips / 100
                            )
                            tp_price = (
                                candle.close
                                + consolidated.tp_pips / 100
                            )
                        else:
                            sl_price = (
                                candle.close
                                + consolidated.sl_pips / 100
                            )
                            tp_price = (
                                candle.close
                                - consolidated.tp_pips / 100
                            )

                    signal = Signal(
                        symbol=symbol,
                        timeframe=base_tf,
                        signal_type=consolidated.direction,
                        confidence=min(
                            consolidated.confidence, 1.0
                        ),
                        stop_loss=sl_price,
                        take_profit=tp_price,
                        reasoning=consolidated.rationale,
                    )

            prev_trade_count = len(
                simulator.get_closed_trades()
            )
            prev_position_count = len(
                simulator.get_open_positions()
            )

            # exit tail: 既存ポジションのSL/TPのみ処理
            if candle_time >= chunk_end:
                if not has_open_position:
                    break
                simulator.process_candle(candle, None)

                # クローズ検出
                current_trades = simulator.get_closed_trades()
                if len(current_trades) > prev_trade_count:
                    trade = current_trades[-1]
                    trades_in_chunk.append(
                        _trade_to_dict(trade)
                    )
                    has_open_position = False
                continue

            # エントリーウィンドウ: 通常処理
            if not has_open_position:
                if signal is not None:
                    simulator.process_candle(candle, signal)
                    positions = simulator.get_open_positions()
                    if len(positions) > prev_position_count:
                        has_open_position = True
            else:
                # SL/TPのみでクローズ
                simulator.process_candle(candle, None)

                # クローズ検出
                current_trades = simulator.get_closed_trades()
                if len(current_trades) > prev_trade_count:
                    trade = current_trades[-1]
                    trades_in_chunk.append(
                        _trade_to_dict(trade)
                    )
                    has_open_position = False

                # 新規ポジション検出
                positions = simulator.get_open_positions()
                if (
                    len(positions) > 0
                    and not has_open_position
                ):
                    has_open_position = True

        processing_time = time.time() - start_time

        return {
            "chunk_id": chunk_id,
            "start_date": chunk_start.isoformat(),
            "end_date": chunk_end.isoformat(),
            "trades": trades_in_chunk,
            "processing_time": processing_time,
            "error": None,
        }

    except Exception as e:
        import traceback

        return {
            "chunk_id": chunk_id,
            "start_date": chunk_start.isoformat(),
            "end_date": chunk_end.isoformat(),
            "trades": [],
            "processing_time": time.time() - start_time,
            "error": (
                f"{type(e).__name__}: {e}\n"
                f"{traceback.format_exc()}"
            ),
        }


def _trade_to_dict(trade: Trade) -> dict[str, Any]:
    """Tradeオブジェクトをdictへ変換

    Args:
        trade: トレードオブジェクト

    Returns:
        dict: トレード辞書
    """
    return {
        "trade_id": trade.trade_id,
        "symbol": trade.symbol,
        "direction": trade.signal_type.value,
        "entry_price": trade.entry_price,
        "exit_price": trade.exit_price,
        "volume": trade.volume,
        "profit_loss": trade.profit_loss,
        "opened_at": (
            trade.opened_at.isoformat()
            if trade.opened_at
            else None
        ),
        "closed_at": (
            trade.closed_at.isoformat()
            if trade.closed_at
            else None
        ),
        "exit_reason": (
            trade.exit_reason.value
            if trade.exit_reason
            else None
        ),
    }


class FastBacktestEngine:
    """高速バックテストエンジン

    並列処理を使用して高速にバックテストを実行する。
    Parquetファイルパス渡しでプロセス間データ転送を最適化。
    """

    def __init__(self, config: FastBacktestConfig):
        """初期化

        Args:
            config: 高速バックテスト設定
        """
        self.config = config
        self._max_workers = (
            config.max_workers or os.cpu_count() or 4
        )

        logger.info(
            f"FastBacktestEngine初期化: "
            f"workers={self._max_workers}, "
            f"chunk_months={config.chunk_months}"
        )

    def run(
        self,
        df: pd.DataFrame,
        market_data: dict[str, pd.DataFrame],
    ) -> FastBacktestResult:
        """バックテスト実行

        Args:
            df: 基準タイムフレームOHLCVデータ
            market_data: 時間足別市場データ（指標計算済み）

        Returns:
            FastBacktestResult: バックテスト結果
        """
        import time

        total_start = time.time()

        # 期間フィルタリング
        df = df[
            (df["time"] >= self.config.start_date)
            & (df["time"] < self.config.end_date)
        ].copy()

        if df.empty:
            logger.warning("対象期間にデータがありません")
            return FastBacktestResult()

        # チャンク分割
        chunks = self._create_chunks()
        logger.info(f"チャンク数: {len(chunks)}")

        # シミュレーター設定
        sim_config_dict = {
            "initial_balance": self.config.initial_balance,
            "default_volume": self.config.default_volume,
            "max_positions": 1,
        }

        # 全体設定
        config_dict = {
            "symbol": self.config.symbol,
            "base_timeframe": self.config.base_timeframe.value,
            "confidence_threshold": (
                self.config.confidence_threshold
            ),
        }

        # 一時ディレクトリ作成
        tmp_dir = Path(
            tempfile.mkdtemp(prefix="autotrader_bt_")
        )

        try:
            # チャンク別Parquetファイルを準備
            chunk_files = self._prepare_chunk_files(
                df, market_data, chunks, tmp_dir
            )

            # 並列実行
            chunk_results: list[ChunkResult] = []

            with ProcessPoolExecutor(
                max_workers=self._max_workers
            ) as executor:
                futures = {}
                for chunk_id, (
                    chunk_start,
                    chunk_end,
                ) in enumerate(chunks):
                    files = chunk_files[chunk_id]

                    future = executor.submit(
                        _process_chunk_worker,
                        chunk_id=chunk_id,
                        chunk_start=chunk_start,
                        chunk_end=chunk_end,
                        base_parquet_path=files["base"],
                        tf_parquet_paths=files["tfs"],
                        sim_config_dict=sim_config_dict,
                        config_dict=config_dict,
                        warmup_bars=self.config.warmup_bars,
                    )
                    futures[future] = chunk_id

                for future in as_completed(futures):
                    result_dict = future.result()
                    chunk_result = ChunkResult(
                        chunk_id=result_dict["chunk_id"],
                        start_date=datetime.fromisoformat(
                            result_dict["start_date"]
                        ),
                        end_date=datetime.fromisoformat(
                            result_dict["end_date"]
                        ),
                        trades=result_dict["trades"],
                        processing_time=result_dict[
                            "processing_time"
                        ],
                        error=result_dict["error"],
                    )
                    chunk_results.append(chunk_result)

                    if chunk_result.error:
                        logger.error(
                            f"チャンク{chunk_result.chunk_id}"
                            f"エラー: "
                            f"{chunk_result.error}"
                        )
                    else:
                        logger.info(
                            f"チャンク{chunk_result.chunk_id}"
                            f"完了: "
                            f"trades="
                            f"{len(chunk_result.trades)}, "
                            f"time="
                            f"{chunk_result.processing_time:.2f}s"
                        )

        finally:
            # 一時ファイル削除
            shutil.rmtree(tmp_dir, ignore_errors=True)

        # 結果を時系列順にソート
        chunk_results.sort(key=lambda x: x.chunk_id)

        # 全トレードをマージ
        all_trades = self._merge_trades(chunk_results)

        # 最終結果を計算
        result = self._calculate_final_result(
            all_trades,
            chunk_results,
            time.time() - total_start,
        )

        logger.info(
            f"高速バックテスト完了: "
            f"trades={result.total_trades}, "
            f"win_rate={result.win_rate:.1f}%, "
            f"pnl={result.total_pnl:.0f}, "
            f"time={result.total_processing_time:.2f}s"
        )

        return result

    def _prepare_chunk_files(
        self,
        df: pd.DataFrame,
        market_data: dict[str, pd.DataFrame],
        chunks: list[tuple[datetime, datetime]],
        tmp_dir: Path,
    ) -> list[dict[str, Any]]:
        """チャンク別Parquetファイルを準備

        Args:
            df: 基準データ
            market_data: 時間足別市場データ
            chunks: チャンク期間リスト
            tmp_dir: 一時ディレクトリ

        Returns:
            チャンク別ファイルパス情報リスト
        """
        # 基準TFの分数を計算
        tf_minutes_map = {
            "M1": 1, "M5": 5, "M15": 15, "M30": 30,
            "H1": 60, "H4": 240, "D1": 1440,
        }
        base_tf_str = self.config.base_timeframe.value
        tf_minutes = tf_minutes_map.get(base_tf_str, 15)
        warmup_minutes = (
            self.config.warmup_bars * tf_minutes
        )

        chunk_files = []
        for chunk_id, (
            chunk_start,
            chunk_end,
        ) in enumerate(chunks):
            # ウォームアップ期間の開始
            warmup_start = chunk_start - timedelta(
                minutes=warmup_minutes
            )
            # exit tail期間の終了
            data_end = chunk_end + timedelta(days=30)

            # 基準データをParquetに保存
            chunk_df = df[
                (df["time"] >= warmup_start)
                & (df["time"] < data_end)
            ].copy()

            base_path = (
                tmp_dir / f"chunk_{chunk_id}_base.parquet"
            )
            chunk_df.to_parquet(base_path, index=False)

            # 市場データも同様
            tf_paths: dict[str, str] = {}
            for tf_str, tf_df in market_data.items():
                tf_df_copy = tf_df.copy()
                # インデックスがdatetimeの場合はリセット
                if isinstance(
                    tf_df_copy.index, pd.DatetimeIndex
                ):
                    tf_df_copy = tf_df_copy.reset_index()
                    tf_df_copy = tf_df_copy.rename(
                        columns={"index": "time"}
                    )

                # 期間でフィルタリング
                if "time" in tf_df_copy.columns:
                    tf_df_copy = tf_df_copy[
                        (tf_df_copy["time"] >= warmup_start)
                        & (tf_df_copy["time"] < data_end)
                    ].copy()

                tf_path = (
                    tmp_dir
                    / f"chunk_{chunk_id}_{tf_str}.parquet"
                )
                tf_df_copy.to_parquet(tf_path, index=False)
                tf_paths[tf_str] = str(tf_path)

            chunk_files.append({
                "base": str(base_path),
                "tfs": tf_paths,
            })

        return chunk_files

    def _create_chunks(
        self,
    ) -> list[tuple[datetime, datetime]]:
        """チャンク期間リストを作成

        Returns:
            (開始日, 終了日)のタプルリスト
        """
        chunks = []
        current = self.config.start_date

        while current < self.config.end_date:
            # チャンク終了日を計算
            chunk_end_month = (
                current.month + self.config.chunk_months
            )
            chunk_end_year = current.year

            while chunk_end_month > 12:
                chunk_end_month -= 12
                chunk_end_year += 1

            chunk_end = datetime(
                chunk_end_year, chunk_end_month, 1
            )

            if chunk_end > self.config.end_date:
                chunk_end = self.config.end_date

            chunks.append((current, chunk_end))
            current = chunk_end

        return chunks

    def _merge_trades(
        self, chunk_results: list[ChunkResult]
    ) -> list[dict[str, Any]]:
        """チャンク結果からトレードをマージ

        Args:
            chunk_results: チャンク結果リスト

        Returns:
            時系列順のトレードリスト
        """
        all_trades = []
        for chunk in chunk_results:
            all_trades.extend(chunk.trades)

        # opened_atでソート
        all_trades.sort(
            key=lambda t: t.get("opened_at") or ""
        )

        return all_trades

    def _calculate_final_result(
        self,
        trades: list[dict[str, Any]],
        chunk_results: list[ChunkResult],
        total_time: float,
    ) -> FastBacktestResult:
        """最終結果を計算

        Args:
            trades: 全トレードリスト
            chunk_results: チャンク結果リスト
            total_time: 総処理時間

        Returns:
            FastBacktestResult
        """
        if not trades:
            return FastBacktestResult(
                chunk_results=chunk_results,
                total_processing_time=total_time,
            )

        winning_trades = sum(
            1
            for t in trades
            if (t.get("profit_loss") or 0) > 0
        )
        losing_trades = sum(
            1
            for t in trades
            if (t.get("profit_loss") or 0) <= 0
        )
        total_trades = len(trades)

        win_rate = (
            winning_trades / total_trades * 100
            if total_trades > 0
            else 0
        )

        total_pnl = sum(
            t.get("profit_loss") or 0 for t in trades
        )

        # プロフィットファクター
        gross_profit = sum(
            t.get("profit_loss") or 0
            for t in trades
            if (t.get("profit_loss") or 0) > 0
        )
        gross_loss = abs(
            sum(
                t.get("profit_loss") or 0
                for t in trades
                if (t.get("profit_loss") or 0) < 0
            )
        )
        profit_factor = (
            gross_profit / gross_loss
            if gross_loss > 0
            else float("inf")
        )

        # 最大ドローダウン計算
        cumulative_pnl = 0.0
        peak = 0.0
        max_dd = 0.0

        for trade in trades:
            cumulative_pnl += trade.get("profit_loss") or 0
            if cumulative_pnl > peak:
                peak = cumulative_pnl
            dd = peak - cumulative_pnl
            if dd > max_dd:
                max_dd = dd

        max_dd_pct = (
            max_dd / self.config.initial_balance * 100
            if self.config.initial_balance > 0
            else 0
        )

        return_pct = (
            total_pnl / self.config.initial_balance * 100
        )

        return FastBacktestResult(
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate=win_rate,
            total_pnl=total_pnl,
            profit_factor=profit_factor,
            max_drawdown_pct=max_dd_pct,
            return_pct=return_pct,
            trades=trades,
            chunk_results=chunk_results,
            total_processing_time=total_time,
        )
