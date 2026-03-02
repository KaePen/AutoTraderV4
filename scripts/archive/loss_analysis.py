"""15年バックテスト損失構造分析"""
from __future__ import annotations

import sys

import pandas as pd


def load_trades(csv_path: str) -> pd.DataFrame:
    """トレードCSV読み込み"""
    df = pd.read_csv(csv_path, parse_dates=["entry_time", "exit_time"])
    df["year"] = df["entry_time"].dt.year
    df["month"] = df["entry_time"].dt.month
    df["hour_utc"] = df["entry_time"].dt.hour
    df["day_of_week"] = df["entry_time"].dt.dayofweek
    df["is_loss"] = df["profit_loss"] < 0
    df["is_win"] = df["profit_loss"] > 0
    df["is_be"] = df["profit_loss"] == 0
    # ボラティリティ帯
    df["vol_band"] = pd.cut(
        df["entry_bb_width"],
        bins=[0, 0.15, 0.25, 0.40, 999],
        labels=["LOW", "MID", "HIGH", "VERY_HIGH"],
    )
    # ATRベース
    df["atr_band"] = pd.qcut(
        df["entry_atr"], q=4, labels=["Q1_LOW", "Q2", "Q3", "Q4_HIGH"]
    )
    return df


def print_section(title: str):
    """セクションヘッダ"""
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")


def basic_stats(df: pd.DataFrame):
    """基本統計"""
    print_section("1. 基本統計 (100万円元手)")
    total = len(df)
    wins = df["is_win"].sum()
    losses = df["is_loss"].sum()
    bes = df["is_be"].sum()
    gross_profit = df.loc[df["profit_loss"] > 0, "profit_loss"].sum()
    gross_loss = abs(df.loc[df["profit_loss"] < 0, "profit_loss"].sum())
    net = df["profit_loss"].sum()
    years = df["year"].nunique()
    months = df.groupby(["year", "month"]).ngroups

    print(f"期間: {df['year'].min()}-{df['year'].max()} ({years}年, {months}ヶ月)")
    print(f"取引数: {total} ({total/months:.1f}/月)")
    print(f"勝率: {wins/total*100:.1f}% (勝{wins} / 負{losses} / BE{bes})")
    print(f"非敗率: {(wins+bes)/total*100:.1f}%")
    print(f"PF: {gross_profit/gross_loss:.2f}")
    print(f"純利益: {net:+,.0f}")
    print(f"年平均: {net/years:+,.0f}")
    print(f"月平均: {net/months:+,.0f}")
    print(f"100万元手月利: {net/months/10000:.1f}%")

    # 月別収益分布
    monthly = df.groupby(["year", "month"])["profit_loss"].sum()
    print(f"\n月次収益分布:")
    print(f"  中央値: {monthly.median():+,.0f}")
    print(f"  平均: {monthly.mean():+,.0f}")
    print(f"  最小: {monthly.min():+,.0f}")
    print(f"  最大: {monthly.max():+,.0f}")
    print(f"  プラス月: {(monthly > 0).sum()}/{len(monthly)} "
          f"({(monthly > 0).sum()/len(monthly)*100:.1f}%)")
    # マイナス月の詳細
    neg_months = monthly[monthly < 0]
    if len(neg_months) > 0:
        print(f"\n  マイナス月一覧:")
        for (y, m), val in neg_months.items():
            print(f"    {y}-{m:02d}: {val:+,.0f}")


def loss_by_exit(df: pd.DataFrame):
    """Exit理由別損失分析"""
    print_section("2. Exit理由別分析")
    grouped = df.groupby("exit_reason").agg(
        count=("profit_loss", "count"),
        wins=("is_win", "sum"),
        losses=("is_loss", "sum"),
        bes=("is_be", "sum"),
        total_pnl=("profit_loss", "sum"),
        avg_pnl=("profit_loss", "mean"),
        avg_holding=("holding_minutes", "mean"),
    ).sort_values("total_pnl")

    for reason, row in grouped.iterrows():
        wr = row["wins"] / row["count"] * 100
        print(
            f"  {reason:15s}: {row['count']:5.0f}件 "
            f"WR={wr:5.1f}% "
            f"損益={row['total_pnl']:+10,.0f} "
            f"平均={row['avg_pnl']:+7,.0f} "
            f"保有={row['avg_holding']:5.0f}分"
        )


