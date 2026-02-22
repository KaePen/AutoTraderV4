"""損失深掘り分析: TREND×SWING + SL_HIT MFE帯"""
from __future__ import annotations

import pandas as pd
import sys


def load_trades(csv_path: str) -> pd.DataFrame:
    """トレードCSV読み込み"""
    df = pd.read_csv(csv_path, parse_dates=["entry_time", "exit_time"])
    df["year"] = df["entry_time"].dt.year
    df["month"] = df["entry_time"].dt.month
    df["hour_utc"] = df["entry_time"].dt.hour
    df["is_loss"] = df["profit_loss"] < 0
    df["is_win"] = df["profit_loss"] > 0
    return df


def print_section(title: str):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")


def trend_swing_deep(df: pd.DataFrame):
    """TREND×SWINGの詳細分析"""
    ts = df[(df["regime"] == "TREND") & (df["mode"] == "SWING")]
    print_section("A. TREND×SWING 全体像")
    print(f"取引数: {len(ts)}")
    print(f"勝率: {ts['is_win'].sum()/len(ts)*100:.1f}%")
    print(f"損益: {ts['profit_loss'].sum():+,.0f}")

    # Exit理由別
    print(f"\n  Exit理由別:")
    for reason, grp in ts.groupby("exit_reason"):
        total = len(grp)
        pnl = grp["profit_loss"].sum()
        avg = grp["profit_loss"].mean()
        avg_hold = grp["holding_minutes"].mean()
        avg_mfe = grp["mfe_r"].mean()
        print(
            f"    {reason:15s}: {total:4d}件 "
            f"損益={pnl:+10,.0f} 平均={avg:+7,.0f} "
            f"保有={avg_hold:5.0f}分 MFE_R={avg_mfe:.3f}"
        )

    # STAGNATION詳細
    print_section("A-1. TREND×SWING STAGNATION詳細")
    stag = ts[ts["exit_reason"] == "STAGNATION"]
    print(f"件数: {len(stag)}, 損益: {stag['profit_loss'].sum():+,.0f}")

    # 保有時間分布
    print(f"\n  保有時間分布:")
    for label, lo, hi in [
        ("60分以下", 0, 60),
        ("60-120分", 60, 120),
        ("120-180分", 120, 180),
        ("180分以上", 180, 99999),
    ]:
        sub = stag[
            (stag["holding_minutes"] >= lo)
            & (stag["holding_minutes"] < hi)
        ]
        if len(sub) > 0:
            print(
                f"    {label:10s}: {len(sub):4d}件 "
                f"損益={sub['profit_loss'].sum():+9,.0f} "
                f"平均MFE_R={sub['mfe_r'].mean():.3f}"
            )

    # セッション別STAGNATION
    print(f"\n  セッション別STAGNATION:")
    for session, grp in stag.groupby("session"):
        pnl = grp["profit_loss"].sum()
        avg_mfe = grp["mfe_r"].mean()
        print(
            f"    {session:12s}: {len(grp):4d}件 "
            f"損益={pnl:+9,.0f} 平均MFE_R={avg_mfe:.3f}"
        )

    # trend_strength別STAGNATION
    print(f"\n  trend_strength別STAGNATION:")
    stag_copy = stag.copy()
    stag_copy["ts_band"] = pd.cut(
        stag_copy["trend_strength"],
        bins=[0, 0.4, 0.6, 0.8, 1.01],
        labels=["<0.4", "0.4-0.6", "0.6-0.8", "0.8+"],
    )
    for band, grp in stag_copy.groupby("ts_band", observed=True):
        pnl = grp["profit_loss"].sum()
        avg_mfe = grp["mfe_r"].mean()
        print(
            f"    TS {band:8s}: {len(grp):4d}件 "
            f"損益={pnl:+9,.0f} 平均MFE_R={avg_mfe:.3f}"
        )

    # ADX別STAGNATION
    print(f"\n  ADX別STAGNATION:")
    stag_copy["adx_band"] = pd.cut(
        stag_copy["entry_adx"],
        bins=[0, 20, 30, 40, 100],
        labels=["<20", "20-30", "30-40", "40+"],
    )
    for band, grp in stag_copy.groupby("adx_band", observed=True):
        pnl = grp["profit_loss"].sum()
        avg_mfe = grp["mfe_r"].mean()
        print(
            f"    ADX {band:6s}: {len(grp):4d}件 "
            f"損益={pnl:+9,.0f} 平均MFE_R={avg_mfe:.3f}"
        )

    # consensus_score別STAGNATION
    print(f"\n  スコア別STAGNATION:")
    stag_copy["score_band"] = pd.cut(
        stag_copy["consensus_score"],
        bins=[0, 6.0, 6.5, 7.0, 8.0, 20],
        labels=["<6.0", "6.0-6.5", "6.5-7.0", "7.0-8.0", "8.0+"],
    )
    for band, grp in stag_copy.groupby("score_band", observed=True):
        pnl = grp["profit_loss"].sum()
        avg_mfe = grp["mfe_r"].mean()
        print(
            f"    Score {band:8s}: {len(grp):4d}件 "
            f"損益={pnl:+9,.0f} 平均MFE_R={avg_mfe:.3f}"
        )


