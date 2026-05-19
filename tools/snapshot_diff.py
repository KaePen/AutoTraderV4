"""Live vs BT の同一時刻スナップショット比較

ライブ engine と BT runner は同じ UnifiedTradeBot ロジックを通すはずだが、
入力 OHLCV のソース (MT5 直接 vs monthly_cache parquet) や、
最終バーの tick mid 上書き / 前処理 (set_index, tz) の有無で差が出る可能性がある。

このスクリプトは指定時刻 T で両経路を再現し、generate_signal の結果と
最終バーの主要 indicator を並列比較して JSON に dump する。

Usage:
    python tools/snapshot_diff.py --symbol AUDJPY --at "2026-05-20 09:00:00"
    python tools/snapshot_diff.py --symbol AUDJPY --now
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autotrader.adapters.mt5.converters import mt5_rates_to_dataframe
from autotrader.backtest.live_replay import _load_market_data_up_to
from autotrader.calculator.precompute import PrecomputeConfig, PrecomputeEngine
from autotrader.config.config_loader import ConfigLoader
from autotrader.config.paths import get_data_dir
from autotrader.config.trading_params import get_pip_unit
from autotrader.core.enums import Timeframe
from autotrader.decision.unified import UnifiedTradeBot

logger = logging.getLogger("snapshot_diff")

ALL_TFS = ["M1", "M5", "M15", "M30", "H1", "H4", "H8", "D1"]

KEY_INDICATORS = [
    "open", "high", "low", "close",
    "sma_20", "ema_50", "ema_200",
    "rsi_14", "atr_14", "macd", "macd_signal",
    "bb_upper", "bb_lower",
    "stoch_k", "stoch_d",
    "adx",
]


def _mt5_tf_map() -> dict[str, int]:
    import MetaTrader5 as mt5
    return {
        "M1": mt5.TIMEFRAME_M1,
        "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30,
        "H1": mt5.TIMEFRAME_H1,
        "H4": mt5.TIMEFRAME_H4,
        "H8": mt5.TIMEFRAME_H8,
        "D1": mt5.TIMEFRAME_D1,
    }


def build_live_market_data(
    symbol: str,
    lookback: int,
    overwrite_with_tick: bool = True,
) -> tuple[dict[str, pd.DataFrame], dict | None, pd.Timestamp]:
    """ライブ engine と同手順で market_data 構築 (現在時刻基準)

    1. MT5 から copy_rates_from_pos で各TFの最新 lookback 本取得
       (ライブ engine の get_candles_from_pos と同 API)
    2. DatetimeIndex化
    3. 最終バーを最新 tick の mid で overwrite (close/high/low)
    4. PrecomputeEngine.precompute(use_cache=False) で指標再計算

    Returns:
        (market_data, tick_info, server_at)
        server_at は MT5 サーバ時刻 (= monthly_cache index と同系)
    """
    import MetaTrader5 as mt5

    if not mt5.initialize():
        raise RuntimeError(f"MT5 init failed: {mt5.last_error()}")

    try:
        if not mt5.symbol_select(symbol, True):
            raise RuntimeError(
                f"symbol_select failed: {mt5.last_error()}"
            )
        tf_map = _mt5_tf_map()

        # MT5 サーバ時刻を tick から取得 (monthly_cache の時刻系と整合)
        tick_now = mt5.symbol_info_tick(symbol)
        if tick_now is None:
            raise RuntimeError("symbol_info_tick failed")
        server_at = pd.to_datetime(tick_now.time, unit="s").floor("1min")
        logger.info(
            "MT5 server time (now) = %s (Unix=%d)",
            server_at, tick_now.time,
        )

        raw: dict[str, pd.DataFrame] = {}
        for tf_str in ALL_TFS:
            mt5_tf = tf_map[tf_str]
            rates = mt5.copy_rates_from_pos(
                symbol, mt5_tf, 0, lookback
            )
            if rates is None or len(rates) == 0:
                logger.warning("MT5 no rates: %s %s", symbol, tf_str)
                continue
            # ライブ engine の DirectTransport.copy_rates_from_pos と同じ
            # numpy structured array → list[dict] 変換
            rates_list = [
                dict(zip(rates.dtype.names, r)) for r in rates
            ]
            # ライブ engine と完全同じ変換 (utc=True, 6カラムのみ)
            df = mt5_rates_to_dataframe(rates_list)
            df = df.set_index("time")
            raw[tf_str] = df
            logger.info(
                "Live MT5 %s %s: %d bars [%s ~ %s]",
                symbol, tf_str, len(df),
                df.index[0], df.index[-1],
            )

        tick_info: dict | None = None
        if overwrite_with_tick:
            bid = float(tick_now.bid)
            ask = float(tick_now.ask)
            if bid > 0 and ask > 0:
                mid = (bid + ask) / 2.0
                tick_info = {"bid": bid, "ask": ask, "mid": mid}
                logger.info(
                    "Live tick: bid=%.5f ask=%.5f mid=%.5f",
                    bid, ask, mid,
                )
                for tf_str, df in list(raw.items()):
                    if df.empty:
                        continue
                    d = df.copy()
                    idx = d.index[-1]
                    d.at[idx, "close"] = mid
                    if mid > float(d.at[idx, "high"]):
                        d.at[idx, "high"] = mid
                    if mid < float(d.at[idx, "low"]):
                        d.at[idx, "low"] = mid
                    raw[tf_str] = d
    finally:
        mt5.shutdown()

    engine = PrecomputeEngine()
    out: dict[str, pd.DataFrame] = {}
    for tf_str, df in raw.items():
        try:
            tf = Timeframe(tf_str)
            out[tf_str] = engine.precompute(
                df, symbol, tf, use_cache=False
            )
        except Exception as e:
            logger.warning(
                "precompute failed %s %s: %s",
                symbol, tf_str, e,
            )
            out[tf_str] = df

    return out, tick_info, server_at


def build_bt_market_data(
    symbol: str,
    at: pd.Timestamp,
) -> dict[str, pd.DataFrame]:
    """BT 経路: monthly_cache から市場データを構築 (precompute 済み)"""
    md = _load_market_data_up_to(symbol, at, Path(get_data_dir()))
    out: dict[str, pd.DataFrame] = {}
    for tf, df in md.items():
        d = df.copy()
        if "time" in d.columns:
            d = d.set_index("time")
        out[tf] = d
        if not d.empty:
            logger.info(
                "BT cache %s %s: %d bars [%s ~ %s]",
                symbol, tf, len(d),
                d.index[0], d.index[-1],
            )
    return out


def run_bot(
    symbol: str,
    at: pd.Timestamp,
    market_data: dict[str, pd.DataFrame],
    tick: dict | None = None,
):
    loader = ConfigLoader()
    bot_config, _pm = loader.load_preset_config(symbol)
    bot = UnifiedTradeBot(bot_config)
    bot.set_market_data(market_data)
    if tick:
        pip_unit = get_pip_unit(symbol)
        spread_pips = (tick["ask"] - tick["bid"]) / pip_unit
        try:
            bot.set_current_spread_pips(spread_pips)
        except Exception:
            pass

    ct = at
    m1 = market_data.get("M1")
    if m1 is not None and not m1.empty:
        idx_tz = getattr(m1.index, "tz", None)
        if idx_tz is None and ct.tzinfo is not None:
            ct = ct.tz_convert("UTC").tz_localize(None)
        elif idx_tz is not None and ct.tzinfo is None:
            ct = ct.tz_localize(idx_tz)

    return bot.generate_signal(ct)


def summarize_signal(s) -> dict:
    return {
        "direction": str(getattr(s, "direction", None)),
        "consensus_score": getattr(s, "consensus_score", None),
        "confidence": getattr(s, "confidence", None),
        "primary_tf": getattr(s, "primary_tf", None),
        "aligned_tfs": list(getattr(s, "aligned_tfs", []) or []),
        "regime": getattr(s, "regime", None),
        "mode": getattr(s, "mode", None),
        "strategy_id": getattr(s, "strategy_id", None),
        "entry_threshold": getattr(s, "entry_threshold", None),
        "htf_alignment": getattr(s, "htf_alignment", None),
        "penalty_total": getattr(s, "penalty_total", None),
        "penalty_breakdown": dict(
            getattr(s, "penalty_breakdown", {}) or {}
        ),
        "trend_strength": getattr(s, "trend_strength", None),
        "buy_score": getattr(s, "buy_score", None),
        "sell_score": getattr(s, "sell_score", None),
        "scores": {
            k: float(v) for k, v in
            (getattr(s, "scores", {}) or {}).items()
        },
        "tf_directions": dict(
            getattr(s, "tf_directions", {}) or {}
        ),
        "rationale": getattr(s, "rationale", None),
    }


def indicators_tail(
    market_data: dict[str, pd.DataFrame], n: int = 5,
) -> dict:
    out: dict = {}
    for tf, df in market_data.items():
        if df is None or df.empty:
            continue
        cols = [c for c in KEY_INDICATORS if c in df.columns]
        d = df[cols].tail(n).copy()
        d = d.reset_index()
        out[tf] = {
            "n_rows_total": len(df),
            "n_columns_total": len(df.columns),
            "index_min": str(df.index[0]),
            "index_max": str(df.index[-1]),
            "columns_sample": list(df.columns)[:20],
            "tail": [
                {k: (None if (isinstance(v, float) and math.isnan(v))
                     else str(v))
                 for k, v in row.items()}
                for row in d.to_dict(orient="records")
            ],
        }
    return out


def diff_summary(
    live_sig: dict, bt_sig: dict,
    live_md: dict[str, pd.DataFrame],
    bt_md: dict[str, pd.DataFrame],
) -> dict:
    cs_live = live_sig.get("consensus_score") or 0
    cs_bt = bt_sig.get("consensus_score") or 0
    diff = {
        "consensus_score_live": cs_live,
        "consensus_score_bt": cs_bt,
        "consensus_score_diff": cs_live - cs_bt,
        "direction_match": live_sig.get("direction") == bt_sig.get("direction"),
        "regime_match": live_sig.get("regime") == bt_sig.get("regime"),
        "tf_score_diff": {},
        "indicator_last_bar_diff": {},
    }
    for tf, sc_live in (live_sig.get("scores") or {}).items():
        sc_bt = (bt_sig.get("scores") or {}).get(tf)
        if sc_bt is not None:
            try:
                diff["tf_score_diff"][tf] = float(sc_live) - float(sc_bt)
            except Exception:
                pass

    for tf in ALL_TFS:
        dl = live_md.get(tf)
        db = bt_md.get(tf)
        if dl is None or db is None or dl.empty or db.empty:
            continue
        cols = [
            c for c in KEY_INDICATORS
            if c in dl.columns and c in db.columns
        ]
        row_l = dl.iloc[-1]
        row_b = db.iloc[-1]
        tf_diff = {}
        for c in cols:
            try:
                vl = float(row_l[c])
                vb = float(row_b[c])
                if math.isnan(vl) and math.isnan(vb):
                    continue
                d = vl - vb
                if abs(d) > 1e-9:
                    tf_diff[c] = {
                        "live": vl,
                        "bt": vb,
                        "diff": d,
                    }
            except Exception:
                pass
        if tf_diff:
            diff["indicator_last_bar_diff"][tf] = tf_diff

    return diff


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(
        description="Live vs BT snapshot diff"
    )
    parser.add_argument("--symbol", required=True)
    parser.add_argument(
        "--out", default=None,
        help="出力JSONパス (省略時 reports/snapshot_diff_{sym}_{ts}.json)",
    )
    parser.add_argument(
        "--no-tick-overwrite", action="store_true",
        help="Live経路の tick mid overwrite を無効化",
    )
    args = parser.parse_args()

    lookback = max(500, PrecomputeConfig().min_warmup_bars())
    logger.info(
        "symbol=%s lookback=%d (now mode)",
        args.symbol, lookback,
    )

    logger.info("=== Build Live market_data (MT5 copy_rates_from_pos) ===")
    live_md, tick, at = build_live_market_data(
        args.symbol, lookback,
        overwrite_with_tick=not args.no_tick_overwrite,
    )

    out_path = args.out or (
        f"reports/snapshot_diff_{args.symbol}_"
        f"{at.strftime('%Y%m%dT%H%M')}.json"
    )

    logger.info(
        "=== Build BT market_data (monthly_cache, at=%s) ===",
        at,
    )
    bt_md = build_bt_market_data(args.symbol, at)

    logger.info("=== generate_signal: Live ===")
    live_sig_obj = run_bot(args.symbol, at, live_md, tick=tick)
    logger.info("=== generate_signal: BT ===")
    bt_sig_obj = run_bot(args.symbol, at, bt_md, tick=None)

    live_sig = summarize_signal(live_sig_obj)
    bt_sig = summarize_signal(bt_sig_obj)
    diff = diff_summary(live_sig, bt_sig, live_md, bt_md)

    result = {
        "symbol": args.symbol,
        "at": str(at),
        "lookback": lookback,
        "tick": tick,
        "live": {
            "signal": live_sig,
            "indicators_tail": indicators_tail(live_md),
        },
        "bt": {
            "signal": bt_sig,
            "indicators_tail": indicators_tail(bt_md),
        },
        "diff": diff,
    }

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(
        json.dumps(result, indent=2, default=str, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\n=== Saved: {out_path} ===")
    print(json.dumps(diff, indent=2, default=str, ensure_ascii=False))


if __name__ == "__main__":
    main()