def loss_by_regime_mode(df: pd.DataFrame):
    """レジーム×モード別分析"""
    print_section("3. レジーム×モード別分析")
    for (regime, mode), grp in df.groupby(["regime", "mode"]):
        wins = grp["is_win"].sum()
        total = len(grp)
        wr = wins / total * 100
        pnl = grp["profit_loss"].sum()
        avg = grp["profit_loss"].mean()
        # 損失取引の平均損失
        loss_trades = grp[grp["is_loss"]]
        avg_loss = loss_trades["profit_loss"].mean() if len(loss_trades) > 0 else 0
        win_trades = grp[grp["is_win"]]
        avg_win = win_trades["profit_loss"].mean() if len(win_trades) > 0 else 0
        print(
            f"  {regime:10s}×{mode:10s}: {total:5d}件 "
            f"WR={wr:5.1f}% PnL={pnl:+10,.0f} "
            f"平均勝={avg_win:+7,.0f} 平均負={avg_loss:+7,.0f} "
            f"RR={abs(avg_win/avg_loss) if avg_loss != 0 else 0:.2f}"
        )


def loss_by_session(df: pd.DataFrame):
    """セッション別分析"""
    print_section("4. セッション別分析")
    for session, grp in df.groupby("session"):
        wins = grp["is_win"].sum()
        total = len(grp)
        wr = wins / total * 100
        pnl = grp["profit_loss"].sum()
        loss_trades = grp[grp["is_loss"]]
        avg_loss = loss_trades["profit_loss"].mean() if len(loss_trades) > 0 else 0
        print(
            f"  {session:12s}: {total:5d}件 "
            f"WR={wr:5.1f}% PnL={pnl:+10,.0f} "
            f"平均負={avg_loss:+7,.0f}"
        )


def loss_by_hour(df: pd.DataFrame):
    """UTC時間帯別分析"""
    print_section("5. UTC時間帯別分析 (損益順)")
    hourly = df.groupby("hour_utc").agg(
        count=("profit_loss", "count"),
        wins=("is_win", "sum"),
        total_pnl=("profit_loss", "sum"),
        avg_pnl=("profit_loss", "mean"),
    )
    hourly["wr"] = hourly["wins"] / hourly["count"] * 100
    hourly = hourly.sort_values("total_pnl")

    for hour, row in hourly.iterrows():
        bar_len = int(row["total_pnl"] / 5000)
        bar = "█" * abs(bar_len) if bar_len >= 0 else "░" * abs(bar_len)
        sign = "+" if row["total_pnl"] >= 0 else "-"
        print(
            f"  UTC{hour:02d} (JST{(hour+9)%24:02d}): "
            f"{row['count']:4.0f}件 WR={row['wr']:5.1f}% "
            f"PnL={row['total_pnl']:+9,.0f} "
            f"{'|' if row['total_pnl'] < 0 else ''}{bar}"
        )


def loss_by_volatility(df: pd.DataFrame):
    """ボラティリティ帯別分析"""
    print_section("6. ボラティリティ(BB幅)帯別分析")
    for band, grp in df.groupby("vol_band", observed=True):
        wins = grp["is_win"].sum()
        total = len(grp)
        if total == 0:
            continue
        wr = wins / total * 100
        pnl = grp["profit_loss"].sum()
        avg = grp["profit_loss"].mean()
        print(
            f"  BBW {band:10s}: {total:5d}件 "
            f"WR={wr:5.1f}% PnL={pnl:+10,.0f} 平均={avg:+7,.0f}"
        )

    print("\n  ATR四分位別:")
    for band, grp in df.groupby("atr_band", observed=True):
        wins = grp["is_win"].sum()
        total = len(grp)
        if total == 0:
            continue
        wr = wins / total * 100
        pnl = grp["profit_loss"].sum()
        print(
            f"  ATR {band:10s}: {total:5d}件 "
            f"WR={wr:5.1f}% PnL={pnl:+10,.0f}"
        )


