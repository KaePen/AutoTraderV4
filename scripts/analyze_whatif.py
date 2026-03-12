"""What-If分析スクリプト: ブロックシグナルの機会損失を定量化する."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

# データディレクトリ自動検出
_DATA_DIR = Path("D:/Projects/AutoTraderV4_data")
_RESULTS_DIR = _DATA_DIR / "backtest_results"
_REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"

# 全対象ペア
ALL_SYMBOLS = [
    "USDJPY", "EURJPY", "GBPJPY", "AUDJPY", "CADJPY", "CHFJPY",
]

# What-Ifデータのディレクトリパターン
_WI_DIR_PATTERN = re.compile(r"\d+_AN-(\w+)-WI$")

# ブロック理由 → カテゴリマッピング
_CATEGORY_PATTERNS: list[tuple[str, str]] = [
    ("スコア不足", "Score_below"),
    ("ペナルティ上限", "Penalty_cap"),
    ("SoftGuard", "SoftGuard"),
    ("HTFトレンド不一致", "HTF"),
    ("BCAブロック", "BCA"),
    ("MACDスロープ", "MACD"),
    ("トレンド強度過大", "Trend_strength"),
    ("RANGE制限", "RANGE_limit"),
    ("RANGE低ボラ", "RANGE_low_vol"),
    ("RANGEスコアプレミアム", "RANGE_premium"),
    ("ボリューム", "Volume"),
    ("TF整合率不足", "TF_align"),
    ("LONDONオフ時間", "London_off"),
    ("WeakHours", "WeakHours"),
    ("東京深夜TREND", "Tokyo_night"),
    ("競合シグナル", "Conflict"),
]

# SL/TP固定値（What-Ifシミュレーション制約）
FIXED_SL = 20.0
FIXED_TP = 24.0
BREAKEVEN_WR = FIXED_SL / (FIXED_SL + FIXED_TP)  # 45.5%


def _categorize_block_reason(reason: str) -> str:
    """ブロック理由文字列をカテゴリに変換する."""
    for pattern, category in _CATEGORY_PATTERNS:
        if pattern in reason:
            return category
    return "Other"


def _find_whatif_dir(symbol: str) -> Path | None:
    """シンボルに対応するWhat-Ifディレクトリを検索する."""
    for d in sorted(_RESULTS_DIR.iterdir()):
        if not d.is_dir():
            continue
        m = _WI_DIR_PATTERN.match(d.name)
        if m and m.group(1) == symbol:
            return d
    return None


def _load_whatif_trades(symbol: str) -> pd.DataFrame:
    """What-Ifトレードデータを読み込む."""
    wi_dir = _find_whatif_dir(symbol)
    if wi_dir is None:
        raise FileNotFoundError(
            f"{symbol}のWhat-Ifデータが見つかりません"
        )
    csv_path = wi_dir / "whatif_trades.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"{csv_path} が存在しません")

    df = pd.read_csv(
        csv_path,
        parse_dates=["signal_time", "exit_time"],
    )
    df["category"] = df["block_reason"].apply(
        _categorize_block_reason
    )
    df["year"] = df["signal_time"].dt.year
    df["win"] = df["pips"] > 0
    df["period"] = df["year"].apply(
        lambda y: "IS(2020-2023)" if y <= 2023 else "OOS(2024-2025)"
    )
    return df


def _stats(df: pd.DataFrame) -> dict[str, float]:
    """基本統計を計算する."""
    n = len(df)
    if n == 0:
        return {
            "count": 0, "wr": 0.0, "avg_pips": 0.0,
            "total_pips": 0.0, "avg_mfe": 0.0, "avg_mae": 0.0,
        }
    return {
        "count": n,
        "wr": df["win"].mean() * 100,
        "avg_pips": df["pips"].mean(),
        "total_pips": df["pips"].sum(),
        "avg_mfe": df["mfe_pips"].mean(),
        "avg_mae": df["mae_pips"].mean(),
    }


def _format_pips(v: float) -> str:
    """pips値をフォーマットする."""
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:,.0f}"


def _df_to_md(df: pd.DataFrame) -> str:
    """DataFrameをMarkdownテーブルに変換する（tabulate不要）."""
    if df.empty:
        return "(データなし)"
    cols = list(df.columns)
    header = "| " + " | ".join(str(c) for c in cols) + " |"
    sep = "| " + " | ".join("---" for _ in cols) + " |"
    rows = []
    for _, row in df.iterrows():
        vals = []
        for c in cols:
            v = row[c]
            if isinstance(v, float):
                vals.append(f"{v:,.1f}" if abs(v) < 1e6 else f"{v:,.0f}")
            else:
                vals.append(str(v))
        rows.append("| " + " | ".join(vals) + " |")
    return "\n".join([header, sep, *rows])


def analyze_by_category(df: pd.DataFrame) -> pd.DataFrame:
    """ブロック理由カテゴリ別統計を集計する."""
    rows = []
    for cat, grp in df.groupby("category"):
        s = _stats(grp)
        rows.append({
            "カテゴリ": cat,
            "件数": int(s["count"]),
            "WR%": round(s["wr"], 1),
            "平均pips": round(s["avg_pips"], 2),
            "合計pips": round(s["total_pips"], 0),
            "平均MFE": round(s["avg_mfe"], 1),
            "平均MAE": round(s["avg_mae"], 1),
            "利益的?": "○" if s["wr"] > BREAKEVEN_WR * 100 else "×",
        })
    result = pd.DataFrame(rows).sort_values(
        "合計pips", ascending=False,
    )
    return result


def analyze_by_score_bucket(df: pd.DataFrame) -> pd.DataFrame:
    """コンセンサススコアバケット別統計を集計する."""
    bins = [0, 7, 8, 9, 10, 11, 12, 100]
    labels = ["<7", "7-8", "8-9", "9-10", "10-11", "11-12", "12+"]
    df = df.copy()
    df["score_bucket"] = pd.cut(
        df["consensus_score"], bins=bins, labels=labels, right=False,
    )
    rows = []
    for bucket, grp in df.groupby("score_bucket", observed=True):
        s = _stats(grp)
        rows.append({
            "スコア帯": str(bucket),
            "件数": int(s["count"]),
            "WR%": round(s["wr"], 1),
            "平均pips": round(s["avg_pips"], 2),
            "合計pips": round(s["total_pips"], 0),
            "利益的?": "○" if s["wr"] > BREAKEVEN_WR * 100 else "×",
        })
    return pd.DataFrame(rows)


def analyze_by_regime(df: pd.DataFrame) -> pd.DataFrame:
    """レジーム別×カテゴリ別統計."""
    rows = []
    for (regime, cat), grp in df.groupby(["regime", "category"]):
        s = _stats(grp)
        if s["count"] < 100:
            continue
        rows.append({
            "レジーム": regime,
            "カテゴリ": cat,
            "件数": int(s["count"]),
            "WR%": round(s["wr"], 1),
            "平均pips": round(s["avg_pips"], 2),
            "合計pips": round(s["total_pips"], 0),
        })
    result = pd.DataFrame(rows).sort_values(
        ["レジーム", "合計pips"], ascending=[True, False],
    )
    return result


def analyze_is_oos(df: pd.DataFrame) -> pd.DataFrame:
    """IS/OOS期間比較: カテゴリ別のWR安定性を評価する."""
    rows = []
    for cat in sorted(df["category"].unique()):
        cat_df = df[df["category"] == cat]
        is_df = cat_df[cat_df["period"] == "IS(2020-2023)"]
        oos_df = cat_df[cat_df["period"] == "OOS(2024-2025)"]
        is_s = _stats(is_df)
        oos_s = _stats(oos_df)
        diff = oos_s["wr"] - is_s["wr"]
        rows.append({
            "カテゴリ": cat,
            "IS件数": int(is_s["count"]),
            "IS_WR%": round(is_s["wr"], 1),
            "OOS件数": int(oos_s["count"]),
            "OOS_WR%": round(oos_s["wr"], 1),
            "WR差(pp)": round(diff, 1),
            "安定?": "○" if abs(diff) <= 5 else "×",
        })
    return pd.DataFrame(rows).sort_values(
        "WR差(pp)", key=abs, ascending=False,
    )


def analyze_score_threshold_sweep(df: pd.DataFrame) -> pd.DataFrame:
    """スコア閾値スイープ: 閾値を変えたときの追加トレード影響.

    「もし閾値をX以上にしていたら、追加で取れたトレード」を累積評価。
    """
    rows = []
    # Score_belowカテゴリのみ対象
    score_df = df[df["category"] == "Score_below"].copy()
    if score_df.empty:
        return pd.DataFrame()

    thresholds = [7.0, 8.0, 8.5, 9.0, 9.5, 10.0, 10.5, 11.0]
    for thr in thresholds:
        # この閾値以上のスコアを持つブロックシグナル
        above = score_df[score_df["consensus_score"] >= thr]
        s = _stats(above)
        rows.append({
            "閾値≥": thr,
            "追加件数": int(s["count"]),
            "WR%": round(s["wr"], 1),
            "平均pips": round(s["avg_pips"], 2),
            "合計pips": round(s["total_pips"], 0),
            "利益的?": "○" if s["wr"] > BREAKEVEN_WR * 100 else "×",
        })
    return pd.DataFrame(rows)


def generate_report(
    symbol: str,
    df: pd.DataFrame,
) -> str:
    """1ペア分の分析レポートを生成する."""
    lines: list[str] = []
    lines.append(f"# What-If分析: {symbol}")
    lines.append("")
    lines.append(f"- データ件数: {len(df):,}")
    lines.append(
        f"- 期間: {df['year'].min()}-{df['year'].max()}"
    )
    lines.append(
        f"- SL/TP固定: {FIXED_SL}/{FIXED_TP} pips"
    )
    lines.append(
        f"- 損益分岐WR: {BREAKEVEN_WR*100:.1f}%"
    )
    overall = _stats(df)
    lines.append(
        f"- 全体WR: {overall['wr']:.1f}%"
    )
    lines.append("")

    # 1. カテゴリ別統計
    lines.append("## 1. ブロック理由カテゴリ別統計")
    lines.append("")
    cat_df = analyze_by_category(df)
    lines.append(_df_to_md(cat_df))
    lines.append("")

    # 主要発見
    profitable = cat_df[cat_df["利益的?"] == "○"]
    if not profitable.empty:
        lines.append("### 改善候補（WR > 損益分岐点）")
        lines.append("")
        for _, row in profitable.iterrows():
            lines.append(
                f"- **{row['カテゴリ']}**: "
                f"WR {row['WR%']}%, "
                f"合計 {_format_pips(row['合計pips'])} pips "
                f"({int(row['件数']):,}件)"
            )
        lines.append("")

    # 2. スコアバケット分析
    lines.append("## 2. コンセンサススコア帯別統計")
    lines.append("")
    score_df = analyze_by_score_bucket(df)
    lines.append(_df_to_md(score_df))
    lines.append("")

    # 3. スコア閾値スイープ
    lines.append("## 3. スコア閾値スイープ（Score_belowのみ）")
    lines.append("")
    sweep_df = analyze_score_threshold_sweep(df)
    if not sweep_df.empty:
        lines.append(_df_to_md(sweep_df))
    else:
        lines.append("Score_belowデータなし")
    lines.append("")

    # 4. レジーム別分析
    lines.append("## 4. レジーム別×カテゴリ別統計")
    lines.append("")
    regime_df = analyze_by_regime(df)
    if not regime_df.empty:
        lines.append(_df_to_md(regime_df))
    else:
        lines.append("十分なデータなし")
    lines.append("")

    # 5. IS/OOS比較
    lines.append("## 5. IS/OOS安定性比較")
    lines.append("")
    isoos_df = analyze_is_oos(df)
    lines.append(_df_to_md(isoos_df))
    lines.append("")

    unstable = isoos_df[isoos_df["安定?"] == "×"]
    if not unstable.empty:
        lines.append("### 不安定カテゴリ（WR差 > 5pp）")
        lines.append("")
        for _, row in unstable.iterrows():
            lines.append(
                f"- **{row['カテゴリ']}**: "
                f"IS {row['IS_WR%']}% → OOS {row['OOS_WR%']}% "
                f"(差 {row['WR差(pp)']:+.1f}pp)"
            )
        lines.append("")

    # 6. 結論
    lines.append("## 6. 結論・推奨アクション")
    lines.append("")
    if not profitable.empty and not isoos_df.empty:
        stable_profitable = []
        for _, row in profitable.iterrows():
            cat = row["カテゴリ"]
            isoos_row = isoos_df[isoos_df["カテゴリ"] == cat]
            if not isoos_row.empty:
                stable = isoos_row.iloc[0]["安定?"] == "○"
                stable_profitable.append(
                    (cat, row["WR%"], row["合計pips"], stable)
                )
        for cat, wr, total, stable in sorted(
            stable_profitable, key=lambda x: -x[2],
        ):
            status = "安定" if stable else "不安定(要注意)"
            lines.append(
                f"- **{cat}**: WR {wr}%, "
                f"合計 {_format_pips(total)} pips "
                f"[{status}]"
            )
        lines.append("")

    return "\n".join(lines)


def generate_cross_pair_summary(
    all_results: dict[str, pd.DataFrame],
) -> str:
    """クロスペアサマリーを生成する."""
    lines: list[str] = []
    lines.append("# What-If分析: 全ペアクロスサマリー")
    lines.append("")

    # カテゴリ×ペア マトリクス（合計pips）
    symbols = list(all_results.keys())
    all_cats: set[str] = set()
    cat_data: dict[str, dict[str, float]] = {}

    for sym, df in all_results.items():
        cat_df = analyze_by_category(df)
        for _, row in cat_df.iterrows():
            cat = row["カテゴリ"]
            all_cats.add(cat)
            if cat not in cat_data:
                cat_data[cat] = {}
            cat_data[cat][sym] = row["合計pips"]

    lines.append("## 合計pips マトリクス（カテゴリ × ペア）")
    lines.append("")

    # ヘッダ
    header = "| カテゴリ | " + " | ".join(symbols) + " |"
    sep = "|" + "---|" * (len(symbols) + 1)
    lines.append(header)
    lines.append(sep)

    for cat in sorted(all_cats):
        vals = []
        for sym in symbols:
            v = cat_data.get(cat, {}).get(sym, 0)
            vals.append(_format_pips(v))
        lines.append(f"| {cat} | " + " | ".join(vals) + " |")
    lines.append("")

    # WRマトリクス
    wr_data: dict[str, dict[str, float]] = {}
    for sym, df in all_results.items():
        cat_df = analyze_by_category(df)
        for _, row in cat_df.iterrows():
            cat = row["カテゴリ"]
            if cat not in wr_data:
                wr_data[cat] = {}
            wr_data[cat][sym] = row["WR%"]

    lines.append("## WR% マトリクス（カテゴリ × ペア）")
    lines.append("")
    lines.append(header)
    lines.append(sep)

    for cat in sorted(all_cats):
        vals = []
        for sym in symbols:
            v = wr_data.get(cat, {}).get(sym, 0)
            mark = " ○" if v > BREAKEVEN_WR * 100 else ""
            vals.append(f"{v:.1f}{mark}")
        lines.append(f"| {cat} | " + " | ".join(vals) + " |")
    lines.append("")

    # ペア別改善候補サマリー
    lines.append("## ペア別改善候補")
    lines.append("")
    for sym, df in all_results.items():
        cat_df = analyze_by_category(df)
        profitable = cat_df[cat_df["利益的?"] == "○"]
        if profitable.empty:
            lines.append(f"### {sym}: 改善候補なし")
        else:
            lines.append(f"### {sym}")
            for _, row in profitable.sort_values(
                "合計pips", ascending=False,
            ).iterrows():
                lines.append(
                    f"- {row['カテゴリ']}: "
                    f"WR {row['WR%']}%, "
                    f"{_format_pips(row['合計pips'])} pips"
                )
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    """メインエントリポイント."""
    parser = argparse.ArgumentParser(
        description="What-If分析: ブロックシグナルの機会損失定量化",
    )
    parser.add_argument(
        "--symbol",
        type=str,
        help="分析対象の通貨ペア（例: USDJPY）",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="全ペアを分析してクロスサマリーも出力",
    )
    args = parser.parse_args()

    if not args.symbol and not args.all:
        parser.error("--symbol または --all を指定してください")

    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    if args.symbol:
        symbols = [args.symbol.upper()]
    else:
        symbols = ALL_SYMBOLS

    all_results: dict[str, pd.DataFrame] = {}

    for symbol in symbols:
        try:
            df = _load_whatif_trades(symbol)
        except FileNotFoundError as e:
            print(f"SKIP: {e}", file=sys.stderr)
            continue

        print(f"分析中: {symbol} ({len(df):,}件)")
        report = generate_report(symbol, df)

        out_path = _REPORTS_DIR / f"whatif_analysis_{symbol}.md"
        out_path.write_text(report, encoding="utf-8")
        print(f"出力: {out_path}")

        all_results[symbol] = df

    # クロスペアサマリー
    if len(all_results) > 1:
        summary = generate_cross_pair_summary(all_results)
        summary_path = (
            _REPORTS_DIR / "whatif_analysis_all_pairs.md"
        )
        summary_path.write_text(summary, encoding="utf-8")
        print(f"出力: {summary_path}")

    # 結果プレビュー（単一ペア時）
    if len(all_results) == 1:
        sym = list(all_results.keys())[0]
        df = all_results[sym]
        print("\n--- カテゴリ別統計 ---")
        print(analyze_by_category(df).to_string(index=False))
        print("\n--- スコア閾値スイープ ---")
        sweep = analyze_score_threshold_sweep(df)
        sweep.columns = [
            c.replace("≥", ">=") for c in sweep.columns
        ]
        print(sweep.to_string(index=False))


if __name__ == "__main__":
    main()
