"""CLI入出力アダプター

コマンドライン引数から ExecutorConfig への変換と、
実行結果のコンソール出力を担当。
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from autotrader.backtest.executor import ExecutorConfig, ExecutorResult

if TYPE_CHECKING:
    pass


class CLIAdapter:
    """CLI入出力アダプター

    argparseの引数をExecutorConfigに変換し、
    結果をコンソールに出力する。
    """

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> ExecutorConfig:
        """argparse引数からExecutorConfigを生成

        Args:
            args: パース済み引数

        Returns:
            ExecutorConfig: 実行設定
        """
        start_year, end_year = cls._parse_years(args.years)

        # 短い時間足オプション
        use_short_tf = not getattr(args, "no_short_tf", False)

        # 並列処理オプション（デフォルト有効）
        parallel_years = not getattr(args, "no_parallel", False)
        max_workers = getattr(args, "workers", None)

        # ファンダメンタルオプション
        fundamental_enabled = getattr(args, "fundamental", False)
        fundamental_csv = getattr(args, "fundamental_csv", None)
        fundamental_guard = getattr(args, "fundamental_guard", 30)


        return ExecutorConfig(
            start_year=start_year,
            end_year=end_year,
            initial_balance=args.initial_balance,
            volume=args.volume,
            symbol=args.symbol,
            data_dir=args.data_dir,
            use_short_timeframe=use_short_tf,
            parallel_years=parallel_years,
            max_workers=max_workers,
            max_positions=args.max_positions,
            verbose=args.verbose,
            fundamental_csv=(
                fundamental_csv if fundamental_enabled else None
            ),
            fundamental_guard_minutes=fundamental_guard,
        )

    @staticmethod
    def _parse_years(years_str: str) -> tuple[int, int]:
        """年範囲をパース

        Args:
            years_str: 年範囲文字列（例: "2020-2024"）

        Returns:
            tuple[int, int]: (開始年, 終了年)
        """
        if "-" in years_str:
            parts = years_str.split("-")
            return int(parts[0]), int(parts[1])
        year = int(years_str)
        return year, year

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """共通引数をパーサーに追加

        Args:
            parser: 引数パーサー
        """
        # 期間設定
        parser.add_argument(
            "--years",
            type=str,
            default="2020-2024",
            help="バックテスト期間（例: 2020-2024）",
        )

        # シンボル・時間足設定
        parser.add_argument(
            "--symbol",
            default="USDJPY",
            help="シンボル",
        )

        # 資金設定
        parser.add_argument(
            "--initial-balance",
            type=float,
            default=1_000_000.0,
            help="初期残高",
        )
        parser.add_argument(
            "--volume",
            type=float,
            default=1.0,
            help="取引ボリューム",
        )
        parser.add_argument(
            "--max-positions",
            type=int,
            default=1,
            help="最大ポジション数",
        )

        # 実行モード
        parser.add_argument(
            "--use-short-tf",
            action="store_true",
            default=True,
            help="短い時間足（M5）を基準に使用",
        )
        parser.add_argument(
            "--no-short-tf",
            action="store_true",
            help="短い時間足を使用しない（M15基準）",
        )
        parser.add_argument(
            "--universal",
            action="store_true",
            help="UNIVERSALモードを有効化（M1〜D1全TFを動的評価）",
        )
        parser.add_argument(
            "--parallel",
            action="store_true",
            default=True,
            help="年並列処理を有効化（デフォルト: True）",
        )
        parser.add_argument(
            "--no-parallel",
            action="store_true",
            help="年並列処理を無効化",
        )
        parser.add_argument(
            "--workers",
            type=int,
            default=None,
            help="並列ワーカー数（デフォルト: CPUコア数）",
        )

        # ファンダメンタル統合
        parser.add_argument(
            "--fundamental",
            action="store_true",
            help="ファンダメンタルフィルターを有効化（CSVが必要）",
        )
        parser.add_argument(
            "--fundamental-csv",
            type=str,
            default=None,
            help="経済イベントCSVファイルパス",
        )
        parser.add_argument(
            "--fundamental-guard",
            type=int,
            default=30,
            help="重要指標前の取引停止分数（デフォルト: 30）",
        )

        # その他
        parser.add_argument(
            "--data-dir",
            type=str,
            default="data/csv",
            help="データディレクトリ",
        )
        parser.add_argument(
            "-v", "--verbose",
            action="store_true",
            help="詳細出力",
        )

    @classmethod
    def print_header(cls, config: ExecutorConfig) -> None:
        """ヘッダーを表示

        Args:
            config: 実行設定
        """
        base_tf = "M5（5分毎判断）" if config.use_short_timeframe else "M15（15分毎判断）"
        parallel_status = "有効" if config.parallel_years else "無効"
        print("=" * 80)
        print("AutoTraderV4 バックテスト")
        print("=" * 80)
        print(f"シンボル: {config.symbol}")
        print(f"トレード判断頻度: {base_tf}")
        print(f"評価時間足: M5, M15, H1, H4, D1（マルチタイムフレーム）")
        print(f"期間: {config.start_year}-{config.end_year}")
        print(f"初期残高: ¥{config.initial_balance:,.0f}")
        print(f"ボリューム: {config.volume}")
        print(f"年並列処理: {parallel_status}")
        print("=" * 80)

    @classmethod
    def print_results(cls, result: ExecutorResult) -> None:
        """結果を表示

        Args:
            result: 実行結果
        """
        print(f"\n{'-' * 80}")
        print("バックテスト結果")
        print(f"{'-' * 80}")

        if result.cancelled:
            print("実行がキャンセルされました")
            return

        print(f"総取引数: {result.trades}")
        print(f"勝率: {result.win_rate:.1f}%")
        print(f"プロフィットファクター: {result.profit_factor:.2f}")
        print(f"純利益: ¥{result.net_profit:+,.0f}")
        print(f"最大ドローダウン: {result.max_drawdown:.2f}%")
        print(f"シャープレシオ: {result.sharpe_ratio:.2f}")
        print(f"年間平均収益率: {result.annual_return:.1f}%")
        print(f"実行時間: {result.execution_time:.1f}秒")

    @classmethod
    def print_yearly_results(cls, yearly_results: list[dict]) -> None:
        """年別結果を表示

        Args:
            yearly_results: 年別結果リスト
        """
        if not yearly_results:
            return

        print(f"\n{'=' * 80}")
        print("年別詳細")
        print(f"{'=' * 80}")
        print(
            f"{'年':<6} {'取引数':>8} {'勝率':>8} {'PF':>8} "
            f"{'利益':>14} {'DD':>8}"
        )
        print("-" * 80)

        for r in yearly_results:
            print(
                f"{r['year']:<6} "
                f"{r.get('trades', 0):>8} "
                f"{r.get('win_rate', 0):>7.1f}% "
                f"{r.get('profit_factor', 0):>7.2f} "
                f"¥{r.get('net_profit', 0):>+12,.0f} "
                f"{r.get('max_drawdown', 0):>7.2f}%"
            )

        print("-" * 80)
        profitable = sum(1 for r in yearly_results if r.get("net_profit", 0) > 0)
        print(f"黒字年: {profitable}/{len(yearly_results)}")

    @classmethod
    def print_monthly_summary(
        cls,
        monthly_results: list[dict],
        verbose: bool = False,
    ) -> None:
        """月別サマリーを表示

        Args:
            monthly_results: 月別結果リスト
            verbose: 詳細表示
        """
        if not monthly_results:
            return

        print(f"\n{'=' * 80}")
        print("月別サマリー")
        print(f"{'=' * 80}")

        # 月間+5%達成率を計算
        total_months = len(monthly_results)
        target_months = sum(
            1 for r in monthly_results if r.get("return_pct", 0) >= 5.0
        )
        avg_return = (
            sum(r.get("return_pct", 0) for r in monthly_results) / total_months
        )
        positive_months = sum(
            1 for r in monthly_results if r.get("return_pct", 0) > 0
        )

        print(f"月間プラス率: {positive_months}/{total_months} "
              f"({100*positive_months/total_months:.1f}%)")
        print(f"月間+5%達成: {target_months}/{total_months} "
              f"({100*target_months/total_months:.1f}%)")
        print(f"月間平均収益率: {avg_return:.2f}%")

        # 直近12ヶ月
        if len(monthly_results) >= 12:
            recent = monthly_results[-12:]
            recent_target = sum(
                1 for r in recent if r.get("return_pct", 0) >= 5.0
            )
            recent_avg = sum(r.get("return_pct", 0) for r in recent) / 12
            print(f"\n直近12ヶ月:")
            print(f"  +5%達成: {recent_target}/12")
            print(f"  平均収益率: {recent_avg:.2f}%")

    @classmethod
    def print_mode_results(cls, mode_results: dict[str, dict]) -> None:
        """モード別結果を表示

        Args:
            mode_results: モード別結果
        """
        if not mode_results:
            return

        print(f"\n{'=' * 80}")
        print("トレードモード別結果")
        print(f"{'=' * 80}")
        print(
            f"{'モード':<12} {'取引数':>8} {'勝率':>8} {'PF':>8} "
            f"{'利益':>14}"
        )
        print("-" * 60)

        for mode, stats in mode_results.items():
            print(
                f"{mode:<12} "
                f"{stats.get('trades', 0):>8} "
                f"{stats.get('win_rate', 0):>7.1f}% "
                f"{stats.get('profit_factor', 0):>7.2f} "
                f"¥{stats.get('net_profit', 0):>+12,.0f}"
            )

    @classmethod
    def print_full_report(
        cls,
        config: ExecutorConfig,
        result: ExecutorResult,
    ) -> None:
        """完全レポートを表示

        Args:
            config: 実行設定
            result: 実行結果
        """
        cls.print_header(config)
        cls.print_results(result)
        cls.print_yearly_results(result.yearly_results)
        cls.print_monthly_summary(
            result.monthly_results, config.verbose
        )
        if result.mode_results:
            cls.print_mode_results(result.mode_results)

        print("\n" + "=" * 80)
        print("完了")
        print("=" * 80)