def loss_by_score(df: pd.DataFrame):
    """エントリースコア帯別分析"""
    print_section("7. エントリースコア帯別分析")
    df["score_band"] = pd.cut(
        df["consensus_score"],
        bins=[0, 5.5, 6.0, 6.5, 7.0, 7.5, 8.0, 20],
        labels=["<5.5", "5.5-6.0", "6.0-6.5", "6.5-7.0",
                "7.0-7.5", "7.5-8.0", "8.0+"],
    )
    for band, grp in df.groupby("score_band", observed=True):
        wins = grp["is_win"].sum()
        total = len(grp)
        if total == 0:
            continue
        wr = wins / total * 100
        pnl = grp["profit_loss"].sum()
        avg = grp["profit_loss"].mean()
        print(
            f"  Score {band:8s}: {total:5d}件 "
            f"WR={wr:5.1f}% PnL={pnl:+10,.0f} 平均={avg:+7,.0f}"
        )


def loss_by_year_regime(df: pd.DataFrame):
    """年×レジーム別分析"""
    print_section("8. 年×レジーム別分析")
    pivot = df.pivot_table(
        index="year",
        columns="regime",
        values="profit_loss",
        aggfunc=["sum", "count"],
    )
    print(f"{'年':>6s}", end="")
    regimes = df["regime"].unique()
    for r in sorted(regimes):
        print(f"  {r:>14s}(件数)", end="")
    print(f"  {'合計':>10s}")
    print("-" * 90)

    for year in sorted(df["year"].unique()):
        yr_data = df[df["year"] == year]
        print(f"{year:>6d}", end="")
        for r in sorted(regimes):
            r_data = yr_data[yr_data["regime"] == r]
            pnl = r_data["profit_loss"].sum()
            cnt = len(r_data)
            print(f"  {pnl:>+10,.0f}({cnt:3d})", end="")
        print(f"  {yr_data['profit_loss'].sum():>+10,.0f}")


def stagnation_analysis(df: pd.DataFrame):
    """STAGNATION決済の詳細分析"""
    print_section("9. STAGNATION決済の詳細分析")
    stag = df[df["exit_reason"] == "STAGNATION"]
    print(f"全STAGNATION: {len(stag)}件, 損益={stag['profit_loss'].sum():+,.0f}")

    for (regime, mode), grp in stag.groupby(["regime", "mode"]):
        total = len(grp)
        pnl = grp["profit_loss"].sum()
        avg_hold = grp["holding_minutes"].mean()
        avg_mfe = grp["mfe_r"].mean()
        avg_mae = grp["mae_r"].mean()
        print(
            f"  {regime:10s}×{mode:10s}: {total:4d}件 "
            f"損益={pnl:+9,.0f} "
            f"平均保有={avg_hold:5.0f}分 "
            f"平均MFE_R={avg_mfe:.3f} "
            f"平均MAE_R={avg_mae:.3f}"
        )

    # MFE_R分布
    print(f"\n  MFE_R分布 (STAGNATION):")
    for label, lo, hi in [
        ("0未満", -999, 0),
        ("0-0.1R", 0, 0.1),
        ("0.1-0.2R", 0.1, 0.2),
        ("0.2-0.5R", 0.2, 0.5),
        ("0.5R+", 0.5, 999),
    ]:
        subset = stag[(stag["mfe_r"] >= lo) & (stag["mfe_r"] < hi)]
        if len(subset) > 0:
            print(
                f"    {label:10s}: {len(subset):4d}件 "
                f"損益={subset['profit_loss'].sum():+9,.0f} "
                f"平均損益={subset['profit_loss'].mean():+7,.0f}"
            )