def sl_hit_deep(df: pd.DataFrame):
    """SL_HIT MFE帯別の詳細分析"""
    print_section("B. SL_HIT MFE 0.3-0.5R帯の詳細")
    sl = df[df["exit_reason"] == "SL_HIT"]
    target = sl[(sl["mfe_r"] >= 0.3) & (sl["mfe_r"] < 0.5)]
    print(f"件数: {len(target)}, 損益: {target['profit_loss'].sum():+,.0f}")

    # レジーム×モード別
    print(f"\n  レジーム×モード別:")
    for (regime, mode), grp in target.groupby(["regime", "mode"]):
        pnl = grp["profit_loss"].sum()
        avg_hold = grp["holding_minutes"].mean()
        print(
            f"    {regime:10s}×{mode:10s}: {len(grp):4d}件 "
            f"損益={pnl:+10,.0f} 保有={avg_hold:5.0f}分"
        )

    # 全SL_HIT: レジーム×モード×MFE帯
    print_section("B-1. 全SL_HIT: レジーム×MFE帯マトリクス")
    for regime in ["RANGE", "TREND"]:
        r_sl = sl[sl["regime"] == regime]
        mode = "DAY_TRADE" if regime == "RANGE" else "SWING"
        print(f"\n  {regime}×{mode}:")
        for label, lo, hi in [
            ("MFE<0.1R", -999, 0.1),
            ("MFE 0.1-0.3R", 0.1, 0.3),
            ("MFE 0.3-0.5R", 0.3, 0.5),
            ("MFE 0.5-1.0R", 0.5, 1.0),
            ("MFE 1.0R+", 1.0, 999),
        ]:
            sub = r_sl[(r_sl["mfe_r"] >= lo) & (r_sl["mfe_r"] < hi)]
            if len(sub) > 0:
                avg_hold = sub["holding_minutes"].mean()
                avg_score = sub["consensus_score"].mean()
                print(
                    f"    {label:15s}: {len(sub):4d}件 "
                    f"損益={sub['profit_loss'].sum():+10,.0f} "
                    f"保有={avg_hold:5.0f}分 "
                    f"Score={avg_score:.2f}"
                )


def swing_be_analysis(df: pd.DataFrame):
    """SWING取引のBE分析"""
    print_section("C. SWING取引のBE/早期BE分析")
    swing = df[df["mode"] == "SWING"]

    # BE_HITの保有時間とMFE_R
    be = swing[swing["exit_reason"] == "BE_HIT"]
    print(f"BE_HIT (SWING): {len(be)}件")
    print(f"  平均保有: {be['holding_minutes'].mean():.0f}分")
    print(f"  平均MFE_R: {be['mfe_r'].mean():.3f}")

    # SL_HIT件でMFE>0.3Rのもの = BEに移動していれば救えた
    sl = swing[swing["exit_reason"] == "SL_HIT"]
    salvageable = sl[sl["mfe_r"] >= 0.3]
    print(f"\nSL_HIT (SWING) MFE>=0.3R: {len(salvageable)}件")
    print(f"  損益: {salvageable['profit_loss'].sum():+,.0f}")
    print(f"  → MFE 0.3Rで早期BEしていれば理論上0円損失")

    salvageable05 = sl[sl["mfe_r"] >= 0.5]
    print(f"\nSL_HIT (SWING) MFE>=0.5R: {len(salvageable05)}件")
    print(f"  損益: {salvageable05['profit_loss'].sum():+,.0f}")
    print(f"  → MFE 0.5Rで早期BEしていれば理論上0円損失")

    # 現在のBE閾値(1R)到達率
    reached_1r = sl[sl["mfe_r"] >= 1.0]
    print(f"\nSL_HIT (SWING) MFE>=1.0R: {len(reached_1r)}件")
    print(f"  損益: {reached_1r['profit_loss'].sum():+,.0f}")
    print(f"  → 現行BE閾値(1R)到達後にSLまで戻された")


