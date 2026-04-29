"""ピンポイント Live 再現検証

特定時刻でのライブ動作 (1秒間隔評価 + live-tick overwrite) を
BT 環境で再現し、bot.generate_signal が同じシグナルを出すか確認する。

これにより BT-Live 乖離の原因仮説 (評価頻度差) を直接検証する。

Usage:
    from autotrader.backtest.live_replay import replay_live_only_trades
    df = replay_live_only_trades(
        live_only_csv="tmp/match_live_only.csv",
    )
    print(df.to_string())
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

from autotrader.backtest.data_pipeline import load_monthly_cache
from autotrader.config.config_loader import ConfigLoader
from autotrader.config.paths import get_data_dir
from autotrader.config.trading_params import get_pip_unit
from autotrader.core.enums import SignalType
from autotrader.decision.unified import UnifiedTradeBot

logger = logging.getLogger(__name__)

ALL_TFS = ["M1", "M5", "M15", "M30", "H1", "H4", "H8", "D1"]


def _load_market_data_up_to(
    symbol: str,
    snapshot_time: pd.Timestamp,
    data_dir: Path,
    tfs: list[str] = ALL_TFS,
) -> dict[str, pd.DataFrame]:
    """指定時刻時点の market_data を構築

    各 TF の monthly_cache から、snapshot_time の月とその前月分を読み込み、
    snapshot_time を含むバーまでに切り詰める。
    """
    out: dict[str, pd.DataFrame] = {}
    cur_y, cur_m = snapshot_time.year, snapshot_time.month
    prev_y, prev_m = (cur_y, cur_m - 1) if cur_m > 1 else (cur_y - 1, 12)

    for tf in tfs:
        dfs: list[pd.DataFrame] = []
        for y, m in [(prev_y, prev_m), (cur_y, cur_m)]:
            df = load_monthly_cache(symbol, tf, y, m, data_dir)
            if df is not None and not df.empty:
                dfs.append(df)
        if not dfs:
            continue
        combined = pd.concat(dfs).sort_index()
        # snapshot_time 以前のバーまで
        ts = (
            snapshot_time.tz_localize(None)
            if snapshot_time.tzinfo is not None
            else snapshot_time
        )
        if combined.index.tz is not None:
            ts = ts.tz_localize(combined.index.tz)
        combined = combined[combined.index <= ts]
        if not combined.empty:
            # 'time' 列を追加（bot.set_market_data 要件）
            combined = combined.reset_index().rename(
                columns={"index": "time"}
            )
            if "time" not in combined.columns:
                combined.rename(columns={combined.columns[0]: "time"}, inplace=True)
            out[tf] = combined
    return out


def _get_tick_at(
    symbol: str,
    target: pd.Timestamp,
    data_dir: Path,
) -> dict[str, float] | None:
    """指定時刻直前の tick (bid/ask/mid) を取得"""
    df = load_monthly_cache(
        symbol, "ticks", target.year, target.month, data_dir
    )
    if df is None or df.empty:
        return None
    ts = target
    if df.index.tz is not None and ts.tzinfo is None:
        ts = ts.tz_localize(df.index.tz)
    elif df.index.tz is None and ts.tzinfo is not None:
        ts = ts.tz_localize(None)
    sub = df[df.index <= ts]
    if sub.empty:
        return None
    last = sub.iloc[-1]
    bid = float(last.get("bid", 0))
    ask = float(last.get("ask", 0))
    if bid <= 0 or ask <= 0:
        return None
    return {"bid": bid, "ask": ask, "mid": (bid + ask) / 2.0}


def _apply_live_overwrite(
    market_data: dict[str, pd.DataFrame],
    tick: dict[str, float],
) -> dict[str, pd.DataFrame]:
    """live engine と同じく、各 TF の最後のバーを tick mid で上書き"""
    mid = tick["mid"]
    out: dict[str, pd.DataFrame] = {}
    for tf, df in market_data.items():
        if df.empty:
            out[tf] = df
            continue
        d = df.copy()
        idx = d.index[-1]
        d.at[idx, "close"] = mid
        if mid > float(d.at[idx, "high"]):
            d.at[idx, "high"] = mid
        if mid < float(d.at[idx, "low"]):
            d.at[idx, "low"] = mid
        out[tf] = d
    return out


def replay_one_trade(
    symbol: str,
    entry_time: pd.Timestamp,
    expected_direction: str,
    data_dir: Path | None = None,
    apply_live_overwrite: bool = True,
    use_live_calc_indicators: bool = False,
) -> dict[str, Any]:
    """1件のライブトレードを再現してシグナル比較

    Args:
        use_live_calc_indicators: True で Live の calc_indicators_multi_tf
            (基本指標26列のみ) を使う。False で BT の PrecomputeEngine 出力
            (monthly_cache、65列)。Live の実動作再現には True 推奨。
    """
    if data_dir is None:
        data_dir = Path(get_data_dir())

    loader = ConfigLoader()
    bot_config, _pm = loader.load_preset_config(symbol)

    market_data = _load_market_data_up_to(
        symbol, entry_time, data_dir,
    )

    # Live 経路再現: 一旦OHLC のみに戻して calc_indicators_multi_tf で再計算
    if use_live_calc_indicators:
        from autotrader.calculator.technical.batch import (
            calc_indicators_multi_tf,
        )
        ohlc_only = {}
        for tf, df in market_data.items():
            # 'time' 列を index に戻して raw OHLCV だけ抽出
            d = df.set_index("time") if "time" in df.columns else df
            cols = [c for c in ["open","high","low","close","volume"] if c in d.columns]
            ohlc_only[tf] = d[cols].copy()
        live_calc = calc_indicators_multi_tf(ohlc_only)
        # 'time' 列を再付与
        market_data = {}
        for tf, df in live_calc.items():
            d = df.reset_index().rename(columns={"index": "time"})
            if "time" not in d.columns:
                d.rename(columns={d.columns[0]: "time"}, inplace=True)
            market_data[tf] = d
    if "M1" not in market_data:
        return {
            "symbol": symbol,
            "entry_time": entry_time,
            "expected": expected_direction,
            "predicted": None,
            "score": None,
            "match": False,
            "reason": "no M1 data",
        }

    tick = _get_tick_at(symbol, entry_time, data_dir)
    used_overwrite = False
    if apply_live_overwrite and tick is not None:
        market_data = _apply_live_overwrite(market_data, tick)
        used_overwrite = True

    bot = UnifiedTradeBot(bot_config)
    bot.set_market_data(market_data)
    if tick is not None:
        # spread を bot に注入 (SoftGuard 用)
        pip_unit = get_pip_unit(symbol)
        spread_pips = (tick["ask"] - tick["bid"]) / pip_unit
        try:
            bot.set_current_spread_pips(spread_pips)
        except Exception:
            pass

    # snapshot_time のタイムゾーンを market_data に合わせる
    base_idx_tz = None
    if "M1" in market_data and len(market_data["M1"]) > 0:
        col = market_data["M1"]["time"]
        if hasattr(col, "dt") and col.dt.tz is not None:
            base_idx_tz = col.dt.tz
    ct = entry_time
    if base_idx_tz is not None and ct.tzinfo is None:
        ct = ct.tz_localize(base_idx_tz)
    elif base_idx_tz is None and ct.tzinfo is not None:
        ct = ct.tz_localize(None)

    consolidated = bot.generate_signal(ct)
    predicted = (
        consolidated.direction.value
        if hasattr(consolidated.direction, "value")
        else str(consolidated.direction)
    )
    score = (
        consolidated.consensus_score
        if hasattr(consolidated, "consensus_score")
        else None
    )
    match = predicted == expected_direction

    return {
        "symbol": symbol,
        "entry_time": entry_time,
        "expected": expected_direction,
        "predicted": predicted,
        "score": score,
        "match": match,
        "buy_score": getattr(consolidated, "buy_score", None),
        "sell_score": getattr(consolidated, "sell_score", None),
        "reason": "ok" if match else "direction mismatch",
        "tick_overwrite": used_overwrite,
        "tick_mid": tick["mid"] if tick else None,
    }


def replay_with_window_scan(
    symbol: str,
    target_time: pd.Timestamp,
    expected_direction: str,
    window_sec_before: int = 60,
    window_sec_after: int = 0,
    step_sec: int = 1,
    data_dir: Path | None = None,
    use_live_calc_indicators: bool = False,
) -> dict[str, Any]:
    """target_time の前後ウィンドウを step_sec 刻みでスキャンし、
    いずれかの瞬間で expected_direction のシグナルが立つか確認する。

    これにより「ライブの 1秒評価で発火したスパイクシグナル」が
    BT 環境でも (時刻違いで) 発生していたかを直接検証する。
    """
    import math
    if data_dir is None:
        data_dir = Path(get_data_dir())

    n_steps = (window_sec_before + window_sec_after) // step_sec + 1
    found = False
    found_at: pd.Timestamp | None = None
    found_score: float | None = None
    max_score = 0.0
    n_evaluated = 0

    for k in range(n_steps):
        offset = -window_sec_before + k * step_sec
        ct = target_time + pd.Timedelta(seconds=offset)
        try:
            r = replay_one_trade(
                symbol, ct, expected_direction,
                data_dir=data_dir,
                use_live_calc_indicators=use_live_calc_indicators,
            )
        except Exception:
            continue
        n_evaluated += 1
        s = r.get("score") or 0.0
        if s > max_score:
            max_score = s
        if r.get("predicted") == expected_direction:
            found = True
            found_at = ct
            found_score = s
            break

    return {
        "symbol": symbol,
        "target_time": target_time,
        "expected": expected_direction,
        "found_match": found,
        "found_at": found_at,
        "found_score": found_score,
        "max_score": max_score,
        "evaluated_steps": n_evaluated,
    }


def replay_live_only_trades(
    live_only_csv: str | Path,
    apply_live_overwrite: bool = True,
    data_dir: Path | None = None,
    use_live_calc_indicators: bool = False,
) -> pd.DataFrame:
    """live_only.csv 全件を再現してまとめて返す"""
    df = pd.read_csv(
        live_only_csv,
        parse_dates=["opened_at", "closed_at"],
    )
    if data_dir is None:
        data_dir = Path(get_data_dir())

    rows = []
    for i, r in df.iterrows():
        sym = r["symbol"]
        et = r["opened_at"]
        if pd.isna(et):
            continue
        sig = r["signal_type"]
        try:
            res = replay_one_trade(
                sym, et, sig, data_dir=data_dir,
                apply_live_overwrite=apply_live_overwrite,
                use_live_calc_indicators=use_live_calc_indicators,
            )
        except Exception as e:
            res = {
                "symbol": sym,
                "entry_time": et,
                "expected": sig,
                "predicted": None,
                "score": None,
                "match": False,
                "reason": f"ERROR: {e}",
            }
        rows.append(res)
        logger.info(
            "[%d/%d] %s @ %s: expected=%s predicted=%s score=%s match=%s",
            i + 1, len(df), sym, et, sig,
            res.get("predicted"), res.get("score"), res.get("match"),
        )
    return pd.DataFrame(rows)
