"""マルチ通貨ペア同時実行バックテスト（時系列インターリーブ方式）

JPY/USDペアを時系列インターリーブで同時実行し、共有資金プール＋
グローバルポジション制限でライブに近い条件を再現する。

使い方:
    python scripts/run_multi_pair_backtest.py --data-dir data
    python scripts/run_multi_pair_backtest.py --data-dir data --tests R1,M0
    python scripts/run_multi_pair_backtest.py --data-dir data --symbols USDJPY,EURJPY
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import gc
import heapq
import logging
import math
import sys
import time
from concurrent.futures import (
    ProcessPoolExecutor,
    ThreadPoolExecutor,
    as_completed,
)
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

# プロジェクトルートをパスに追加
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from autotrader.backtest.candle_arrays import CandleArrays  # noqa: E402
from autotrader.backtest.runner import (  # noqa: E402
    BacktestConfig,
    BacktestRunner,
)
from autotrader.backtest.simulator import (  # noqa: E402
    SimulatorConfig,
    TradeSimulator,
)
from autotrader.config.paths import get_data_dir, get_log_dir  # noqa: E402
from autotrader.config.trading_params import (  # noqa: E402
    get_pip_unit,
    get_preset,
    get_quote_ccy_rate,
)
from autotrader.core.entities import Signal  # noqa: E402
from autotrader.core.enums import (  # noqa: E402
    ExitReason,
    SignalType,
    Timeframe,
)
from autotrader.decision.unified import (  # noqa: E402
    UnifiedBotConfig,
    UnifiedTradeBot,
)
from autotrader.decision.unified.adaptive import (  # noqa: E402
    TradeRecord,
)


def load_signal_overrides(
    symbol: str,
    preset_path: Path | None = None,
) -> dict[str, Any]:
    """symbol_presets.yaml からsignal設定を読み込み"""
    path = preset_path or (_PROJECT_ROOT / "config" / "symbol_presets.yaml")
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}
    defaults = dict(raw.get("signal", {}))
    symbols = raw.get("symbols", {})
    sym_data = symbols.get(symbol, {})
    if isinstance(sym_data, dict):
        sym_signal = sym_data.get("signal", {})
        if sym_signal:
            defaults.update(sym_signal)
    return defaults


def build_bot_config(
    symbol: str,
    extra_overrides: dict[str, Any] | None = None,
) -> UnifiedBotConfig:
    """プリセット + signal設定からUnifiedBotConfigを構築"""
    preset = get_preset(symbol)
    signal = load_signal_overrides(symbol)
    valid_fields = {f.name for f in dataclasses.fields(UnifiedBotConfig)}
    overrides: dict[str, Any] = {}
    _pip_unit = get_pip_unit(symbol)
    _qcr = get_quote_ccy_rate(symbol)
    overrides.update(
        {
            "max_positions": preset.max_positions,
            "bonus_max_positions": preset.bonus_max_positions,
            "bonus_score_threshold": preset.bonus_score_threshold,
            "base_risk_pct": preset.base_risk_pct,
            "max_lot_per_trade": preset.max_lot_per_trade,
            "max_total_exposure_lot": preset.max_total_exposure_lot,
            "equity_floor_pct": preset.equity_floor_pct,
            "pip_unit": _pip_unit,
            "quote_ccy_rate": _qcr,
        }
    )
    for k, v in signal.items():
        if k in valid_fields:
            overrides[k] = v
    if extra_overrides:
        overrides.update(extra_overrides)
    return UnifiedBotConfig(**overrides)

# --- 定数 ---
JPY_SYMBOLS = [
    "USDJPY", "EURJPY", "GBPJPY",
    "AUDJPY", "CADJPY", "CHFJPY",
]
USD_SYMBOLS = [
    "EURUSD", "GBPUSD", "AUDUSD",
    "NZDUSD", "USDCAD", "USDCHF",
]
SYMBOLS = JPY_SYMBOLS + USD_SYMBOLS
START_YEAR = 2020
END_YEAR = 2025
INITIAL_EQUITY = 1_000_000.0

logger = logging.getLogger(__name__)


# =============================================================
# データクラス
# =============================================================
@dataclass
class MultiPairConfig:
    """テストケースのパラメータ

    Attributes:
        name: テスト名
        global_max_positions: 全ペア合計の最大ポジション数
        per_pair_max_positions: ペア当たりの最大ポジション数
        global_max_exposure_lot: 全ペア合計の最大ロット
        base_risk_pct: リスク率（ポートフォリオ全体）
        consensus_threshold: コンセンサス閾値
    """

    name: str = "M0"
    global_max_positions: int = 6
    per_pair_max_positions: int = 1
    global_max_exposure_lot: float = 10.0
    base_risk_pct: float = 0.02
    consensus_threshold: float = 9.0


@dataclass
class PortfolioState:
    """共有ポートフォリオ状態

    Attributes:
        equity: 現在の共有資金
        initial_equity: 初期資金
        peak_equity: ピーク資金
        global_open_positions: 全ペア合計ポジション数
        global_exposure_lot: 全ペア合計ロット
        per_pair_positions: ペア別ポジション数
        per_pair_exposure: ペア別ロット
        blocked_global: グローバル制限発動回数
        blocked_per_pair: ペア別制限発動回数
        blocked_exposure: エクスポージャー制限発動回数
        monthly_pnl: 月次PnL辞書
    """

    equity: float = INITIAL_EQUITY
    initial_equity: float = INITIAL_EQUITY
    peak_equity: float = INITIAL_EQUITY
    max_dd_pct: float = 0.0
    global_open_positions: int = 0
    global_exposure_lot: float = 0.0
    per_pair_positions: dict[str, int] = field(default_factory=dict)
    per_pair_exposure: dict[str, float] = field(default_factory=dict)
    blocked_global: int = 0
    blocked_per_pair: int = 0
    blocked_exposure: int = 0
    monthly_pnl: dict[tuple[int, int], float] = field(
        default_factory=dict,
    )

    def can_open_position(
        self,
        symbol: str,
        config: MultiPairConfig,
    ) -> bool:
        """新規ポジションを開けるかチェック

        Args:
            symbol: 通貨ペア名
            config: テスト設定

        Returns:
            bool: 開ける場合True
        """
        # グローバルポジション制限
        if self.global_open_positions >= config.global_max_positions:
            self.blocked_global += 1
            return False
        # ペア別ポジション制限
        pair_pos = self.per_pair_positions.get(symbol, 0)
        if pair_pos >= config.per_pair_max_positions:
            self.blocked_per_pair += 1
            return False
        # グローバルエクスポージャー制限
        if self.global_exposure_lot >= config.global_max_exposure_lot:
            self.blocked_exposure += 1
            return False
        return True

    def update_positions(
        self,
        contexts: dict[str, "PairContext"],
    ) -> None:
        """全ペアのポジション情報を更新

        Args:
            contexts: ペアコンテキスト辞書
        """
        total_pos = 0
        total_lot = 0.0
        for sym, ctx in contexts.items():
            positions = ctx.simulator.get_open_positions()
            n = len(positions)
            lot = sum(p.volume for p in positions)
            self.per_pair_positions[sym] = n
            self.per_pair_exposure[sym] = lot
            total_pos += n
            total_lot += lot
        self.global_open_positions = total_pos
        self.global_exposure_lot = total_lot

    def update_peak(self) -> None:
        """ピーク更新＆バーレベルDD追跡"""
        if self.equity > self.peak_equity:
            self.peak_equity = self.equity
        if self.peak_equity > 0:
            dd = (
                (self.peak_equity - self.equity)
                / self.peak_equity
                * 100
            )
            if dd > self.max_dd_pct:
                self.max_dd_pct = dd


@dataclass
class PairContext:
    """ペアごとのバックテストコンテキスト

    Attributes:
        symbol: 通貨ペア名
        bot: トレードボット
        simulator: シミュレーター
        arrays: CandleArrays（基準TF）
        base_tf: 基準タイムフレーム
        runner: BacktestRunner（データ保持用）
    """

    symbol: str
    bot: UnifiedTradeBot
    simulator: TradeSimulator
    arrays: CandleArrays
    base_tf: Timeframe
    runner: BacktestRunner


# =============================================================
# データロード
# =============================================================
def load_pair_data(
    symbol: str,
    data_dir: str,
) -> tuple[BacktestRunner, dict[str, pd.DataFrame]]:
    """ペアデータをロードしてBacktestRunnerとmarket_dataを返す

    Args:
        symbol: 通貨ペア名
        data_dir: データディレクトリ

    Returns:
        tuple: (BacktestRunner, market_data辞書)
    """
    preset = get_preset(symbol)
    config = BacktestConfig(
        symbol=symbol,
        spread_pips=preset.spread_pips,
        slippage_pips=preset.slippage_pips,
        pip_value=preset.pip_value,
        max_positions=preset.max_positions,
        bonus_max_positions=preset.bonus_max_positions,
        bonus_score_threshold=preset.bonus_score_threshold,
    )
    runner = BacktestRunner(
        data_dir=data_dir,
        config=config,
        verbose=False,
        log_to_file=False,
    )
    # 全期間データをロード
    # load_data()はDaily→D1フォールバック等を処理する
    market_data = runner._load_all_timeframes(include_m1=True)
    # _load_all_timeframes でD1がロードされない場合
    # load_data() の Daily→D1 フォールバックを利用
    if "D1" not in market_data:
        runner.load_data()
        if runner._d1_df is not None:
            market_data["D1"] = runner._d1_df
    # メモリ最適化: _tf_data は market_data に移管済み
    if hasattr(runner, "_tf_data"):
        runner._tf_data.clear()
    return runner, market_data


def load_all_pair_data(
    symbols: list[str],
    data_dir: str,
    max_workers: int = 6,
) -> dict[str, tuple[BacktestRunner, dict[str, pd.DataFrame]]]:
    """全ペアのデータをスレッド並列でロード

    I/Oバウンド（CSV/Parquet読み込み）のためスレッド並列が最適。
    各呼び出しが独立したオブジェクトを返すためスレッドセーフ。

    Args:
        symbols: シンボルリスト
        data_dir: データディレクトリ
        max_workers: 並列ワーカー数

    Returns:
        dict[str, tuple[BacktestRunner, dict[str, pd.DataFrame]]]:
            シンボル→(BacktestRunner, market_data)
    """
    runners: dict[
        str, tuple[BacktestRunner, dict[str, pd.DataFrame]]
    ] = {}
    workers = min(max_workers, len(symbols))
    _t0 = time.time()
    print(
        f"\nデータロード中... "
        f"({len(symbols)}ペア, workers={workers})"
    )

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(load_pair_data, sym, data_dir): sym
            for sym in symbols
        }
        for future in as_completed(futures):
            sym = futures[future]
            _elapsed = time.time() - _t0
            runners[sym] = future.result()
            print(f"  {sym}: ロード完了 ({_elapsed:.1f}s)")

    total = time.time() - _t0
    print(f"  全ペアロード完了: {total:.1f}s")
    return runners


# =============================================================
# コンテキスト構築
# =============================================================
def setup_pair_context(
    symbol: str,
    runner: BacktestRunner,
    year: int,
    bot_config: UnifiedBotConfig,
    initial_balance: float,
    full_market_data: dict[str, pd.DataFrame] | None = None,
) -> PairContext | None:
    """ペアごとのBot/Simulator/Arraysを初期化

    Args:
        symbol: 通貨ペア名
        runner: データロード済みランナー
        year: 対象年
        bot_config: ボット設定
        initial_balance: 初期残高
        full_market_data: 全期間market_data（年フィルタ前）

    Returns:
        PairContext | None: コンテキスト（データなしならNone）
    """
    if full_market_data is None:
        return None

    # 基準TF選択（M1 > M5 > M15 > H1）
    base_tf_name = None
    for tf_name in ["M1", "M5", "M15", "H1"]:
        if tf_name in full_market_data:
            base_tf_name = tf_name
            break
    if base_tf_name is None:
        return None

    tf = Timeframe(base_tf_name)
    df = full_market_data[base_tf_name]

    # 年フィルタ
    start_date = datetime(year, 1, 1)
    end_date = datetime(year + 1, 1, 1)
    period_df = df[
        (df["time"] >= start_date) & (df["time"] < end_date)
    ].reset_index(drop=True)

    if period_df.empty:
        return None

    # market_data: 年フィルタ済みデータ
    market_data: dict[str, pd.DataFrame] = {}
    for tf_key, tf_df in full_market_data.items():
        year_df = tf_df[
            (tf_df["time"] >= start_date)
            & (tf_df["time"] < end_date)
        ].reset_index(drop=True)
        if not year_df.empty:
            market_data[tf_key] = year_df

    # Bot初期化
    bot = UnifiedTradeBot(bot_config)
    bot.state = bot.state.with_initial_equity(initial_balance)
    bot.set_market_data(market_data)

    # SimulatorConfig
    preset = get_preset(symbol)
    _pip_unit = get_pip_unit(symbol)
    _quote_ccy_rate = get_quote_ccy_rate(symbol)
    sim_config = SimulatorConfig(
        initial_balance=initial_balance,
        spread_pips=preset.spread_pips,
        slippage_pips=preset.slippage_pips,
        pip_value=preset.pip_value,
        max_positions=preset.max_positions,
        bonus_max_positions=preset.bonus_max_positions,
        bonus_score_threshold=preset.bonus_score_threshold,
        default_volume=1.0,
        use_position_manager=bot_config.use_position_manager,
        use_dynamic_lot=bot_config.use_dynamic_lot,
        pip_unit=_pip_unit,
        quote_ccy_rate=_quote_ccy_rate,
        commission_per_lot=preset.commission_per_lot,
        bot_config=bot_config,
    )
    simulator = TradeSimulator(config=sim_config)

    arrays = CandleArrays.from_dataframe(period_df)
    del period_df  # DataFrame のメモリ解放（CandleArrays に変換済み）

    return PairContext(
        symbol=symbol,
        bot=bot,
        simulator=simulator,
        arrays=arrays,
        base_tf=tf,
        runner=runner,
    )


# =============================================================
# 核心: 時系列インターリーブ実行
# =============================================================
def run_multi_pair_year(
    year: int,
    contexts: dict[str, PairContext],
    multi_config: MultiPairConfig,
    portfolio: PortfolioState,
) -> dict[str, list[Any]]:
    """1年分のマルチペアインターリーブ実行

    Args:
        year: 対象年
        contexts: ペアコンテキスト辞書
        multi_config: テスト設定
        portfolio: 共有ポートフォリオ状態

    Returns:
        dict: ペア別トレードリスト
    """
    # 全ペアのタイムスタンプをストリーミングマージ
    # 各ペアのタイムスタンプは既にソート済み → heapq.merge
    def _pair_time_gen(
        sym: str,
        arrays: CandleArrays,
    ):  # type: ignore[return]
        """ペア別ソート済みタイムスタンプジェネレータ"""
        for idx in range(arrays.n_rows):
            yield (arrays.get_time(idx), sym, idx)

    total_bars = sum(
        ctx.arrays.n_rows for ctx in contexts.values()
    )
    merged = heapq.merge(
        *(
            _pair_time_gen(sym, ctx.arrays)
            for sym, ctx in contexts.items()
        ),
        key=lambda x: (x[0], x[1]),
    )

    # 月次トラッキング
    current_month: tuple[int, int] | None = None
    month_start_equity = portfolio.equity

    # Opt 2: ポジション情報キャッシュ初期化
    # (exposure, buy_lot, sell_lot, buy_count, sell_count)
    _cached_exposure: dict[
        str, tuple[float, float, float, int, int]
    ] = {}
    for sym in contexts:
        _cached_exposure[sym] = (0.0, 0.0, 0.0, 0, 0)
        portfolio.per_pair_positions[sym] = 0
        portfolio.per_pair_exposure[sym] = 0.0

    # 進捗表示
    _t0 = time.time()

    for bar_num, (bar_time, sym, idx) in enumerate(merged):
        ctx = contexts[sym]

        # 月変わり検出
        candle_month = (bar_time.year, bar_time.month)
        if current_month is None:
            current_month = candle_month
            month_start_equity = portfolio.equity
        elif candle_month != current_month:
            # 月末PnL記録
            month_pnl = portfolio.equity - month_start_equity
            portfolio.monthly_pnl[current_month] = (
                portfolio.monthly_pnl.get(current_month, 0.0) + month_pnl
            )
            current_month = candle_month
            month_start_equity = portfolio.equity

        # Candle取得
        candle = ctx.arrays.get_candle(idx, sym, ctx.base_tf)

        # equity同期: 共有equityをシミュレータに反映
        ctx.simulator.state.balance = portfolio.equity
        ctx.simulator.state.equity = portfolio.equity
        ctx.bot.state = ctx.bot.state.with_initial_equity(
            portfolio.initial_equity,
        )
        ctx.bot.state = dataclasses.replace(
            ctx.bot.state,
            equity=portfolio.equity,
        )

        # ポジション情報をbot stateに同期（キャッシュ参照）
        exp, b_lot, s_lot, b_cnt, s_cnt = _cached_exposure[sym]
        ctx.bot.state = ctx.bot.state.with_exposure(
            open_exposure_lot=exp,
            open_same_direction_lot=max(b_lot, s_lot),
            open_buy_count=b_cnt,
            open_sell_count=s_cnt,
        )

        # シグナル生成
        current_time = pd.Timestamp(bar_time)
        consolidated = ctx.bot.generate_signal(
            current_time,
            candle,
        )

        # Signal変換
        signal = None
        if (
            consolidated.direction != SignalType.HOLD
            and consolidated.confidence >= 0.5
        ):
            sl_price = None
            tp_price = None
            if consolidated.sl_pips > 0:
                _base = (
                    consolidated.entry_price
                    if consolidated.entry_price is not None
                    else candle.close
                )
                _pu = get_pip_unit(sym)
                if consolidated.direction == SignalType.BUY:
                    sl_price = (
                        _base - consolidated.sl_pips * _pu
                    )
                    tp_price = (
                        _base + consolidated.tp_pips * _pu
                    )
                else:
                    sl_price = (
                        _base + consolidated.sl_pips * _pu
                    )
                    tp_price = (
                        _base - consolidated.tp_pips * _pu
                    )

            signal = Signal(
                symbol=sym,
                timeframe=ctx.base_tf,
                signal_type=consolidated.direction,
                confidence=min(consolidated.confidence, 1.0),
                stop_loss=sl_price,
                take_profit=tp_price,
                reasoning=consolidated.rationale,
                regime=consolidated.regime,
                mode=consolidated.mode,
                consensus_score=consolidated.consensus_score,
                lot=consolidated.lot,
            )

        # グローバル制限チェック（シグナルありの場合のみ）
        if signal is not None:
            if not portfolio.can_open_position(sym, multi_config):
                signal = None  # エントリーブロック

        # balance記録・ポジション数スナップショット
        balance_before = ctx.simulator.state.balance
        prev_n = len(ctx.simulator.state.open_positions)
        prev_trade_count = len(ctx.simulator.state.closed_trades)

        # process_candle実行
        _consensus_scores = None
        if consolidated is not None:
            _consensus_scores = (
                consolidated.buy_score,
                consolidated.sell_score,
            )
        ctx.simulator.process_candle(
            candle,
            signal,
            consensus_scores=_consensus_scores,
        )

        # PnL差分キャプチャ
        pnl_delta = ctx.simulator.state.balance - balance_before
        portfolio.equity += pnl_delta
        portfolio.update_peak()

        # Opt 2: ポジション変化検出 → キャッシュ差分更新
        curr_n = len(ctx.simulator.state.open_positions)
        balance_changed = (
            ctx.simulator.state.balance != balance_before
        )
        if curr_n != prev_n or balance_changed:
            positions = ctx.simulator.state.open_positions
            new_exp = sum(p.volume for p in positions)
            new_b_lot = sum(
                p.volume
                for p in positions
                if p.signal_type == SignalType.BUY
            )
            new_s_lot = sum(
                p.volume
                for p in positions
                if p.signal_type == SignalType.SELL
            )
            new_b_cnt = sum(
                1
                for p in positions
                if p.signal_type == SignalType.BUY
            )
            new_s_cnt = sum(
                1
                for p in positions
                if p.signal_type == SignalType.SELL
            )
            _cached_exposure[sym] = (
                new_exp,
                new_b_lot,
                new_s_lot,
                new_b_cnt,
                new_s_cnt,
            )
            # portfolio 差分更新
            portfolio.per_pair_positions[sym] = curr_n
            portfolio.per_pair_exposure[sym] = new_exp
            portfolio.global_open_positions = sum(
                portfolio.per_pair_positions.values(),
            )
            portfolio.global_exposure_lot = sum(
                portfolio.per_pair_exposure.values(),
            )

        # 決済時: bot.on_trade_executed呼び出し
        closed_trades = ctx.simulator.state.closed_trades
        if len(closed_trades) > prev_trade_count:
            new_trade = closed_trades[-1]
            pnl = new_trade.profit_loss or 0
            _trade_record = TradeRecord.from_trade(new_trade)
            ctx.bot.on_trade_executed(
                bar_time,
                pnl=pnl,
                trade_record=_trade_record,
            )

        # 進捗表示（5000バーごと）
        if bar_num % 5000 == 0 and bar_num > 0:
            elapsed = time.time() - _t0
            pct = bar_num / total_bars * 100
            print(
                f"    {year}年: {pct:.0f}% "
                f"({bar_num}/{total_bars}) "
                f"{elapsed:.0f}s",
                end="\r",
            )

    # 年末: 全ペア強制決済
    for sym, ctx in contexts.items():
        last_idx = ctx.arrays.n_rows - 1
        if last_idx >= 0:
            last_candle = ctx.arrays.get_candle(
                last_idx, sym, ctx.base_tf,
            )
            balance_before = ctx.simulator.state.balance
            ctx.simulator.force_close_all(
                last_candle, ExitReason.FORCE_CLOSE,
            )
            pnl_delta = ctx.simulator.state.balance - balance_before
            portfolio.equity += pnl_delta
            portfolio.update_peak()

    # 最終月PnL記録
    if current_month is not None:
        month_pnl = portfolio.equity - month_start_equity
        portfolio.monthly_pnl[current_month] = (
            portfolio.monthly_pnl.get(current_month, 0.0) + month_pnl
        )

    # ポジション更新（年末強制決済後）
    for sym, ctx in contexts.items():
        positions = ctx.simulator.state.open_positions
        n = len(positions)
        lot = sum(p.volume for p in positions)
        portfolio.per_pair_positions[sym] = n
        portfolio.per_pair_exposure[sym] = lot
    portfolio.global_open_positions = sum(
        portfolio.per_pair_positions.values(),
    )
    portfolio.global_exposure_lot = sum(
        portfolio.per_pair_exposure.values(),
    )

    elapsed = time.time() - _t0
    print(
        f"    {year}年: 100% "
        f"({total_bars}/{total_bars}) "
        f"{elapsed:.0f}s          ",
    )

    # ペア別トレード返却
    pair_trades: dict[str, list[Any]] = {}
    for sym, ctx in contexts.items():
        pair_trades[sym] = list(ctx.simulator.state.closed_trades)

    return pair_trades


# =============================================================
# 年並列ワーカー（別プロセスで実行）
# =============================================================
def _run_year_worker(args: tuple) -> dict[str, Any] | None:
    """年並列ワーカー（別プロセスで実行）

    各ワーカーが独立にデータロード→コンテキスト構築→
    インターリーブ実行を行う。
    結果はシリアライズ可能なdictで返す。

    Args:
        args: (year, symbols, data_dir,
               multi_config_dict, bot_extra_overrides)

    Returns:
        dict | None: 年の実行結果（データなしならNone）
    """
    (
        year,
        symbols,
        data_dir,
        multi_config_dict,
        bot_extra_overrides,
    ) = args

    # ワーカープロセス内でインポート（spawn対応）
    import sys as _sys
    from pathlib import Path as _Path

    _sr = _Path(__file__).resolve().parent.parent
    if str(_sr) not in _sys.path:
        _sys.path.insert(0, str(_sr))

    from scripts.run_multi_pair_backtest import (
        build_bot_config as _build_bot_config,
    )

    # MultiPairConfig 復元
    mc = MultiPairConfig(**multi_config_dict)

    if len(symbols) >= 8:
        print(
            f"  警告: {len(symbols)}ペア×年並列 = "
            f"高メモリ使用。max_year_workers=1 を推奨"
        )

    # データロード（各ワーカーが独自にスレッド並列ロード）
    runners = load_all_pair_data(
        symbols, data_dir, max_workers=min(6, len(symbols)),
    )

    # ポートフォリオ初期化（年独立: 毎年100万リセット）
    portfolio = PortfolioState(
        equity=INITIAL_EQUITY,
        initial_equity=INITIAL_EQUITY,
        peak_equity=INITIAL_EQUITY,
    )

    # ペアコンテキスト構築
    contexts: dict[str, PairContext] = {}
    for sym in symbols:
        runner, full_md = runners[sym]
        extra_overrides: dict[str, Any] = {
            "base_risk_pct": mc.base_risk_pct,
            "consensus_threshold": mc.consensus_threshold,
        }
        extra_overrides.update(bot_extra_overrides)
        bot_config = _build_bot_config(sym, extra_overrides)

        ctx = setup_pair_context(
            sym,
            runner,
            year,
            bot_config,
            portfolio.equity,
            full_market_data=full_md,
        )
        if ctx is not None:
            contexts[sym] = ctx

    if not contexts:
        return None

    # インターリーブ実行
    pair_trades = run_multi_pair_year(
        year, contexts, mc, portfolio,
    )

    # 結果をシリアライズ可能なdictに変換
    year_pnl = portfolio.equity - portfolio.initial_equity
    year_trades = sum(len(t) for t in pair_trades.values())

    # 月次PnLをstr化（tupleキーはpickle不可ではないが
    # JSON化で問題になるためstr変換）
    monthly_pnl_str: dict[str, float] = {}
    for (y, m), pnl in portfolio.monthly_pnl.items():
        monthly_pnl_str[f"{y:04d}-{m:02d}"] = pnl

    # ペア別サマリー（Tradeオブジェクトは転送不可）
    pair_summaries: dict[str, dict[str, Any]] = {}
    for sym in symbols:
        trades = pair_trades.get(sym, [])
        n = len(trades)
        wins = sum(
            1 for t in trades if (t.profit_loss or 0) > 0
        )
        gp = sum(
            t.profit_loss
            for t in trades
            if (t.profit_loss or 0) > 0
        )
        gl = abs(
            sum(
                t.profit_loss
                for t in trades
                if (t.profit_loss or 0) <= 0
            )
        )
        np_ = sum(t.profit_loss or 0 for t in trades)
        pair_summaries[sym] = {
            "trades": n,
            "wins": wins,
            "gross_profit": gp,
            "gross_loss": gl,
            "net_profit": np_,
        }

    return {
        "year": year,
        "year_pnl": year_pnl,
        "year_trades": year_trades,
        "final_equity": portfolio.equity,
        "max_dd_pct": portfolio.max_dd_pct,
        "monthly_pnl": monthly_pnl_str,
        "blocked_global": portfolio.blocked_global,
        "blocked_per_pair": portfolio.blocked_per_pair,
        "blocked_exposure": portfolio.blocked_exposure,
        "pair_summaries": pair_summaries,
    }


# =============================================================
# 年並列結果の集約
# =============================================================
def aggregate_year_results(
    test_name: str,
    year_results: list[dict[str, Any]],
    symbols: list[str],
    num_years: int,
) -> dict[str, Any]:
    """年並列結果をポートフォリオメトリクスに集約

    Args:
        test_name: テスト名
        year_results: 年ワーカーの結果リスト
        symbols: シンボルリスト
        num_years: 年数

    Returns:
        dict: 集約結果（aggregate_resultsと同形式）
    """
    total_profit = sum(yr["year_pnl"] for yr in year_results)
    max_dd_pct = max(yr["max_dd_pct"] for yr in year_results)

    # 月次PnLを結合（"YYYY-MM" -> pnl）
    all_monthly: dict[str, float] = {}
    for yr in year_results:
        for key, pnl in yr["monthly_pnl"].items():
            all_monthly[key] = (
                all_monthly.get(key, 0.0) + pnl
            )

    sorted_months = sorted(all_monthly.keys())
    monthly_pnl_list = [all_monthly[m] for m in sorted_months]

    # 月次DDも計算（バーレベルDDとの比較用）
    equity = INITIAL_EQUITY
    peak = equity
    max_dd_monthly = 0.0
    for pnl in monthly_pnl_list:
        equity += pnl
        if equity > peak:
            peak = equity
        dd = (
            (peak - equity) / peak * 100
            if peak > 0
            else 0.0
        )
        if dd > max_dd_monthly:
            max_dd_monthly = dd
    # 年独立実行なので各年のバーレベルDDの最大値を使用
    max_dd = max(max_dd_pct, max_dd_monthly)

    # Sharpe
    if len(monthly_pnl_list) > 1:
        arr = np.array(monthly_pnl_list)
        mean_m = np.mean(arr)
        std_m = np.std(arr, ddof=1)
        sharpe = (
            (mean_m / std_m) * math.sqrt(12)
            if std_m > 0
            else 0.0
        )
    else:
        sharpe = 0.0

    # ペア別メトリクス集約
    total_trades = 0
    total_wins = 0
    total_gp = 0.0
    total_gl = 0.0
    pair_metrics: dict[str, dict[str, Any]] = {}

    for sym in symbols:
        n = 0
        wins = 0
        gp = 0.0
        gl = 0.0
        np_ = 0.0
        for yr in year_results:
            ps = yr["pair_summaries"].get(sym, {})
            n += ps.get("trades", 0)
            wins += ps.get("wins", 0)
            gp += ps.get("gross_profit", 0.0)
            gl += ps.get("gross_loss", 0.0)
            np_ += ps.get("net_profit", 0.0)
        total_trades += n
        total_wins += wins
        total_gp += gp
        total_gl += gl

        wr = wins / n * 100 if n > 0 else 0.0
        pf = gp / gl if gl > 0 else float("inf")
        pair_metrics[sym] = {
            "trades": n,
            "profit": np_,
            "wr": wr,
            "pf": pf,
            "contribution": 0.0,
        }

    # 寄与率
    for sym in symbols:
        pm = pair_metrics[sym]
        pm["contribution"] = (
            pm["profit"] / total_profit * 100
            if total_profit > 0
            else 0.0
        )

    wr = (
        total_wins / total_trades * 100
        if total_trades > 0
        else 0.0
    )
    pf = total_gp / total_gl if total_gl > 0 else float("inf")

    # 月間勝率
    winning_months = sum(1 for p in monthly_pnl_list if p > 0)
    monthly_wr = (
        winning_months / len(monthly_pnl_list) * 100
        if monthly_pnl_list
        else 0.0
    )

    # 年間収益率（各年の収益率の平均）
    year_return_pcts = [
        yr["year_pnl"] / INITIAL_EQUITY * 100
        for yr in year_results
    ]
    annual_return = (
        sum(year_return_pcts) / len(year_return_pcts)
        if year_return_pcts
        else 0.0
    )

    # blocked合計
    blocked_global = sum(
        yr["blocked_global"] for yr in year_results
    )
    blocked_per_pair = sum(
        yr["blocked_per_pair"] for yr in year_results
    )
    blocked_exposure = sum(
        yr["blocked_exposure"] for yr in year_results
    )

    # yearly_results構築
    yearly_results = [
        {
            "year": yr["year"],
            "pnl": yr["year_pnl"],
            "trades": yr["year_trades"],
            "equity": yr["final_equity"],
            "return_pct": (
                yr["year_pnl"] / INITIAL_EQUITY * 100
            ),
        }
        for yr in sorted(year_results, key=lambda x: x["year"])
    ]

    # monthly_pnlをtuple形式に戻す（既存コードとの互換性）
    monthly_pnl_tuples: dict[tuple[int, int], float] = {}
    for key, pnl in all_monthly.items():
        parts = key.split("-")
        monthly_pnl_tuples[(int(parts[0]), int(parts[1]))] = pnl

    final_equity = INITIAL_EQUITY + total_profit

    return {
        "test_name": test_name,
        "total_profit": total_profit,
        "annual_return_pct": annual_return,
        "max_dd_pct": max_dd,
        "sharpe": sharpe,
        "wr": wr,
        "pf": pf,
        "monthly_wr": monthly_wr,
        "total_trades": total_trades,
        "pair_metrics": pair_metrics,
        "yearly_results": yearly_results,
        "monthly_pnl": monthly_pnl_tuples,
        "blocked_global": blocked_global,
        "blocked_per_pair": blocked_per_pair,
        "blocked_exposure": blocked_exposure,
        "final_equity": final_equity,
    }


# =============================================================
# テストケース実行
# =============================================================
def run_test_case(
    test_config: MultiPairConfig,
    runners: dict[str, tuple[BacktestRunner, dict[str, pd.DataFrame]]],
    symbols: list[str],
    start_year: int = START_YEAR,
    end_year: int = END_YEAR,
    max_year_workers: int = 1,
    data_dir: str = "",
    bot_extra_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """テストケースを実行（順次 or 年並列）

    Args:
        test_config: テスト設定
        runners: ペア別(BacktestRunner, market_data)辞書
            （順次実行時のみ使用）
        symbols: 対象シンボル
        start_year: 開始年
        end_year: 終了年
        max_year_workers: 年並列数（1=順次, >1=並列）
        data_dir: データディレクトリ（並列実行時に必須）
        bot_extra_overrides: 追加botオーバーライド

    Returns:
        dict: テスト結果
    """
    num_years = end_year - start_year + 1
    _extra = bot_extra_overrides or {}

    # 8ペア以上では年並列を無効化（メモリ不足防止）
    if len(symbols) >= 8 and max_year_workers > 1:
        print(
            f"  注意: {len(symbols)}ペアのため "
            f"年並列を無効化 (max_year_workers: "
            f"{max_year_workers} → 1)"
        )
        max_year_workers = 1

    # 年並列→順次に切り替わった場合、データロードが必要
    if max_year_workers <= 1 and not runners:
        if not data_dir:
            data_dir = str(get_data_dir())
        runners = load_all_pair_data(
            symbols,
            data_dir,
            max_workers=min(6, len(symbols)),
        )

    print(f"\n{'=' * 60}")
    print(f"  テスト: {test_config.name}")
    print(
        f"  global_max={test_config.global_max_positions}, "
        f"per_pair={test_config.per_pair_max_positions}, "
        f"exposure={test_config.global_max_exposure_lot}, "
        f"risk={test_config.base_risk_pct}, "
        f"CT={test_config.consensus_threshold}"
    )
    if max_year_workers > 1:
        print(f"  年並列: {max_year_workers}ワーカー")
    print(f"{'=' * 60}")

    # --- 年並列実行 ---
    if max_year_workers > 1:
        mc_dict = asdict(test_config)
        worker_args = [
            (
                year,
                symbols,
                data_dir,
                mc_dict,
                _extra,
            )
            for year in range(start_year, end_year + 1)
        ]

        year_results: list[dict[str, Any]] = []
        with ProcessPoolExecutor(
            max_workers=max_year_workers,
        ) as executor:
            futures = executor.map(
                _run_year_worker, worker_args,
            )
            for yr_result in futures:
                if yr_result is not None:
                    year_results.append(yr_result)
                    print(
                        f"  {yr_result['year']}年: "
                        f"PnL={yr_result['year_pnl']:+,.0f}, "
                        f"Trades={yr_result['year_trades']}, "
                        f"Equity="
                        f"{yr_result['final_equity']:,.0f}"
                    )

        if not year_results:
            # データなし時のフォールバック
            return {
                "test_name": test_config.name,
                "total_profit": 0.0,
                "annual_return_pct": 0.0,
                "max_dd_pct": 0.0,
                "sharpe": 0.0,
                "wr": 0.0,
                "pf": 0.0,
                "monthly_wr": 0.0,
                "total_trades": 0,
                "pair_metrics": {},
                "yearly_results": [],
                "monthly_pnl": {},
                "blocked_global": 0,
                "blocked_per_pair": 0,
                "blocked_exposure": 0,
                "final_equity": INITIAL_EQUITY,
            }

        result = aggregate_year_results(
            test_config.name,
            year_results,
            symbols,
            num_years,
        )
        _print_result_summary(result)
        return result

    # --- 順次実行（年独立エクイティリセット） ---
    all_pair_trades: dict[str, list[Any]] = {
        sym: [] for sym in symbols
    }
    yearly_results_seq: list[dict[str, Any]] = []
    all_monthly_pnl: dict[tuple[int, int], float] = {}
    year_return_pcts: list[float] = []
    max_dd_across_years = 0.0
    total_blocked_global = 0
    total_blocked_per_pair = 0
    total_blocked_exposure = 0

    for year in range(start_year, end_year + 1):
        # 毎年エクイティを初期残高にリセット
        portfolio = PortfolioState(
            equity=INITIAL_EQUITY,
            initial_equity=INITIAL_EQUITY,
            peak_equity=INITIAL_EQUITY,
        )

        # ペアコンテキスト構築
        contexts: dict[str, PairContext] = {}
        for sym in symbols:
            runner, full_md = runners[sym]
            # ペア別bot_config構築
            extra_overrides: dict[str, Any] = {
                "base_risk_pct": test_config.base_risk_pct,
                "consensus_threshold": (
                    test_config.consensus_threshold
                ),
            }
            extra_overrides.update(_extra)
            bot_config = build_bot_config(
                sym, extra_overrides,
            )

            ctx = setup_pair_context(
                sym, runner, year, bot_config,
                INITIAL_EQUITY,
                full_market_data=full_md,
            )
            if ctx is not None:
                contexts[sym] = ctx

        if not contexts:
            continue

        # インターリーブ実行
        pair_trades = run_multi_pair_year(
            year, contexts, test_config, portfolio,
        )

        # トレード蓄積
        for sym, trades in pair_trades.items():
            all_pair_trades[sym].extend(trades)

        # 年次サマリー
        year_pnl = portfolio.equity - INITIAL_EQUITY
        year_return_pct = year_pnl / INITIAL_EQUITY * 100
        year_return_pcts.append(year_return_pct)
        year_trades = sum(
            len(t) for t in pair_trades.values()
        )

        # 月次PnL蓄積
        for key, pnl in portfolio.monthly_pnl.items():
            all_monthly_pnl[key] = (
                all_monthly_pnl.get(key, 0.0) + pnl
            )

        # DD/blocked蓄積
        if portfolio.max_dd_pct > max_dd_across_years:
            max_dd_across_years = portfolio.max_dd_pct
        total_blocked_global += portfolio.blocked_global
        total_blocked_per_pair += portfolio.blocked_per_pair
        total_blocked_exposure += portfolio.blocked_exposure

        yearly_results_seq.append(
            {
                "year": year,
                "pnl": year_pnl,
                "trades": year_trades,
                "equity": portfolio.equity,
                "return_pct": year_return_pct,
            }
        )
        print(
            f"  {year}年: "
            f"PnL={year_pnl:+,.0f}, "
            f"Return={year_return_pct:+.1f}%, "
            f"Trades={year_trades}, "
            f"Equity={portfolio.equity:,.0f}"
        )

        # メモリ解放（年間コンテキストは次年で再構築）
        del contexts
        gc.collect()

    # 結果集約（年独立モード）
    total_profit = sum(yr["pnl"] for yr in yearly_results_seq)
    avg_annual_return = (
        sum(year_return_pcts) / len(year_return_pcts)
        if year_return_pcts
        else 0.0
    )

    # 合成PortfolioState（集約用）
    agg_portfolio = PortfolioState(
        equity=INITIAL_EQUITY + total_profit,
        initial_equity=INITIAL_EQUITY,
        peak_equity=INITIAL_EQUITY + total_profit,
        max_dd_pct=max_dd_across_years,
        blocked_global=total_blocked_global,
        blocked_per_pair=total_blocked_per_pair,
        blocked_exposure=total_blocked_exposure,
    )
    agg_portfolio.monthly_pnl = all_monthly_pnl

    result = aggregate_results(
        test_config.name,
        agg_portfolio,
        all_pair_trades,
        yearly_results_seq,
        symbols,
        num_years,
    )
    # 年間収益率を平均値で上書き
    result["annual_return_pct"] = avg_annual_return
    _print_result_summary(result)
    return result


# =============================================================
# 結果集約
# =============================================================
def aggregate_results(
    test_name: str,
    portfolio: PortfolioState,
    pair_trades: dict[str, list[Any]],
    yearly_results: list[dict[str, Any]],
    symbols: list[str],
    num_years: int,
) -> dict[str, Any]:
    """テスト結果を集約

    Args:
        test_name: テスト名
        portfolio: ポートフォリオ状態
        pair_trades: ペア別トレードリスト
        yearly_results: 年次結果
        symbols: シンボルリスト
        num_years: 年数

    Returns:
        dict: 集約結果
    """
    total_profit = portfolio.equity - portfolio.initial_equity

    # 月次PnL
    sorted_months = sorted(portfolio.monthly_pnl.keys())
    monthly_pnl_list = [
        portfolio.monthly_pnl[m] for m in sorted_months
    ]

    # DD計算（バーレベルDDと月次DDの大きい方）
    equity = portfolio.initial_equity
    peak = equity
    max_dd_monthly = 0.0
    for pnl in monthly_pnl_list:
        equity += pnl
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak * 100 if peak > 0 else 0.0
        if dd > max_dd_monthly:
            max_dd_monthly = dd
    # バーレベルDDと月次DDの大きい方を採用
    max_dd = max(max_dd_monthly, portfolio.max_dd_pct)

    # Sharpe
    if len(monthly_pnl_list) > 1:
        arr = np.array(monthly_pnl_list)
        mean_m = np.mean(arr)
        std_m = np.std(arr, ddof=1)
        sharpe = (
            (mean_m / std_m) * math.sqrt(12) if std_m > 0 else 0.0
        )
    else:
        sharpe = 0.0

    # WR/PF
    total_trades = 0
    total_wins = 0
    total_gp = 0.0
    total_gl = 0.0
    pair_metrics: dict[str, dict[str, Any]] = {}

    for sym in symbols:
        trades = pair_trades.get(sym, [])
        n_trades = len(trades)
        wins = sum(1 for t in trades if (t.profit_loss or 0) > 0)
        gp = sum(
            t.profit_loss for t in trades if (t.profit_loss or 0) > 0
        )
        gl = abs(
            sum(
                t.profit_loss
                for t in trades
                if (t.profit_loss or 0) <= 0
            )
        )
        np_ = sum(t.profit_loss or 0 for t in trades)

        total_trades += n_trades
        total_wins += wins
        total_gp += gp
        total_gl += gl

        wr = wins / n_trades * 100 if n_trades > 0 else 0.0
        pf = gp / gl if gl > 0 else float("inf")

        pair_metrics[sym] = {
            "trades": n_trades,
            "profit": np_,
            "wr": wr,
            "pf": pf,
            "contribution": 0.0,  # 後で計算
        }

    # 寄与率
    for sym in symbols:
        pm = pair_metrics[sym]
        pm["contribution"] = (
            pm["profit"] / total_profit * 100
            if total_profit > 0
            else 0.0
        )

    wr = total_wins / total_trades * 100 if total_trades > 0 else 0.0
    pf = total_gp / total_gl if total_gl > 0 else float("inf")

    # 月間勝率
    winning_months = sum(1 for p in monthly_pnl_list if p > 0)
    monthly_wr = (
        winning_months / len(monthly_pnl_list) * 100
        if monthly_pnl_list
        else 0.0
    )

    # 年間収益率
    annual_return = (
        (total_profit / portfolio.initial_equity)
        / num_years
        * 100
        if num_years > 0
        else 0.0
    )

    return {
        "test_name": test_name,
        "total_profit": total_profit,
        "annual_return_pct": annual_return,
        "max_dd_pct": max_dd,
        "sharpe": sharpe,
        "wr": wr,
        "pf": pf,
        "monthly_wr": monthly_wr,
        "total_trades": total_trades,
        "pair_metrics": pair_metrics,
        "yearly_results": yearly_results,
        "monthly_pnl": portfolio.monthly_pnl,
        "blocked_global": portfolio.blocked_global,
        "blocked_per_pair": portfolio.blocked_per_pair,
        "blocked_exposure": portfolio.blocked_exposure,
        "final_equity": portfolio.equity,
    }


def _print_result_summary(result: dict[str, Any]) -> None:
    """テスト結果サマリー出力"""
    print(f"\n  --- {result['test_name']} サマリー ---")
    print(f"  総利益:     {result['total_profit']:>+12,.0f}")
    print(f"  年間収益率: {result['annual_return_pct']:>8.1f}%")
    print(f"  最大DD:     {result['max_dd_pct']:>8.2f}%")
    print(f"  Sharpe:     {result['sharpe']:>8.2f}")
    print(f"  WR:         {result['wr']:>8.1f}%")
    print(f"  PF:         {result['pf']:>8.2f}")
    print(f"  月間勝率:   {result['monthly_wr']:>8.1f}%")
    print(f"  トレード数: {result['total_trades']}")
    print(
        f"  制限発動: global={result['blocked_global']}, "
        f"per_pair={result['blocked_per_pair']}, "
        f"exposure={result['blocked_exposure']}"
    )

    print("\n  ペア別:")
    for sym, pm in result["pair_metrics"].items():
        if pm["trades"] > 0:
            print(
                f"    {sym:8s} | "
                f"Profit: {pm['profit']:>+10,.0f} | "
                f"WR: {pm['wr']:5.1f}% | "
                f"PF: {pm['pf']:5.2f} | "
                f"Trades: {pm['trades']:4d} | "
                f"寄与: {pm['contribution']:5.1f}%"
            )


# =============================================================
# テストマトリクス定義
# =============================================================
TEST_MATRIX: dict[str, MultiPairConfig] = {
    # 推奨設定（JPYポートフォリオ検証結果）
    "R1": MultiPairConfig(
        name="R1",
        global_max_positions=6,
        per_pair_max_positions=1,
        global_max_exposure_lot=10.0,
        base_risk_pct=0.015,
        consensus_threshold=9.5,
    ),
    "M0": MultiPairConfig(
        name="M0",
        global_max_positions=6,
        per_pair_max_positions=1,
        global_max_exposure_lot=10.0,
        base_risk_pct=0.02,
        consensus_threshold=9.0,
    ),
    "M1": MultiPairConfig(
        name="M1",
        global_max_positions=6,
        per_pair_max_positions=1,
        global_max_exposure_lot=10.0,
        base_risk_pct=0.02,
        consensus_threshold=10.0,
    ),
    "M2": MultiPairConfig(
        name="M2",
        global_max_positions=6,
        per_pair_max_positions=1,
        global_max_exposure_lot=10.0,
        base_risk_pct=0.02,
        consensus_threshold=11.0,
    ),
    "M3": MultiPairConfig(
        name="M3",
        global_max_positions=6,
        per_pair_max_positions=1,
        global_max_exposure_lot=10.0,
        base_risk_pct=0.02,
        consensus_threshold=12.0,
    ),
    "M4": MultiPairConfig(
        name="M4",
        global_max_positions=6,
        per_pair_max_positions=2,
        global_max_exposure_lot=10.0,
        base_risk_pct=0.015,
        consensus_threshold=11.0,
    ),
    "M5": MultiPairConfig(
        name="M5",
        global_max_positions=8,
        per_pair_max_positions=2,
        global_max_exposure_lot=12.0,
        base_risk_pct=0.015,
        consensus_threshold=10.0,
    ),
    "M6": MultiPairConfig(
        name="M6",
        global_max_positions=4,
        per_pair_max_positions=1,
        global_max_exposure_lot=6.0,
        base_risk_pct=0.03,
        consensus_threshold=12.0,
    ),
    # --- CT微調整テスト（年間70%目標）---
    "T1": MultiPairConfig(
        name="T1",
        global_max_positions=6,
        per_pair_max_positions=1,
        global_max_exposure_lot=10.0,
        base_risk_pct=0.02,
        consensus_threshold=10.0,
    ),
    "T2": MultiPairConfig(
        name="T2",
        global_max_positions=6,
        per_pair_max_positions=1,
        global_max_exposure_lot=10.0,
        base_risk_pct=0.02,
        consensus_threshold=10.5,
    ),
    "T3": MultiPairConfig(
        name="T3",
        global_max_positions=6,
        per_pair_max_positions=1,
        global_max_exposure_lot=10.0,
        base_risk_pct=0.02,
        consensus_threshold=11.0,
    ),
    "T4": MultiPairConfig(
        name="T4",
        global_max_positions=6,
        per_pair_max_positions=1,
        global_max_exposure_lot=10.0,
        base_risk_pct=0.015,
        consensus_threshold=10.0,
    ),
    "T5": MultiPairConfig(
        name="T5",
        global_max_positions=6,
        per_pair_max_positions=1,
        global_max_exposure_lot=10.0,
        base_risk_pct=0.015,
        consensus_threshold=10.5,
    ),
}


# =============================================================
# レポート生成
# =============================================================
def generate_report(
    results: dict[str, dict[str, Any]],
    symbols: list[str],
    output_path: Path,
) -> None:
    """Markdownレポートを生成

    Args:
        results: テスト名→結果辞書
        symbols: シンボルリスト
        output_path: 出力パス
    """
    lines: list[str] = []
    lines.append(
        "# マルチ通貨ペアバックテスト（時系列インターリーブ方式）\n"
    )
    lines.append(
        f"検証期間: {START_YEAR}-{END_YEAR} "
        f"({END_YEAR - START_YEAR + 1}年)\n"
    )
    lines.append(f"対象ペア: {', '.join(symbols)}\n")
    lines.append(f"初期残高: {INITIAL_EQUITY:,.0f}\n")
    lines.append("")

    # サマリーテーブル
    lines.append("## テスト結果サマリー\n")
    lines.append(
        "| テスト | 総利益 | 年間収益率 | DD | Sharpe "
        "| WR | PF | 月間+ | Trades |"
    )
    lines.append(
        "|--------|--------|-----------|-----|--------"
        "|-----|-----|------|--------|"
    )
    for name, r in results.items():
        lines.append(
            f"| {name} | "
            f"{r['total_profit']:+,.0f} | "
            f"{r['annual_return_pct']:.1f}% | "
            f"{r['max_dd_pct']:.2f}% | "
            f"{r['sharpe']:.2f} | "
            f"{r['wr']:.1f}% | "
            f"{r['pf']:.2f} | "
            f"{r['monthly_wr']:.1f}% | "
            f"{r['total_trades']} |"
        )
    lines.append("")

    # グローバル制限発動統計
    lines.append("## グローバル制限発動統計\n")
    lines.append(
        "| テスト | global制限 | per_pair制限 | exposure制限 |"
    )
    lines.append("|--------|-----------|-------------|-------------|")
    for name, r in results.items():
        lines.append(
            f"| {name} | "
            f"{r['blocked_global']} | "
            f"{r['blocked_per_pair']} | "
            f"{r['blocked_exposure']} |"
        )
    lines.append("")

    # ペア別内訳（各テスト）
    for name, r in results.items():
        lines.append(f"## {name} ペア別内訳\n")
        lines.append(
            "| ペア | 利益 | WR | PF | Trades | 寄与率 |"
        )
        lines.append(
            "|------|------|-----|-----|--------|--------|"
        )
        for sym in symbols:
            pm = r["pair_metrics"].get(sym, {})
            if pm.get("trades", 0) > 0:
                lines.append(
                    f"| {sym} | "
                    f"{pm['profit']:+,.0f} | "
                    f"{pm['wr']:.1f}% | "
                    f"{pm['pf']:.2f} | "
                    f"{pm['trades']} | "
                    f"{pm['contribution']:.1f}% |"
                )
        lines.append("")

    # 年次推移（最初のテスト）
    first = next(iter(results.values()), None)
    if first and first.get("yearly_results"):
        lines.append("## 年次推移（最初のテスト）\n")
        lines.append(
            "| 年 | PnL | Return | Trades | Equity |"
        )
        lines.append(
            "|----|-----|--------|--------|--------|"
        )
        for yr in first["yearly_results"]:
            ret = yr.get("return_pct", 0.0)
            lines.append(
                f"| {yr['year']} | "
                f"{yr['pnl']:+,.0f} | "
                f"{ret:+.1f}% | "
                f"{yr['trades']} | "
                f"{yr['equity']:,.0f} |"
            )
        lines.append("")

    # 推奨設定
    lines.append("## 推奨設定\n")
    best_name = ""
    best_score = -999.0
    for name, r in results.items():
        if r["max_dd_pct"] < 5.0 and r["wr"] > 65.0:
            score = r["sharpe"]
            if score > best_score:
                best_score = score
                best_name = name

    if best_name:
        br = results[best_name]
        lines.append(f"推奨テスト: **{best_name}**\n")
        lines.append(f"- 年間収益率: {br['annual_return_pct']:.1f}%")
        lines.append(f"- DD: {br['max_dd_pct']:.2f}%")
        lines.append(f"- Sharpe: {br['sharpe']:.2f}")
        lines.append(f"- WR: {br['wr']:.1f}%")
        lines.append(f"- PF: {br['pf']:.2f}")
    else:
        lines.append(
            "WR>65%かつDD<5%を満たすテストなし。設定調整が必要。\n"
        )

    lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"\nレポート出力: {output_path}")


def save_trades_csv(
    results: dict[str, dict[str, Any]],
    log_dir: Path,
) -> None:
    """全テストのトレードを統合CSVに出力

    Args:
        results: テスト結果辞書
        log_dir: ログディレクトリ
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary_path = log_dir / f"summary_{timestamp}.log"

    # サマリーログ
    lines: list[str] = []
    lines.append("=== マルチ通貨ペアバックテスト サマリー ===\n")
    for name, r in results.items():
        lines.append(f"--- {name} ---")
        lines.append(f"総利益: {r['total_profit']:+,.0f}")
        lines.append(f"年間収益率: {r['annual_return_pct']:.1f}%")
        lines.append(f"最大DD: {r['max_dd_pct']:.2f}%")
        lines.append(f"Sharpe: {r['sharpe']:.2f}")
        lines.append(f"WR: {r['wr']:.1f}%")
        lines.append(f"PF: {r['pf']:.2f}")
        lines.append(f"月間勝率: {r['monthly_wr']:.1f}%")
        lines.append(f"Trades: {r['total_trades']}")
        lines.append(
            f"制限発動: global={r['blocked_global']}, "
            f"per_pair={r['blocked_per_pair']}, "
            f"exposure={r['blocked_exposure']}"
        )
        lines.append("")

    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"サマリーログ: {summary_path}")