def sl_hit_analysis(df: pd.DataFrame):
    """SL_HIT詳細分析"""
    print_section("10. SL_HIT詳細分析")
    sl = df[df["exit_reason"] == "SL_HIT"]
    print(f"全SL_HIT: {len(sl)}件, 損益={sl['profit_loss'].sum():+,.0f}")

    # MFE到達前にSLに触れた（MFE_R < 0.3）= 方向性の問題
    print(f"\n  MFE_R別SL_HIT分析:")
    for label, lo, hi in [
        ("MFE<0.1R", -999, 0.1),
        ("MFE 0.1-0.3R", 0.1, 0.3),
        ("MFE 0.3-0.5R", 0.3, 0.5),
        ("MFE 0.5-1.0R", 0.5, 1.0),
        ("MFE 1.0R+", 1.0, 999),
    ]:
        subset = sl[(sl["mfe_r"] >= lo) & (sl["mfe_r"] < hi)]
        if len(subset) > 0:
            avg_hold = subset["holding_minutes"].mean()
            print(
                f"    {label:15s}: {len(subset):4d}件 "
                f"損益={subset['profit_loss'].sum():+10,.0f} "
                f"平均保有={avg_hold:5.0f}分"
            )

    # スコア別SL_HIT
    print(f"\n  スコア別SL_HIT:")
    sl["score_band"] = pd.cut(
        sl["consensus_score"],
        bins=[0, 6.0, 6.5, 7.0, 8.0, 20],
        labels=["<6.0", "6.0-6.5", "6.5-7.0", "7.0-8.0", "8.0+"],
    )
    for band, grp in sl.groupby("score_band", observed=True):
        total = len(grp)
        pnl = grp["profit_loss"].sum()
        avg_mfe = grp["mfe_r"].mean()
        print(
            f"    Score {band:8s}: {total:4d}件 "
            f"損益={pnl:+10,.0f} "
            f"平均MFE_R={avg_mfe:.3f}"
        )


def consecutive_loss_analysis(df: pd.DataFrame):
    """連敗パターン分析"""
    print_section("11. 連敗パターン分析")
    # 連敗数の分布
    print(f"  連敗数分布:")
    for n in sorted(df["consecutive_losses"].unique()):
        cnt = (df["consecutive_losses"] == n).sum()
        if cnt > 0:
            subset = df[df["consecutive_losses"] == n]
            wr = subset["is_win"].sum() / len(subset) * 100
            pnl = subset["profit_loss"].sum()
            print(
                f"    連敗{n:2.0f}後: {cnt:5d}件 "
                f"WR={wr:5.1f}% 損益={pnl:+10,.0f}"
            )


def penalty_analysis(df: pd.DataFrame):
    """ペナルティ別分析"""
    print_section("12. ペナルティスコア別分析")
    df["penalty_band"] = pd.cut(
        df["penalty_total"],
        bins=[-0.01, 0.0, 0.5, 1.0, 2.0, 999],
        labels=["0.0", "0-0.5", "0.5-1.0", "1.0-2.0", "2.0+"],
    )
    for band, grp in df.groupby("penalty_band", observed=True):
        total = len(grp)
        if total == 0:
            continue
        wins = grp["is_win"].sum()
        wr = wins / total * 100
        pnl = grp["profit_loss"].sum()
        avg = grp["profit_loss"].mean()
        print(
            f"  Penalty {band:8s}: {total:5d}件 "
            f"WR={wr:5.1f}% PnL={pnl:+10,.0f} 平均={avg:+7,.0f}"
        )


def trend_strength_analysis(df: pd.DataFrame):
    """トレンド強度別分析"""
    print_section("13. トレンド強度別分析")
    df["ts_band"] = pd.cut(
        df["trend_strength"],
        bins=[-0.01, 0.2, 0.4, 0.6, 0.8, 1.01],
        labels=["<0.2", "0.2-0.4", "0.4-0.6", "0.6-0.8", "0.8+"],
    )
    for band, grp in df.groupby("ts_band", observed=True):
        total = len(grp)
        if total == 0:
            continue
        wins = grp["is_win"].sum()
        wr = wins / total * 100
        pnl = grp["profit_loss"].sum()
        avg = grp["profit_loss"].mean()
        # レジーム内訳
        regime_counts = grp["regime"].value_counts().to_dict()
        regime_str = ", ".join(
            f"{r}:{c}" for r, c in sorted(regime_counts.items())
        )
        print(
            f"  TS {band:8s}: {total:5d}件 "
            f"WR={wr:5.1f}% PnL={pnl:+10,.0f} 平均={avg:+7,.0f} "
            f"[{regime_str}]"
        )


def htf_alignment_analysis(df: pd.DataFrame):
    """HTFアライメント別分析"""
    print_section("14. HTFアライメント別分析")
    df["htf_band"] = pd.cut(
        df["htf_alignment"],
        bins=[-1.01, -0.5, 0.0, 0.5, 1.01],
        labels=["<-0.5", "-0.5-0", "0-0.5", "0.5+"],
    )
    for band, grp in df.groupby("htf_band", observed=True):
        total = len(grp)
        if total == 0:
            continue
        wins = grp["is_win"].sum()
        wr = wins / total * 100
        pnl = grp["profit_loss"].sum()
        print(
            f"  HTF {band:8s}: {total:5d}件 "
            f"WR={wr:5.1f}% PnL={pnl:+10,.0f}"
        )