def tokyo_deep(df: pd.DataFrame):
    """東京セッション弱さの分析"""
    print_section("D. 東京セッションの弱さ分析")
    tokyo = df[df["session"] == "TOKYO"]
    others = df[df["session"] != "TOKYO"]

    print(f"東京: {len(tokyo)}件 WR={tokyo['is_win'].sum()/len(tokyo)*100:.1f}%")
    print(f"他: {len(others)}件 WR={others['is_win'].sum()/len(others)*100:.1f}%")

    # 東京のレジーム別
    print(f"\n  東京レジーム別:")
    for regime, grp in tokyo.groupby("regime"):
        wins = grp["is_win"].sum()
        total = len(grp)
        wr = wins / total * 100
        pnl = grp["profit_loss"].sum()
        print(
            f"    {regime:10s}: {total:4d}件 WR={wr:5.1f}% "
            f"損益={pnl:+10,.0f}"
        )

    # 東京のExit理由別
    print(f"\n  東京Exit理由別:")
    for reason, grp in tokyo.groupby("exit_reason"):
        pnl = grp["profit_loss"].sum()
        total = len(grp)
        print(f"    {reason:15s}: {total:4d}件 損益={pnl:+10,.0f}")

    # 東京×時間帯
    print(f"\n  東京UTC時間帯別:")
    for hour, grp in tokyo.groupby("hour_utc"):
        wins = grp["is_win"].sum()
        total = len(grp)
        wr = wins / total * 100
        pnl = grp["profit_loss"].sum()
        print(
            f"    UTC{hour:02d}(JST{(hour+9)%24:02d}): "
            f"{total:4d}件 WR={wr:5.1f}% 損益={pnl:+9,.0f}"
        )


def improvement_simulation(df: pd.DataFrame):
    """改善シミュレーション（概算）"""
    print_section("E. 改善シミュレーション")

    base_pnl = df["profit_loss"].sum()
    base_trades = len(df)
    print(f"ベースライン: {base_trades}件, {base_pnl:+,.0f}")

    # シミュ1: TREND×SWING早期BE 0.5R
    print(f"\n--- シミュ1: TREND×SWING早期BE 0.5R ---")
    df_sim = df.copy()
    swing_sl = df_sim[
        (df_sim["mode"] == "SWING")
        & (df_sim["exit_reason"] == "SL_HIT")
        & (df_sim["mfe_r"] >= 0.5)
    ]
    # MFE>=0.5Rの場合、損益を0にする（BE=エントリー価格で決済）
    saved = abs(swing_sl["profit_loss"].sum())
    new_pnl = base_pnl + saved
    # ただしBEで弾かれた分、TRAIL_HITの利益も一部失う可能性
    print(f"  対象: {len(swing_sl)}件 (SL_HIT, MFE>=0.5R)")
    print(f"  救済額: +{saved:,.0f}")
    print(f"  新損益: {new_pnl:+,.0f} (月利{new_pnl/192/10000:.1f}%)")

    # シミュ2: TREND×SWING STAGNATION 90分短縮
    print(f"\n--- シミュ2: STAGNATION MFE<0.1R → 90分で損切り ---")
    stag_target = df_sim[
        (df_sim["mode"] == "SWING")
        & (df_sim["exit_reason"] == "STAGNATION")
        & (df_sim["mfe_r"] < 0.1)
    ]
    # 早めに切れば損失が少なくなる（保有時間短縮 → MAE減少）
    # 概算: 保有時間半減で損失30%減
    current_loss = abs(stag_target["profit_loss"].sum())
    estimated_save = current_loss * 0.3
    print(f"  対象: {len(stag_target)}件 (STAGNATION, MFE<0.1R)")
    print(f"  現在損失: -{current_loss:,.0f}")
    print(f"  推定救済: +{estimated_save:,.0f}")

    # シミュ3: スコア閾値引き上げ(SWING 6.5→7.0)
    print(f"\n--- シミュ3: SWING閾値 6.0→6.5 ---")
    swing = df_sim[df_sim["mode"] == "SWING"]
    filtered_out = swing[swing["consensus_score"] < 6.5]
    kept = swing[swing["consensus_score"] >= 6.5]
    print(f"  除外: {len(filtered_out)}件")
    print(f"    除外分の損益: {filtered_out['profit_loss'].sum():+,.0f}")
    print(f"  残り: {len(kept)}件")
    print(f"    残り分の損益: {kept['profit_loss'].sum():+,.0f}")

    # シミュ4: 月曜フィルター（スコア+0.5）
    print(f"\n--- シミュ4: 月曜スコア+0.5 ---")
    monday = df_sim[df_sim["entry_time"].dt.dayofweek == 0]
    mon_filtered = monday[monday["consensus_score"] < 6.5]
    print(f"  月曜全体: {len(monday)}件, 損益={monday['profit_loss'].sum():+,.0f}")
    print(f"  除外対象(score<6.5): {len(mon_filtered)}件")
    print(f"    除外分の損益: {mon_filtered['profit_loss'].sum():+,.0f}")


