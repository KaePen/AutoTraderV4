"""トレードフロー分析モジュール

シグナル生成パイプラインの各段階でのフィルタリング状況を
収集・分析し、ボトルネックを特定する。
"""

from __future__ import annotations

from collections import defaultdict

__all__ = ["TradeFlowAnalyzer"]


class TradeFlowAnalyzer:
    """トレードフロー分析器

    _generate_signal_newの各段階でのデータを収集し、
    フィルタリングファネルや勝率分析を行う。
    """

    def __init__(self) -> None:
        """初期化"""
        self._records: list[SignalStepRecord] = []

    def collect(self, record: SignalStepRecord) -> None:
        """レコードを収集

        Args:
            record: シグナルステップ記録
        """
        self._records.append(record)

    @property
    def records(self) -> list[SignalStepRecord]:
        """収集済みレコード"""
        return self._records

    def generate_report(
        self,
        closed_trades: list | None = None,
        trade_timestamps: list | None = None,
        trade_modes: list[str] | None = None,
        trade_regimes: list[str] | None = None,
        trade_consensus_scores: list[float] | None = None,
    ) -> str:
        """分析レポートを生成

        Args:
            closed_trades: 決済済みトレードリスト
            trade_timestamps: トレード開始時刻リスト
            trade_modes: トレード別モードリスト
            trade_regimes: トレード別レジームリスト
            trade_consensus_scores: トレード別コンセンサススコア

        Returns:
            str: テキストレポート
        """
        lines: list[str] = []
        total = len(self._records)
        if total == 0:
            return "レコードなし"

        lines.append("=" * 64)
        lines.append("トレードフロー分析レポート")
        lines.append("=" * 64)
        lines.append("")

        # 1. フィルタリングファネル
        lines.extend(self._build_funnel_section(total))
        lines.append("")

        # 2. コンセンサススコア分布
        lines.extend(self._build_score_distribution(total))
        lines.append("")

        # 3. モード選択分布
        lines.extend(self._build_mode_distribution(total))
        lines.append("")

        # 4. 勝率の多次元分析
        if closed_trades:
            lines.extend(self._build_win_rate_analysis(
                closed_trades,
                trade_modes or [],
                trade_regimes or [],
                trade_consensus_scores or [],
            ))
            lines.append("")

            # 5. TP/SL分析
            lines.extend(self._build_tp_sl_analysis(closed_trades))
            lines.append("")

        # 6. HTFフィルター効果
        lines.extend(self._build_htf_analysis())
        lines.append("")

        # 7. 改善示唆
        lines.extend(self._build_suggestions())

        return "\n".join(lines)

    def _build_funnel_section(self, total: int) -> list[str]:
        """フィルタリングファネルセクション"""
        lines = ["1. フィルタリングファネル"]
        lines.append("-" * 64)

        risk_fail = sum(
            1 for r in self._records if not r.risk_passed
        )
        # リスク通過後のレコード
        risk_passed = [r for r in self._records if r.risk_passed]
        consensus_fail = sum(
            1 for r in risk_passed
            if not r.consensus_passed and r.hold_reason
            and "スコア不足" in r.hold_reason
        )
        htf_fail = sum(
            1 for r in risk_passed
            if r.consensus_passed and not r.htf_passed
        )
        primary_fail = sum(
            1 for r in risk_passed
            if r.consensus_passed and r.htf_passed
            and r.hold_reason == "primary_tfデータなし"
        )
        signals = sum(
            1 for r in self._records
            if r.final_direction not in ("HOLD", "")
        )
        buy_signals = sum(
            1 for r in self._records if r.final_direction == "BUY"
        )
        sell_signals = sum(
            1 for r in self._records if r.final_direction == "SELL"
        )

        def _pct(n: int) -> str:
            return f"{n / total * 100:5.1f}%" if total else "  0.0%"

        lines.append(
            f"  全バー数:              {total:>8} (100.0%)"
        )
        lines.append(
            f"  +-- リスク管理除外:     {risk_fail:>8} ({_pct(risk_fail)})"
        )
        lines.append(
            f"  +-- コンセンサス不足:   {consensus_fail:>8} ({_pct(consensus_fail)})"
        )

        # コンセンサススコア分布（リスク通過後全体）
        score_bins = self._bin_scores(risk_passed)
        if score_bins:
            parts = []
            for label, count in score_bins.items():
                rp_total = len(risk_passed) or 1
                parts.append(
                    f"{label}: {count / rp_total * 100:.0f}%"
                )
            lines.append(
                f"  |   スコア分布: {', '.join(parts)}"
            )

        lines.append(
            f"  +-- HTFフィルター除外:  {htf_fail:>8} ({_pct(htf_fail)})"
        )
        lines.append(
            f"  +-- primary_tfなし:     {primary_fail:>8} ({_pct(primary_fail)})"
        )
        lines.append(
            f"  +-- シグナル発生:       {signals:>8} ({_pct(signals)})"
        )
        lines.append(
            f"      +-- BUY:            {buy_signals:>8}"
        )
        lines.append(
            f"      +-- SELL:           {sell_signals:>8}"
        )
        return lines

    def _bin_scores(
        self, records: list[SignalStepRecord],
    ) -> dict[str, int]:
        """スコアをビン分割"""
        bins = {
            "[0,1)": 0, "[1,2)": 0, "[2,3)": 0,
            "[3,4)": 0, "[4,5)": 0, "[5+)": 0,
        }
        for r in records:
            s = r.consensus_score
            if s < 1:
                bins["[0,1)"] += 1
            elif s < 2:
                bins["[1,2)"] += 1
            elif s < 3:
                bins["[2,3)"] += 1
            elif s < 4:
                bins["[3,4)"] += 1
            elif s < 5:
                bins["[4,5)"] += 1
            else:
                bins["[5+)"] += 1
        return bins

    def _build_score_distribution(self, total: int) -> list[str]:
        """コンセンサススコア分布セクション"""
        lines = ["2. コンセンサススコア分布（リスク通過後）"]
        lines.append("-" * 64)

        risk_passed = [r for r in self._records if r.risk_passed]
        # 細かいビン
        fine_bins = [
            (0, 1), (1, 2), (2, 3), (3, 3.5),
            (3.5, 4.5), (4.5, 6.0), (6.0, 100),
        ]
        labels = [
            "[0, 1)", "[1, 2)", "[2, 3)", "[3, 3.5)",
            "[3.5, 4.5)", "[4.5, 6.0)", "[6.0+)",
        ]
        # 閾値マッピング
        mode_note = {
            "[0, 1)": "HOLD",
            "[1, 2)": "HOLD",
            "[2, 3)": "HOLD",
            "[3, 3.5)": "HOLD(低スコア)",
            "[3.5, 4.5)": "低スコア通過",
            "[4.5, 6.0)": "中スコア通過",
            "[6.0+)": "全通過",
        }

        lines.append(
            f"  {'スコア範囲':<14} | {'件数':>7} | {'割合':>6} | 判定"
        )
        lines.append(f"  {'-' * 50}")

        rp_total = len(risk_passed) or 1
        for i, (lo, hi) in enumerate(fine_bins):
            count = sum(
                1 for r in risk_passed
                if lo <= r.consensus_score < hi
            )
            pct = count / rp_total * 100
            lines.append(
                f"  {labels[i]:<14} | {count:>7} | {pct:>5.1f}% | "
                f"{mode_note[labels[i]]}"
            )

        lines.append("  ※ UNIVERSAL閾値=4.5")
        return lines

    def _build_mode_distribution(self, total: int) -> list[str]:
        """モード選択分布セクション"""
        lines = ["3. モード選択分布"]
        lines.append("-" * 64)

        mode_counts: dict[str, int] = defaultdict(int)
        mode_signals: dict[str, int] = defaultdict(int)

        for r in self._records:
            if r.mode:
                mode_counts[r.mode] += 1
                if r.final_direction not in ("HOLD", ""):
                    mode_signals[r.mode] += 1

        for mode in ["UNIVERSAL"]:
            mc = mode_counts.get(mode, 0)
            ms = mode_signals.get(mode, 0)
            pct = mc / total * 100 if total else 0
            sig_rate = ms / mc * 100 if mc else 0
            lines.append(
                f"  {mode:<12} {mc:>8} ({pct:>5.1f}%) "
                f"-> シグナル: {ms:>6} ({sig_rate:>5.1f}%)"
            )

        return lines

    def _build_win_rate_analysis(
        self,
        trades: list,
        modes: list[str],
        regimes: list[str],
        scores: list[float],
    ) -> list[str]:
        """勝率の多次元分析セクション"""
        lines = ["4. 勝率の多次元分析"]
        lines.append("-" * 64)

        # 4a. モード別勝率
        lines.append("  4a. モード別勝率")
        mode_trades: dict[str, list] = defaultdict(list)
        for i, t in enumerate(trades):
            m = modes[i] if i < len(modes) else "UNKNOWN"
            mode_trades[m].append(t)

        for mode in ["UNIVERSAL", "UNKNOWN"]:
            tlist = mode_trades.get(mode, [])
            if not tlist:
                continue
            wins = sum(1 for t in tlist if (t.profit_loss or 0) > 0)
            wr = wins / len(tlist) * 100 if tlist else 0
            gp = sum(
                (t.profit_loss or 0) for t in tlist
                if (t.profit_loss or 0) > 0
            )
            gl = abs(sum(
                (t.profit_loss or 0) for t in tlist
                if (t.profit_loss or 0) < 0
            ))
            pf = gp / gl if gl > 0 else 0
            lines.append(
                f"  {mode:<12} {wr:>5.1f}% "
                f"({len(tlist)} trades, PF {pf:.2f})"
            )

        # 4b. 時間帯別
        lines.append("")
        lines.append("  4b. 時間帯別(UTC)")
        hour_bins = {
            "00-06 東京": (0, 6),
            "07-12 ロンドン": (7, 12),
            "13-20 NY": (13, 20),
            "21-23 その他": (21, 24),
        }
        for label, (h_start, h_end) in hour_bins.items():
            tlist = [
                t for t in trades
                if t.opened_at
                and h_start <= t.opened_at.hour < h_end
            ]
            if not tlist:
                continue
            wins = sum(1 for t in tlist if (t.profit_loss or 0) > 0)
            wr = wins / len(tlist) * 100
            lines.append(
                f"  {label:<18} {wr:>5.1f}% ({len(tlist)} trades)"
            )

        # 4c. レジーム別
        lines.append("")
        lines.append("  4c. レジーム別")
        regime_trades: dict[str, list] = defaultdict(list)
        for i, t in enumerate(trades):
            rg = regimes[i] if i < len(regimes) else "UNKNOWN"
            regime_trades[rg].append(t)

        for regime in ["TREND", "RANGE", "HIGH_VOL", "LOW_VOL", "UNKNOWN"]:
            tlist = regime_trades.get(regime, [])
            if not tlist:
                continue
            wins = sum(1 for t in tlist if (t.profit_loss or 0) > 0)
            wr = wins / len(tlist) * 100
            lines.append(
                f"  {regime:<12} {wr:>5.1f}% ({len(tlist)} trades)"
            )

        # 4d. コンセンサススコア帯別
        lines.append("")
        lines.append("  4d. コンセンサススコア帯別")
        score_bins_def = [
            ("[3.5, 4.0)", 3.5, 4.0),
            ("[4.0, 5.0)", 4.0, 5.0),
            ("[5.0, 6.0)", 5.0, 6.0),
            ("[6.0+)", 6.0, 100),
        ]
        for label, lo, hi in score_bins_def:
            indices = [
                i for i, s in enumerate(scores)
                if lo <= s < hi and i < len(trades)
            ]
            if not indices:
                continue
            tlist = [trades[i] for i in indices]
            wins = sum(1 for t in tlist if (t.profit_loss or 0) > 0)
            wr = wins / len(tlist) * 100
            lines.append(
                f"  {label:<14} {wr:>5.1f}% ({len(tlist)} trades)"
            )

        return lines

    def _build_tp_sl_analysis(self, trades: list) -> list[str]:
        """TP/SL分析セクション"""
        lines = ["5. TP/SL分析"]
        lines.append("-" * 64)

        # 決済理由分布
        exit_counts: dict[str, int] = defaultdict(int)
        for t in trades:
            reason = t.exit_reason.value if t.exit_reason else "UNKNOWN"
            exit_counts[reason] += 1

        lines.append("  決済理由分布:")
        total_t = len(trades) or 1
        for reason in [
            "STOP_LOSS", "TAKE_PROFIT", "SIGNAL_REVERSAL",
            "FORCE_CLOSE", "TIME_EXIT", "TRAILING_STOP",
        ]:
            count = exit_counts.get(reason, 0)
            if count == 0:
                continue
            pct = count / total_t * 100
            lines.append(
                f"    {reason:<18} {pct:>5.1f}% ({count}件)"
            )

        # 実効RR比（SLとTPで決済したトレードのみ）
        lines.append("")
        lines.append("  実効RR比 (TP決済の平均利益 / SL決済の平均損失):")
        sl_trades = [
            t for t in trades
            if t.exit_reason and t.exit_reason.value == "STOP_LOSS"
        ]
        tp_trades = [
            t for t in trades
            if t.exit_reason and t.exit_reason.value == "TAKE_PROFIT"
        ]
        avg_sl_loss = 0.0
        if sl_trades:
            avg_sl_loss = abs(sum(
                (t.profit_loss or 0) for t in sl_trades
            ) / len(sl_trades))
        avg_tp_gain = 0.0
        if tp_trades:
            avg_tp_gain = sum(
                (t.profit_loss or 0) for t in tp_trades
            ) / len(tp_trades)
        effective_rr = (
            avg_tp_gain / avg_sl_loss if avg_sl_loss > 0 else 0
        )
        lines.append(
            f"    平均TP利益: {avg_tp_gain:>10,.0f}  "
            f"平均SL損失: {avg_sl_loss:>10,.0f}  "
            f"実効RR: {effective_rr:.2f}"
        )

        return lines

    def _build_htf_analysis(self) -> list[str]:
        """HTFフィルター効果分析セクション"""
        lines = ["6. HTFフィルター効果分析"]
        lines.append("-" * 64)

        # コンセンサス通過後のレコード
        consensus_passed = [
            r for r in self._records
            if r.risk_passed and r.consensus_passed
        ]
        if not consensus_passed:
            lines.append("  コンセンサス通過レコードなし")
            return lines

        htf_passed = sum(1 for r in consensus_passed if r.htf_passed)
        htf_failed = len(consensus_passed) - htf_passed
        pass_rate = htf_passed / len(consensus_passed) * 100

        lines.append(
            f"  コンセンサス通過後: {len(consensus_passed)}件"
        )
        lines.append(f"  HTF通過:  {htf_passed}件 ({pass_rate:.1f}%)")
        lines.append(
            f"  HTF除外:  {htf_failed}件 "
            f"({htf_failed / len(consensus_passed) * 100:.1f}%)"
        )
        lines.append(
            "  ※ HTF除外シグナルの仮想勝率は"
            "別途シミュレーション要"
        )

        return lines

    def _build_suggestions(self) -> list[str]:
        """改善示唆セクション"""
        lines = ["7. 改善示唆"]
        lines.append("-" * 64)

        risk_passed = [r for r in self._records if r.risk_passed]
        total = len(self._records)
        if not total:
            return lines

        # コンセンサス不足が最大ボトルネックかチェック
        consensus_fail = sum(
            1 for r in risk_passed
            if not r.consensus_passed and r.hold_reason
            and "スコア不足" in r.hold_reason
        )
        consensus_fail_pct = consensus_fail / total * 100

        if consensus_fail_pct > 90:
            lines.append(
                f"  [!] コンセンサス不足が{consensus_fail_pct:.1f}%。"
                "閾値の引き下げを検討:"
            )
            # スコア帯別の追加シグナル数を計算
            bins = [
                (3.0, 3.5), (3.5, 4.0), (4.0, 4.5), (4.5, 5.0),
            ]
            for lo, hi in bins:
                count = sum(
                    1 for r in risk_passed
                    if lo <= r.consensus_score < hi
                )
                if count > 0:
                    lines.append(
                        f"    閾値を{lo:.1f}に下げた場合: "
                        f"+{count}件/年 追加"
                    )

        # HTFフィルターの除外率
        consensus_passed_recs = [
            r for r in self._records
            if r.risk_passed and r.consensus_passed
        ]
        if consensus_passed_recs:
            htf_fail = sum(
                1 for r in consensus_passed_recs if not r.htf_passed
            )
            htf_fail_pct = (
                htf_fail / len(consensus_passed_recs) * 100
            )
            if htf_fail_pct > 30:
                lines.append(
                    f"  [!] HTFフィルター除外率が{htf_fail_pct:.1f}%。"
                    "HTFフィルターの緩和を検討。"
                )

        # モード別シグナル発生率の偏り
        mode_counts: dict[str, int] = defaultdict(int)
        mode_signals: dict[str, int] = defaultdict(int)
        for r in self._records:
            if r.mode:
                mode_counts[r.mode] += 1
                if r.final_direction not in ("HOLD", ""):
                    mode_signals[r.mode] += 1

        for mode in ["UNIVERSAL"]:
            mc = mode_counts.get(mode, 0)
            ms = mode_signals.get(mode, 0)
            if mc > 0 and ms == 0:
                lines.append(
                    f"  [!] {mode}モードでシグナル発生ゼロ。"
                    "閾値が厳しすぎる可能性。"
                )

        if len(lines) == 2:
            lines.append("  特になし")

        return lines
