"""BT vs リアル直接比較

エクスポートされたMT5データでBTを実行し、
リアルトレードと1件ずつ比較する。
"""

from __future__ import annotations

import sqlite3
import sys
import warnings
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
warnings.filterwarnings("ignore")

import logging

logging.disable(logging.WARNING)

from autotrader.calculator.precompute import PrecomputeEngine
from autotrader.core.enums import Timeframe
from autotrader.decision.unified.config import UnifiedBotConfig
from autotrader.decision.unified.trade_bot import UnifiedTradeBot


def load_real_trades(db_path: str, start: str, end: str) -> pd.DataFrame:
    conn = sqlite3.connect(db_path)
    query = f"""
        SELECT trade_id, symbol, signal_type, volume,
               entry_price, exit_price, stop_loss, take_profit,
               profit_loss, profit_loss_pips, exit_reason,
               entry_own_score, opened_at, closed_at
        FROM trades
        WHERE is_open = 0
          AND opened_at >= '{start}'
          AND opened_at < '{end}'
        ORDER BY opened_at
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    df["opened_at"] = pd.to_datetime(df["opened_at"])
    df["closed_at"] = pd.to_datetime(df["closed_at"])
    return df


def load_mt5_data(
    export_dir: Path, symbol: str
) -> dict[str, pd.DataFrame]:
    """エクスポートデータ + 既存長期データを結合して読み込み

    既存データ(~2025末) + MT5エクスポート(2026-04) を結合し、
    インジケータの完全なウォームアップを確保する。
    """
    data = {}
    engine = PrecomputeEngine()

    # 既存長期データのパス
    hist_base = Path(
        "/home/yamas/projects/AutoTraderV4_data/data"
    ) / symbol / "chart" / "cache"

    for tf_name in ["M1", "M5", "M15", "M30", "H1", "H4", "H8", "D1"]:
        # 1. MT5エクスポートデータ
        pq = export_dir / f"{symbol}_{tf_name}.parquet"
        if not pq.exists():
            continue
        new_df = pd.read_parquet(pq)
        if "time" in new_df.columns:
            new_df["time"] = pd.to_datetime(new_df["time"])
            new_df = new_df.set_index("time")

        # 2. 既存長期データ（ウォームアップ用）
        # D1はファイル名が "Daily" の場合がある
        hist_pq = hist_base / f"{symbol}_{tf_name}.parquet"
        if not hist_pq.exists() and tf_name == "D1":
            hist_pq = hist_base / f"{symbol}_Daily.parquet"
        if hist_pq.exists():
            hist_df = pd.read_parquet(hist_pq)
            if "time" in hist_df.columns:
                hist_df["time"] = pd.to_datetime(hist_df["time"])
                hist_df = hist_df.set_index("time")

            # 直近1年分 + 新データを結合
            cutoff = new_df.index.min() - pd.DateOffset(years=1)
            hist_tail = hist_df[hist_df.index >= cutoff]

            # 共通カラムで結合
            common_cols = list(
                set(hist_tail.columns) & set(new_df.columns)
            )
            combined = pd.concat(
                [hist_tail[common_cols], new_df[common_cols]]
            )
            combined = combined[
                ~combined.index.duplicated(keep="last")
            ].sort_index()
        else:
            combined = new_df

        # 3. 事前計算
        try:
            tf = Timeframe(tf_name)
            combined = engine.precompute(
                combined, symbol, tf, use_cache=False
            )
            data[tf_name] = combined
        except Exception as e:
            print(f"  !! {symbol} {tf_name}: precompute error: {e}")

    return data


def replay_bot(
    symbol: str,
    market_data: dict[str, pd.DataFrame],
    config: UnifiedBotConfig,
) -> list[dict]:
    """BotをリプレイしてBTシグナルを記録"""
    bot = UnifiedTradeBot(config=config)

    # market_data をbotに設定
    bot._market_data = {}
    bot._time_arrays = {}
    bot._current_indices = {}

    import numpy as np

    for tf, df in market_data.items():
        bot._market_data[tf] = df
        if hasattr(df.index, "values"):
            bot._time_arrays[tf] = df.index.values.astype(
                "datetime64[ns]"
            )
        bot._current_indices[tf] = 0

    # M15バーを基準にイテレート（ライブと同じ頻度）
    iterate_tf = "M15" if "M15" in market_data else "H1"
    iterate_df = market_data[iterate_tf]

    signals = []
    for ts in iterate_df.index:
        current_time = pd.Timestamp(ts)

        try:
            signal = bot.generate_signal(current_time)
        except Exception:
            continue

        if signal.direction.value != "HOLD":
            signals.append(
                {
                    "time": str(current_time),
                    "direction": signal.direction.value,
                    "confidence": signal.confidence,
                    "sl_pips": signal.sl_pips,
                    "tp_pips": signal.tp_pips,
                    "mode": getattr(signal, "mode", ""),
                    "score": getattr(signal, "consensus_score", 0),
                    "rationale": (
                        signal.rationale[:80]
                        if signal.rationale
                        else ""
                    ),
                }
            )

    return signals


def main() -> None:
    db_path = "/mnt/d/Projects/AutoTraderV4/data/autotrader.db"
    export_dir = Path(
        "/mnt/d/Projects/AutoTraderV4_data/tmp/mt5_export"
    )

    # 1. リアルトレード読み込み
    real = load_real_trades(db_path, "2026-04-20", "2026-04-26")
    print(f"リアルトレード: {len(real)}件")
    print(
        f"  期間: {real['opened_at'].min()} → {real['opened_at'].max()}"
    )
    print(f"  合計PnL: {real['profit_loss'].sum():+,.0f}")
    print(f"  通貨: {sorted(real['symbol'].unique())}")

    # 2. 通貨ペア別にBTリプレイ
    config = UnifiedBotConfig()  # デフォルト設定（ライブと同じ想定）

    print("\n" + "=" * 80)
    print("SYMBOL-BY-SYMBOL COMPARISON")
    print("=" * 80)

    all_bt_signals = []

    for symbol in sorted(real["symbol"].unique()):
        sym_real = real[real["symbol"] == symbol]
        print(f"\n--- {symbol} ---")
        print(
            f"  Real: {len(sym_real)}件, "
            f"PnL={sym_real['profit_loss'].sum():+,.0f}"
        )

        # データ読み込み
        data = load_mt5_data(export_dir, symbol)
        if not data:
            print(f"  !! No data for {symbol}")
            continue

        tfs = list(data.keys())
        print(f"  TFs loaded: {tfs}")

        # BTリプレイ
        bt_signals = replay_bot(symbol, data, config)
        all_bt_signals.extend(
            [{**s, "symbol": symbol} for s in bt_signals]
        )
        print(f"  BT signals: {len(bt_signals)}件")

        # 時間帯の一致を確認
        for _, trade in sym_real.iterrows():
            open_time = trade["opened_at"]
            direction = trade["signal_type"]

            # BT信号で同日の同方向を検索
            matches = [
                s
                for s in bt_signals
                if abs(
                    (
                        pd.Timestamp(s["time"]) - open_time
                    ).total_seconds()
                )
                < 7200  # 2時間以内
                and s["direction"] == direction
            ]

            if matches:
                m = matches[0]
                time_diff = (
                    pd.Timestamp(m["time"]) - open_time
                ).total_seconds() / 60
                print(
                    f"  {str(open_time)[11:16]} {direction:<4} "
                    f"score={trade['entry_own_score']:.1f} "
                    f"pnl={trade['profit_loss']:>+8,.0f} "
                    f"→ BT match at {m['time'][11:16]} "
                    f"(Δ{time_diff:+.0f}min) "
                    f"bt_score={m['score']:.1f}"
                )
            else:
                print(
                    f"  {str(open_time)[11:16]} {direction:<4} "
                    f"score={trade['entry_own_score']:.1f} "
                    f"pnl={trade['profit_loss']:>+8,.0f} "
                    f"→ !! NO BT MATCH"
                )

    # 3. サマリー
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Real trades: {len(real)}")
    print(f"BT signals:  {len(all_bt_signals)}")

    # BT signals per symbol
    bt_df = pd.DataFrame(all_bt_signals) if all_bt_signals else pd.DataFrame()
    if not bt_df.empty:
        for sym in sorted(real["symbol"].unique()):
            sym_bt = bt_df[bt_df["symbol"] == sym]
            sym_real_count = len(real[real["symbol"] == sym])
            print(
                f"  {sym}: Real={sym_real_count} BT={len(sym_bt)}"
            )


if __name__ == "__main__":
    main()
