"""ストレステスト結果レポート生成

全フェーズのBT結果を収集し、合格基準判定付きMarkdownレポートを生成する。

使用方法:
    uv run python scripts/stress_test_report.py
    uv run python scripts/stress_test_report.py --mc-result reports/stress_p5_mc_0000074.json
    uv run python scripts/stress_test_report.py --baseline 0000074
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from autotrader.config.paths import get_results_dir

# 合格基準
CRITERIA = {
    "p1": {"pf_min": 1.8, "dd_max": 5.0, "annual_min": 30.0},
    "p2": {"pf_drop_max": 20.0, "wr_min": 75.0},
    "p3": {},  # なだらかな山（定量判定不可、形状出力のみ）
    "p4": {"pf_min": 1.5, "dd_max": 10.0},
    "p5": {"pf_worst_min": 1.3, "dd_worst_max": 15.0},
}


def _load_result(result_id: str) -> dict | None:
    """result.jsonを読み込む"""
    results_dir = get_results_dir()
    path = results_dir / result_id / "result.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _find_stress_results() -> dict[str, list[dict]]:
    """stress_で始まる結果を収集してフェーズ別に分類"""
    results_dir = get_results_dir()
    if not results_dir.exists():
        return {}

    phase_results: dict[str, list[dict]] = {
        "p1": [], "p2": [], "p3": [], "p4": [],
    }

    for d in sorted(results_dir.iterdir()):
        if not d.is_dir():
            continue
        result_path = d / "result.json"
        config_path = d / "job_config.json"
        if not result_path.exists():
            continue

        result = json.loads(
            result_path.read_text(encoding="utf-8"),
        )
        job_id = result.get("job_id", d.name)

        # stress_p* または ss_p* プレフィックスを検出
        if not (
            job_id.startswith("stress_p")
            or job_id.startswith("ss_p")
        ):
            continue

        # フェーズ判定
        if "p1_" in job_id[:8]:
            phase = "p1"
        elif "p2_" in job_id[:8]:
            phase = "p2"
        elif "p3_" in job_id[:8]:
            phase = "p3"
        elif "p4_" in job_id[:8]:
            phase = "p4"
        else:
            continue

        # job_config.jsonからdescription取得
        desc = result.get("description", "")
        if config_path.exists():
            cfg = json.loads(
                config_path.read_text(encoding="utf-8"),
            )
            desc = cfg.get("description", desc)

        phase_results[phase].append({
            "job_id": job_id,
            "description": desc,
            "pf": result.get("profit_factor", 0.0),
            "dd": result.get("max_drawdown", 0.0),
            "wr": result.get("win_rate", 0.0),
            "net": result.get("net_profit", 0.0),
            "trades": result.get("trades", 0),
            "sharpe": result.get("sharpe_ratio", 0.0),
        })

    return phase_results


def _judge(ok: bool) -> str:
    """合否判定マーク"""
    return "PASS" if ok else "FAIL"


def _pf_drop_pct(baseline_pf: float, test_pf: float) -> float:
    """PF低下率(%)"""
    if baseline_pf == 0:
        return 100.0
    return round(
        (1.0 - test_pf / baseline_pf) * 100, 1,
    )


def generate_phase1_table(
    results: list[dict],
) -> str:
    """Phase 1: 約定ストレス テーブル"""
    if not results:
        return "データなし\n"

    lines = [
        "| 条件 | PF | DD(%) | Net(千円) | Sharpe | 判定 |",
        "|------|-----|-------|----------|--------|------|",
    ]
    crit = CRITERIA["p1"]
    for r in results:
        pf_ok = r["pf"] >= crit["pf_min"]
        dd_ok = r["dd"] <= crit["dd_max"]
        ok = pf_ok and dd_ok
        desc = r["description"].replace("[STRESS] ", "")
        lines.append(
            f"| {desc} | {r['pf']:.2f} "
            f"| {r['dd']:.2f} "
            f"| {r['net'] / 1000:.0f} "
            f"| {r['sharpe']:.2f} "
            f"| {_judge(ok)} |",
        )
    return "\n".join(lines) + "\n"


def generate_phase2_table(
    results: list[dict],
    baseline_pf: float = 0.0,
) -> str:
    """Phase 2: ノイズ耐性 テーブル"""
    if not results:
        return "データなし\n"

    lines = [
        "| ノイズ | PF | PF変化(%) | 勝率(%) | 判定 |",
        "|--------|-----|-----------|---------|------|",
    ]
    crit = CRITERIA["p2"]
    for r in results:
        drop = _pf_drop_pct(baseline_pf, r["pf"])
        drop_ok = drop <= crit["pf_drop_max"]
        wr_ok = r["wr"] >= crit["wr_min"]
        ok = drop_ok and wr_ok
        desc = r["description"].replace("[STRESS] ", "")
        lines.append(
            f"| {desc} | {r['pf']:.2f} "
            f"| {drop:+.1f} "
            f"| {r['wr']:.1f} "
            f"| {_judge(ok)} |",
        )
    return "\n".join(lines) + "\n"


def generate_phase3_table(
    results: list[dict],
) -> str:
    """Phase 3: パラメータ耐性 テーブル（ヒートマップ風）"""
    if not results:
        return "データなし\n"

    # パラメータ名でグルーピング
    params: dict[str, list[dict]] = {}
    for r in results:
        # ss_p3_{param}_{mult}x から param を抽出
        jid = r["job_id"]
        _stripped = jid.replace("ss_p3_", "").replace(
            "stress_p3_", "",
        )
        parts = _stripped.rsplit("_", 1)
        if len(parts) == 2:
            param_name = parts[0]
        else:
            param_name = jid
        params.setdefault(param_name, []).append(r)

    lines = [
        "| パラメータ | 0.8x | 0.9x | 1.0x | 1.1x | 1.2x | 形状 |",
        "|-----------|------|------|------|------|------|------|",
    ]

    for param_name, param_results in sorted(params.items()):
        # 倍率順にソート
        param_results.sort(key=lambda x: x["job_id"])
        pfs = [r["pf"] for r in param_results]

        # 5水準揃っているか確認
        if len(pfs) == 5:
            peak_idx = pfs.index(max(pfs))
            # 形状判定: 中央付近にピークがあれば「なだらかな山」
            if 1 <= peak_idx <= 3:
                shape = "山"
            elif peak_idx == 0:
                shape = "右下がり"
            else:
                shape = "右上がり"
            # 変動幅
            _range = max(pfs) - min(pfs)
            if _range < 0.2:
                shape += "(安定)"
        else:
            shape = f"({len(pfs)}/5)"

        pf_strs = [f"{pf:.2f}" for pf in pfs]
        while len(pf_strs) < 5:
            pf_strs.append("-")

        lines.append(
            f"| {param_name} "
            f"| {pf_strs[0]} | {pf_strs[1]} "
            f"| {pf_strs[2]} | {pf_strs[3]} "
            f"| {pf_strs[4]} | {shape} |",
        )

    return "\n".join(lines) + "\n"


def generate_phase4_table(
    results: list[dict],
) -> str:
    """Phase 4: レジーム別 テーブル"""
    if not results:
        return "データなし\n"

    lines = [
        "| 条件 | PF | DD(%) | WR(%) | Trades | 判定 |",
        "|------|-----|-------|-------|--------|------|",
    ]
    crit = CRITERIA["p4"]
    for r in results:
        pf_ok = r["pf"] >= crit["pf_min"]
        dd_ok = r["dd"] <= crit["dd_max"]
        ok = pf_ok and dd_ok
        desc = r["description"].replace("[STRESS] ", "")
        lines.append(
            f"| {desc} | {r['pf']:.2f} "
            f"| {r['dd']:.2f} "
            f"| {r['wr']:.1f} "
            f"| {r['trades']} "
            f"| {_judge(ok)} |",
        )
    return "\n".join(lines) + "\n"


def generate_phase5_table(
    mc_result: dict | None,
) -> str:
    """Phase 5: モンテカルロ テーブル"""
    if mc_result is None:
        return "データなし（stress_test_monte_carlo.py を実行してください）\n"

    lines = [
        "| 分析 | PF(p5) | PF(avg) | PF(worst) "
        "| DD(p95) | DD(worst) | 判定 |",
        "|------|--------|---------|-----------|"
        "---------|-----------|------|",
    ]
    crit = CRITERIA["p5"]
    for analysis in mc_result.get("analyses", []):
        pf_worst = analysis.get("pf_worst", 0.0)
        dd_worst = analysis.get("dd_worst", 0.0)
        pf_ok = pf_worst >= crit["pf_worst_min"]
        dd_ok = dd_worst <= crit["dd_worst_max"]
        ok = pf_ok and dd_ok
        lines.append(
            f"| {analysis['method']} "
            f"| {analysis.get('pf_p5', 0.0):.3f} "
            f"| {analysis.get('pf_mean', 0.0):.3f} "
            f"| {pf_worst:.3f} "
            f"| {analysis.get('dd_p95', 0.0):.2f}% "
            f"| {dd_worst:.2f}% "
            f"| {_judge(ok)} |",
        )
    return "\n".join(lines) + "\n"


def generate_summary(
    phase_results: dict[str, list[dict]],
    mc_result: dict | None,
    baseline_pf: float,
) -> str:
    """総合判定セクション"""
    results: dict[str, str] = {}

    # P1判定
    crit1 = CRITERIA["p1"]
    if phase_results["p1"]:
        all_ok = all(
            r["pf"] >= crit1["pf_min"] and r["dd"] <= crit1["dd_max"]
            for r in phase_results["p1"]
        )
        results["P1 約定ストレス"] = _judge(all_ok)
    else:
        results["P1 約定ストレス"] = "未実行"

    # P2判定
    crit2 = CRITERIA["p2"]
    if phase_results["p2"]:
        all_ok = all(
            _pf_drop_pct(baseline_pf, r["pf"]) <= crit2["pf_drop_max"]
            and r["wr"] >= crit2["wr_min"]
            for r in phase_results["p2"]
        )
        results["P2 ノイズ耐性"] = _judge(all_ok)
    else:
        results["P2 ノイズ耐性"] = "未実行"

    # P3判定（自動判定困難、手動確認推奨）
    if phase_results["p3"]:
        results["P3 パラメータ耐性"] = "要手動確認"
    else:
        results["P3 パラメータ耐性"] = "未実行"

    # P4判定
    crit4 = CRITERIA["p4"]
    if phase_results["p4"]:
        all_ok = all(
            r["pf"] >= crit4["pf_min"] and r["dd"] <= crit4["dd_max"]
            for r in phase_results["p4"]
        )
        results["P4 レジーム破壊"] = _judge(all_ok)
    else:
        results["P4 レジーム破壊"] = "未実行"

    # P5判定
    crit5 = CRITERIA["p5"]
    if mc_result and mc_result.get("analyses"):
        all_ok = all(
            a.get("pf_worst", 0) >= crit5["pf_worst_min"]
            and a.get("dd_worst", 100) <= crit5["dd_worst_max"]
            for a in mc_result["analyses"]
        )
        results["P5 モンテカルロ"] = _judge(all_ok)
    else:
        results["P5 モンテカルロ"] = "未実行"

    lines = [
        "| フェーズ | 判定 |",
        "|---------|------|",
    ]
    for phase, verdict in results.items():
        lines.append(f"| {phase} | {verdict} |")

    # 総合
    verdicts = list(results.values())
    has_fail = "FAIL" in verdicts
    has_not_run = "未実行" in verdicts
    if has_fail:
        overall = "FAIL"
    elif has_not_run:
        overall = "未完了（一部フェーズ未実行）"
    elif all(v in ("PASS", "要手動確認") for v in verdicts):
        overall = "PASS（P3要手動確認）"
    else:
        overall = "未完了"

    lines.append(f"\n**総合判定: {overall}**")
    return "\n".join(lines) + "\n"


def generate_report(
    *,
    baseline_id: str | None = None,
    mc_result_path: str | None = None,
) -> str:
    """Markdownレポートを生成

    Args:
        baseline_id: ベースラインBT結果ID（PF低下率計算用）
        mc_result_path: Phase 5モンテカルロ結果JSONパス

    Returns:
        Markdownレポート文字列
    """
    # ベースラインPF取得
    baseline_pf = 0.0
    if baseline_id:
        bl = _load_result(baseline_id)
        if bl:
            baseline_pf = bl.get("profit_factor", 0.0)

    # ストレステスト結果収集
    phase_results = _find_stress_results()

    # MC結果読み込み
    mc_result = None
    if mc_result_path:
        mc_path = Path(mc_result_path)
        if mc_path.exists():
            mc_result = json.loads(
                mc_path.read_text(encoding="utf-8"),
            )

    # レポート組み立て
    sections = [
        "# ストレステスト結果\n",
    ]

    if baseline_pf > 0:
        sections.append(
            f"ベースラインPF: {baseline_pf:.2f}"
            f"（結果ID: {baseline_id}）\n",
        )

    # 各フェーズ
    sections.append("## 1. 約定ストレス\n")
    sections.append(generate_phase1_table(phase_results["p1"]))

    sections.append("\n## 2. ノイズ耐性\n")
    sections.append(
        generate_phase2_table(phase_results["p2"], baseline_pf),
    )

    sections.append("\n## 3. パラメータ耐性\n")
    sections.append(generate_phase3_table(phase_results["p3"]))

    sections.append("\n## 4. レジーム別\n")
    sections.append(generate_phase4_table(phase_results["p4"]))

    sections.append("\n## 5. モンテカルロ\n")
    sections.append(generate_phase5_table(mc_result))

    sections.append("\n## 総合判定\n")
    sections.append(
        generate_summary(phase_results, mc_result, baseline_pf),
    )

    return "\n".join(sections)


def main() -> None:
    """メインエントリーポイント"""
    parser = argparse.ArgumentParser(
        description="ストレステスト結果レポート生成",
    )
    parser.add_argument(
        "--baseline",
        type=str,
        default=None,
        help="ベースラインBT結果ID（PF低下率計算用）",
    )
    parser.add_argument(
        "--mc-result",
        type=str,
        default=None,
        help="Phase 5 MC結果JSONパス",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="出力ファイルパス",
    )
    args = parser.parse_args()

    report = generate_report(
        baseline_id=args.baseline,
        mc_result_path=args.mc_result,
    )

    # 出力先決定
    if args.output:
        out_path = Path(args.output)
    else:
        reports_dir = _project_root / "reports"
        reports_dir.mkdir(exist_ok=True)
        out_path = reports_dir / "stress_test_report.md"

    out_path.write_text(report, encoding="utf-8")
    print(f"レポート出力: {out_path}")
    print()
    print(report)


if __name__ == "__main__":
    main()