# =============================================================
# メイン
# =============================================================
def find_available_symbols(data_dir: str) -> list[str]:
    """データが存在するシンボルを検出

    Args:
        data_dir: データディレクトリ

    Returns:
        list[str]: 利用可能シンボルリスト
    """
    available = []
    base = Path(data_dir)
    for sym in SYMBOLS:
        sym_dir = base / sym
        if sym_dir.exists() and any(sym_dir.iterdir()):
            available.append(sym)
    return available


def main() -> None:
    """メインエントリーポイント"""
    parser = argparse.ArgumentParser(
        description="マルチ通貨ペア同時実行バックテスト"
        "（時系列インターリーブ方式）",
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help="データディレクトリ（省略時は自動解決）",
    )
    parser.add_argument(
        "--tests",
        default="R1",
        help="テストケース（カンマ区切り、例: R1,M0,M1 or all）",
    )
    parser.add_argument(
        "--symbols",
        default=None,
        help="対象シンボル（カンマ区切り、省略時は自動検出）",
    )
    parser.add_argument(
        "--output",
        default="reports/multi_pair_backtest.md",
        help="レポート出力先",
    )
    parser.add_argument(
        "--start-year",
        type=int,
        default=START_YEAR,
        help="開始年",
    )
    parser.add_argument(
        "--end-year",
        type=int,
        default=END_YEAR,
        help="終了年",
    )
    parser.add_argument(
        "--max-year-workers",
        type=int,
        default=1,
        help="年並列ワーカー数（1=順次, 0=年数から自動）",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s: %(message)s",
    )

    # データディレクトリ解決
    data_dir = args.data_dir or get_data_dir()

    # シンボル検出
    if args.symbols:
        available = [s.strip() for s in args.symbols.split(",")]
    else:
        available = find_available_symbols(data_dir)

    if not available:
        print(
            f"エラー: {data_dir} にデータが見つかりません。"
            f"\n対象: {', '.join(SYMBOLS)}"
        )
        sys.exit(1)

    print(f"検出ペア: {', '.join(available)}")
    missing = set(SYMBOLS) - set(available)
    if missing:
        print(f"データなし（スキップ）: {', '.join(sorted(missing))}")

    # テストケース選択
    if args.tests.lower() == "all":
        test_names = list(TEST_MATRIX.keys())
    else:
        test_names = [t.strip() for t in args.tests.split(",")]

    # 年並列ワーカー数の決定
    num_years = args.end_year - args.start_year + 1
    max_year_workers = args.max_year_workers
    if max_year_workers == 0:
        max_year_workers = num_years
    is_parallel = max_year_workers > 1

    if is_parallel:
        print(f"\n年並列モード: {max_year_workers}ワーカー")

    # データロード（順次実行時のみ事前ロード）
    runners: dict[
        str, tuple[BacktestRunner, dict[str, pd.DataFrame]]
    ] = {}
    if not is_parallel:
        runners = load_all_pair_data(available, data_dir)
    else:
        print(
            "\n各ワーカーがデータを独自にロードします。"
        )

    # テスト実行
    results: dict[str, dict[str, Any]] = {}
    for test_name in test_names:
        if test_name not in TEST_MATRIX:
            print(f"警告: 不明なテスト '{test_name}' をスキップ")
            continue

        test_config = TEST_MATRIX[test_name]
        result = run_test_case(
            test_config,
            runners,
            available,
            start_year=args.start_year,
            end_year=args.end_year,
            max_year_workers=max_year_workers,
            data_dir=data_dir,
        )
        results[test_name] = result

    if not results:
        print("テスト結果なし。")
        sys.exit(1)

    # レポート生成
    output_path = Path(args.output)
    generate_report(results, available, output_path)

    # ログ出力
    log_dir = Path(get_log_dir()) / "multi_pair"
    save_trades_csv(results, log_dir)

    # 最終サマリー
    print(f"\n{'=' * 80}")
    print("  最終サマリー")
    print(f"{'=' * 80}")
    print(
        f"{'テスト':8s} | {'年間収益率':>10s} | {'DD':>6s} | "
        f"{'Sharpe':>7s} | {'WR':>6s} | {'PF':>6s} | "
        f"{'月間+':>6s} | {'Trades':>7s}"
    )
    print("-" * 80)
    for name, r in results.items():
        print(
            f"{name:8s} | "
            f"{r['annual_return_pct']:>9.1f}% | "
            f"{r['max_dd_pct']:>5.2f}% | "
            f"{r['sharpe']:>7.2f} | "
            f"{r['wr']:>5.1f}% | "
            f"{r['pf']:>5.2f} | "
            f"{r['monthly_wr']:>5.1f}% | "
            f"{r['total_trades']:>7d}"
        )


if __name__ == "__main__":
    main()