def position_sizing_analysis(df: pd.DataFrame):
    """ポジションサイジング分析"""
    print_section("F. ポジションサイジング分析")
    # 現在は固定ロット
    # Kelly基準での最適リスク率を計算

    wins = df[df["profit_loss"] > 0]
    losses = df[df["profit_loss"] < 0]

    wr = len(wins) / (len(wins) + len(losses))  # BE除外
    avg_win_r = wins["mfe_r"].mean()  # 近似
    avg_loss_r = abs(losses["mae_r"].mean())  # 近似

    # 実際のRRは利益/損失の平均
    avg_win_pips = wins["pips"].mean()
    avg_loss_pips = abs(losses["pips"].mean())
    rr = avg_win_pips / avg_loss_pips if avg_loss_pips > 0 else 0

    print(f"勝率(BE除外): {wr*100:.1f}%")
    print(f"平均勝ちpips: {avg_win_pips:.1f}")
    print(f"平均負けpips: {avg_loss_pips:.1f}")
    print(f"RR比: {rr:.2f}")

    # Kelly
    kelly = wr - (1 - wr) / rr if rr > 0 else 0
    half_kelly = kelly / 2
    print(f"\nKelly基準: {kelly*100:.1f}%")
    print(f"Half-Kelly: {half_kelly*100:.1f}%")

    # 複利シミュレーション
    print(f"\n--- 複利シミュレーション (Half-Kelly={half_kelly*100:.1f}%) ---")
    equity = 1_000_000
    monthly = df.groupby(
        [df["entry_time"].dt.year, df["entry_time"].dt.month]
    )
    yearly_equity = {}
    for (year, month), trades in monthly:
        for _, trade in trades.iterrows():
            # リスク額 = equity * half_kelly
            risk = equity * half_kelly
            sl_pips = trade["sl_pips"]
            if sl_pips <= 0:
                continue
            # lot = risk / (sl_pips * 100)  # USDJPY近似
            # 損益 = lot * pips * 100
            # = (risk / sl_pips) * pips
            pips = trade["pips"]
            pnl = risk * (pips / sl_pips)
            equity += pnl
            equity = max(equity, 10000)  # 最低1万円
        if month == 12 or (year, month) == (2025, 12):
            yearly_equity[year] = equity

    for year, eq in sorted(yearly_equity.items()):
        print(f"  {year}年末: {eq:>15,.0f}")

    print(f"\n  最終equity: {equity:,.0f}")
    print(f"  倍率: {equity/1_000_000:.1f}x")
    cagr = (equity / 1_000_000) ** (1 / 16) - 1
    print(f"  CAGR: {cagr*100:.1f}%")
    monthly_avg = equity ** (1 / 192) - 1
    print(f"  月平均複利: 概算")


def main():
    csv_path = sys.argv[1] if len(sys.argv) > 1 else (
        "logs/backtest_log/trades_20260217_113709.csv"
    )
    df = load_trades(csv_path)

    trend_swing_deep(df)
    sl_hit_deep(df)
    swing_be_analysis(df)
    tokyo_deep(df)
    improvement_simulation(df)
    position_sizing_analysis(df)


if __name__ == "__main__":
    main()