def day_of_week_analysis(df: pd.DataFrame):
    """曜日別分析"""
    print_section("15. 曜日別分析")
    days = {0: "月", 1: "火", 2: "水", 3: "木", 4: "金", 5: "土", 6: "日"}
    for dow, grp in df.groupby("day_of_week"):
        total = len(grp)
        if total == 0:
            continue
        wins = grp["is_win"].sum()
        wr = wins / total * 100
        pnl = grp["profit_loss"].sum()
        avg = grp["profit_loss"].mean()
        print(
            f"  {days.get(dow, '?')}曜: {total:5d}件 "
            f"WR={wr:5.1f}% PnL={pnl:+10,.0f} 平均={avg:+7,.0f}"
        )


def worst_trades_analysis(df: pd.DataFrame):
    """最悪取引TOP20"""
    print_section("16. 最悪取引TOP20")
    worst = df.nsmallest(20, "profit_loss")
    for _, row in worst.iterrows():
        print(
            f"  {row['entry_time']} {row['direction']:4s} "
            f"{row['regime']:10s} {row['mode']:10s} "
            f"Score={row['consensus_score']:.1f} "
            f"PnL={row['profit_loss']:+8,.0f} "
            f"SL={row['sl_pips']:.1f}p "
            f"MFE_R={row['mfe_r']:.2f} "
            f"Exit={row['exit_reason']}"
        )


def monthly_target_analysis(df: pd.DataFrame):
    """月次目標分析"""
    print_section("17. 月次収益と目標分析")
    monthly = df.groupby(["year", "month"]).agg(
        pnl=("profit_loss", "sum"),
        trades=("profit_loss", "count"),
        wins=("is_win", "sum"),
    )
    monthly["wr"] = monthly["wins"] / monthly["trades"] * 100

    print(f"100万円元手での月次収益:")
    print(f"{'年月':>8s} {'取引':>5s} {'勝率':>6s} {'損益':>10s} {'月利':>7s}")
    print("-" * 45)

    for (year, month), row in monthly.iterrows():
        pct = row["pnl"] / 10000  # 100万基準のパーセント
        print(
            f"  {year}-{month:02d} {row['trades']:5.0f} "
            f"{row['wr']:5.1f}% {row['pnl']:+10,.0f} "
            f"{pct:+6.1f}%"
        )


def score_component_analysis(df: pd.DataFrame):
    """スコア構成要素と勝敗の関係"""
    print_section("18. スコア構成要素×勝敗分析")
    components = [
        "score_trend", "score_adx", "score_rsi",
        "score_macd_slope", "score_divergence",
        "score_ema_cross", "score_stochastic", "score_htf",
    ]
    print(f"{'要素':>20s} {'勝ち平均':>8s} {'負け平均':>8s} {'差':>8s}")
    print("-" * 50)
    wins = df[df["is_win"]]
    losses = df[df["is_loss"]]
    for comp in components:
        w_mean = wins[comp].mean()
        l_mean = losses[comp].mean()
        diff = w_mean - l_mean
        print(
            f"  {comp:>18s} {w_mean:+8.2f} {l_mean:+8.2f} "
            f"{diff:+8.2f}"
        )


def main():
    csv_path = sys.argv[1] if len(sys.argv) > 1 else (
        "logs/backtest_log/trades_20260217_113709.csv"
    )
    df = load_trades(csv_path)

    basic_stats(df)
    loss_by_exit(df)
    loss_by_regime_mode(df)
    loss_by_session(df)
    loss_by_hour(df)
    loss_by_volatility(df)
    loss_by_score(df)
    loss_by_year_regime(df)
    stagnation_analysis(df)
    sl_hit_analysis(df)
    consecutive_loss_analysis(df)
    penalty_analysis(df)
    trend_strength_analysis(df)
    htf_alignment_analysis(df)
    day_of_week_analysis(df)
    worst_trades_analysis(df)
    monthly_target_analysis(df)
    score_component_analysis(df)


if __name__ == "__main__":
    main()
