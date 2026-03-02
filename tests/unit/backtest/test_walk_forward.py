"""ウォークフォワード検証のユニットテスト"""

from __future__ import annotations

import pytest

from autotrader.backtest.walk_forward import (
    WalkForwardWindow,
    RollingWFResult,
    RollingWFReport,
    RollingWalkForwardValidator,
    ParameterStabilityTest,
    StabilityResult,
    StabilityReport,
    create_walk_forward_periods,
)


# ============================================================
# WalkForwardWindow
# ============================================================


class TestWalkForwardWindow:
    """WalkForwardWindow のテスト"""

    def test_label(self) -> None:
        """ラベル生成"""
        w = WalkForwardWindow(
            is_start_year=2020,
            is_end_year=2022,
            oos_year=2023,
        )
        assert w.label == "IS:2020-2022_OOS:2023"

    def test_frozen(self) -> None:
        """frozen dataclass"""
        w = WalkForwardWindow(2020, 2022, 2023)
        with pytest.raises(AttributeError):
            w.is_start_year = 2019  # type: ignore[misc]


# ============================================================
# RollingWFResult
# ============================================================


class TestRollingWFResult:
    """RollingWFResult のテスト"""

    def test_oos_profit(self) -> None:
        """OOS利益取得"""
        r = RollingWFResult(
            window=WalkForwardWindow(2020, 2022, 2023),
            is_metrics={"total_profit": 1000.0},
            oos_metrics={"total_profit": 500.0},
        )
        assert r.oos_profit == 500.0

    def test_oos_profit_missing_key(self) -> None:
        """total_profitキーなし時のデフォルト"""
        r = RollingWFResult(
            window=WalkForwardWindow(2020, 2022, 2023),
            is_metrics={},
            oos_metrics={},
        )
        assert r.oos_profit == 0.0

    def test_oos_sharpe(self) -> None:
        """OOSシャープレシオ取得"""
        r = RollingWFResult(
            window=WalkForwardWindow(2020, 2022, 2023),
            is_metrics={},
            oos_metrics={"sharpe_ratio": 2.5},
        )
        assert r.oos_sharpe == 2.5

    def test_degradation_pct_normal(self) -> None:
        """劣化率: 正常ケース"""
        r = RollingWFResult(
            window=WalkForwardWindow(2020, 2022, 2023),
            is_metrics={"total_profit": 1000.0},
            oos_metrics={"total_profit": 700.0},
        )
        # (1000 - 700) / 1000 * 100 = 30.0
        assert r.degradation_pct == pytest.approx(30.0)

    def test_degradation_pct_is_negative(self) -> None:
        """劣化率: IS利益が負の場合は0"""
        r = RollingWFResult(
            window=WalkForwardWindow(2020, 2022, 2023),
            is_metrics={"total_profit": -100.0},
            oos_metrics={"total_profit": 50.0},
        )
        assert r.degradation_pct == 0.0

    def test_degradation_pct_is_zero(self) -> None:
        """劣化率: IS利益が0の場合は0"""
        r = RollingWFResult(
            window=WalkForwardWindow(2020, 2022, 2023),
            is_metrics={"total_profit": 0.0},
            oos_metrics={"total_profit": 50.0},
        )
        assert r.degradation_pct == 0.0


# ============================================================
# RollingWFReport
# ============================================================


class TestRollingWFReport:
    """RollingWFReport のテスト"""

    @pytest.fixture()
    def sample_report(self) -> RollingWFReport:
        """3ウィンドウのサンプルレポート"""
        return RollingWFReport(
            results=[
                RollingWFResult(
                    window=WalkForwardWindow(
                        2020, 2022, 2023
                    ),
                    is_metrics={"total_profit": 3000.0},
                    oos_metrics={"total_profit": 800.0},
                ),
                RollingWFResult(
                    window=WalkForwardWindow(
                        2021, 2023, 2024
                    ),
                    is_metrics={"total_profit": 2500.0},
                    oos_metrics={"total_profit": 600.0},
                ),
                RollingWFResult(
                    window=WalkForwardWindow(
                        2022, 2024, 2025
                    ),
                    is_metrics={"total_profit": 2800.0},
                    oos_metrics={"total_profit": 700.0},
                ),
            ]
        )

    def test_avg_oos_profit(
        self, sample_report: RollingWFReport
    ) -> None:
        """OOS利益の平均"""
        # (800 + 600 + 700) / 3 = 700.0
        assert sample_report.avg_oos_profit == pytest.approx(
            700.0
        )

    def test_avg_degradation(
        self, sample_report: RollingWFReport
    ) -> None:
        """平均劣化率"""
        # window1: (3000-800)/3000*100 = 73.33
        # window2: (2500-600)/2500*100 = 76.0
        # window3: (2800-700)/2800*100 = 75.0
        # avg = (73.33 + 76.0 + 75.0) / 3 = 74.78
        assert sample_report.avg_degradation == pytest.approx(
            74.78, abs=0.01
        )

    def test_all_oos_profitable_true(
        self, sample_report: RollingWFReport
    ) -> None:
        """全OOS黒字のケース"""
        assert sample_report.all_oos_profitable is True

    def test_all_oos_profitable_false(self) -> None:
        """OOS赤字ありのケース"""
        report = RollingWFReport(
            results=[
                RollingWFResult(
                    window=WalkForwardWindow(
                        2020, 2022, 2023
                    ),
                    is_metrics={"total_profit": 1000.0},
                    oos_metrics={"total_profit": -100.0},
                ),
            ]
        )
        assert report.all_oos_profitable is False

    def test_empty_report(self) -> None:
        """空レポート"""
        report = RollingWFReport()
        assert report.avg_oos_profit == 0.0
        assert report.avg_degradation == 0.0
        assert report.all_oos_profitable is True

    def test_summary_format(
        self, sample_report: RollingWFReport
    ) -> None:
        """サマリー出力フォーマット"""
        text = sample_report.summary()
        assert "Walk-Forward Validation Report" in text
        assert "IS:2020-2022_OOS:2023" in text
        assert "Avg OOS Profit:" in text
        assert "All OOS Profitable: True" in text


# ============================================================
# RollingWalkForwardValidator - ウィンドウ生成
# ============================================================


class TestRollingWalkForwardValidator:
    """RollingWalkForwardValidator のテスト"""

    def test_default_3is_1oos_generates_3_windows(
        self,
    ) -> None:
        """3年IS+1年OOS, 2020-2025 -> 3ウィンドウ"""
        v = RollingWalkForwardValidator(
            symbol="USDJPY",
            is_years=3,
            oos_years=1,
            start_year=2020,
            end_year=2025,
        )
        windows = v.generate_windows()
        assert len(windows) == 3
        # ウィンドウ1: IS 2020-2022, OOS 2023
        assert windows[0].is_start_year == 2020
        assert windows[0].is_end_year == 2022
        assert windows[0].oos_year == 2023
        # ウィンドウ2: IS 2021-2023, OOS 2024
        assert windows[1].is_start_year == 2021
        assert windows[1].is_end_year == 2023
        assert windows[1].oos_year == 2024
        # ウィンドウ3: IS 2022-2024, OOS 2025
        assert windows[2].is_start_year == 2022
        assert windows[2].is_end_year == 2024
        assert windows[2].oos_year == 2025

    def test_2is_1oos_generates_4_windows(self) -> None:
        """2年IS+1年OOS, 2020-2025 -> 4ウィンドウ"""
        v = RollingWalkForwardValidator(
            symbol="EURJPY",
            is_years=2,
            oos_years=1,
            start_year=2020,
            end_year=2025,
        )
        windows = v.generate_windows()
        assert len(windows) == 4
        assert windows[0].is_start_year == 2020
        assert windows[0].oos_year == 2022
        assert windows[3].is_start_year == 2023
        assert windows[3].oos_year == 2025

    def test_4is_1oos_generates_2_windows(self) -> None:
        """4年IS+1年OOS, 2020-2025 -> 2ウィンドウ"""
        v = RollingWalkForwardValidator(
            symbol="USDJPY",
            is_years=4,
            oos_years=1,
            start_year=2020,
            end_year=2025,
        )
        windows = v.generate_windows()
        assert len(windows) == 2
        assert windows[0].oos_year == 2024
        assert windows[1].oos_year == 2025

    def test_insufficient_data_returns_empty(self) -> None:
        """データ不足時は空リスト"""
        v = RollingWalkForwardValidator(
            symbol="USDJPY",
            is_years=5,
            oos_years=1,
            start_year=2020,
            end_year=2024,
        )
        windows = v.generate_windows()
        assert len(windows) == 0

    def test_run_calls_backtest_fn(self) -> None:
        """runがbacktest_fnを正しく呼び出す"""
        # 呼び出し記録
        calls: list[tuple[str, int, int]] = []

        def mock_backtest(
            symbol: str,
            start_year: int,
            end_year: int,
        ) -> dict[str, float]:
            calls.append((symbol, start_year, end_year))
            return {
                "total_profit": 500.0,
                "win_rate": 0.6,
                "profit_factor": 1.5,
                "sharpe_ratio": 2.0,
            }

        v = RollingWalkForwardValidator(
            symbol="USDJPY",
            is_years=3,
            oos_years=1,
            start_year=2020,
            end_year=2025,
        )
        report = v.run(mock_backtest)

        # 3ウィンドウ * (IS + OOS) = 6回呼び出し
        assert len(calls) == 6
        assert len(report.results) == 3

        # IS呼び出しの確認
        assert calls[0] == ("USDJPY", 2020, 2022)
        # OOS呼び出しの確認
        assert calls[1] == ("USDJPY", 2023, 2023)

    def test_run_report_metrics(self) -> None:
        """runの結果メトリクスが正しい"""

        def mock_backtest(
            symbol: str,
            start_year: int,
            end_year: int,
        ) -> dict[str, float]:
            # IS期間は年数分だけ利益が出る
            years = end_year - start_year + 1
            return {"total_profit": 300.0 * years}

        v = RollingWalkForwardValidator(
            symbol="USDJPY",
            is_years=3,
            oos_years=1,
            start_year=2020,
            end_year=2025,
        )
        report = v.run(mock_backtest)

        # 各ウィンドウ: IS=900(3年), OOS=300(1年)
        assert report.results[0].oos_profit == 300.0
        assert report.avg_oos_profit == 300.0
        # 劣化率: (900-300)/900*100 = 66.67%
        assert report.results[0].degradation_pct == (
            pytest.approx(66.67, abs=0.01)
        )
        assert report.all_oos_profitable is True


# ============================================================
# create_walk_forward_periods（既存関数の互換テスト）
# ============================================================


class TestCreateWalkForwardPeriods:
    """create_walk_forward_periods のテスト"""

    def test_default_3is_1oos(self) -> None:
        """3年IS+1年OOS"""
        periods = create_walk_forward_periods(
            start_year=2020, end_year=2025
        )
        assert len(periods) == 3
        assert periods[0] == ((2020, 2023), (2023, 2024))
        assert periods[1] == ((2021, 2024), (2024, 2025))
        assert periods[2] == ((2022, 2025), (2025, 2026))


# ============================================================
# ParameterStabilityTest
# ============================================================


class TestParameterStabilityTest:
    """ParameterStabilityTest のテスト"""

    def test_default_variations(self) -> None:
        """デフォルトバリエーション"""
        t = ParameterStabilityTest(
            base_config={"threshold": 10.0},
            param_name="threshold",
        )
        assert t.variations == [
            0.90, 0.95, 1.0, 1.05, 1.10
        ]

    def test_custom_variations(self) -> None:
        """カスタムバリエーション"""
        t = ParameterStabilityTest(
            base_config={"threshold": 10.0},
            param_name="threshold",
            variations=[0.8, 1.0, 1.2],
        )
        assert t.variations == [0.8, 1.0, 1.2]

    def test_run_calls_backtest_fn(self) -> None:
        """runがbacktest_fnを正しく呼び出す"""
        configs_seen: list[dict] = []

        def mock_backtest(
            config: dict,
        ) -> dict[str, float]:
            configs_seen.append(config)
            return {
                "total_profit": config["threshold"] * 100
            }

        t = ParameterStabilityTest(
            base_config={"threshold": 10.0},
            param_name="threshold",
            variations=[0.9, 1.0, 1.1],
        )
        report = t.run(mock_backtest)

        assert len(configs_seen) == 3
        assert len(report.results) == 3
        # x0.9 -> threshold=9.0, profit=900
        assert report.results[0].actual_value == (
            pytest.approx(9.0)
        )
        assert report.results[0].metrics[
            "total_profit"
        ] == pytest.approx(900.0)
        # x1.0 -> threshold=10.0, profit=1000
        assert report.results[1].actual_value == (
            pytest.approx(10.0)
        )
        # x1.1 -> threshold=11.0, profit=1100
        assert report.results[2].actual_value == (
            pytest.approx(11.0)
        )

    def test_run_preserves_other_config(self) -> None:
        """他のconfigパラメータが保持される"""
        seen: list[dict] = []

        def mock_backtest(
            config: dict,
        ) -> dict[str, float]:
            seen.append(config)
            return {"total_profit": 100.0}

        t = ParameterStabilityTest(
            base_config={
                "threshold": 10.0,
                "other_param": "keep_me",
            },
            param_name="threshold",
            variations=[1.0],
        )
        t.run(mock_backtest)

        assert seen[0]["other_param"] == "keep_me"
        assert seen[0]["threshold"] == pytest.approx(10.0)


# ============================================================
# StabilityReport
# ============================================================


class TestStabilityReport:
    """StabilityReport のテスト"""

    @pytest.fixture()
    def sample_report(self) -> StabilityReport:
        """サンプルレポート"""
        return StabilityReport(
            param_name="threshold",
            base_value=10.0,
            results=[
                StabilityResult(
                    multiplier=0.9,
                    actual_value=9.0,
                    metrics={"total_profit": 800.0},
                ),
                StabilityResult(
                    multiplier=1.0,
                    actual_value=10.0,
                    metrics={"total_profit": 1000.0},
                ),
                StabilityResult(
                    multiplier=1.1,
                    actual_value=11.0,
                    metrics={"total_profit": 900.0},
                ),
            ],
        )

    def test_profit_range(
        self, sample_report: StabilityReport
    ) -> None:
        """利益レンジ"""
        # 1000 - 800 = 200
        assert sample_report.profit_range == pytest.approx(
            200.0
        )

    def test_is_stable_all_positive(
        self, sample_report: StabilityReport
    ) -> None:
        """全黒字なら安定"""
        assert sample_report.is_stable is True

    def test_is_stable_with_loss(self) -> None:
        """赤字ありなら不安定"""
        report = StabilityReport(
            param_name="x",
            base_value=1.0,
            results=[
                StabilityResult(
                    multiplier=1.0,
                    actual_value=1.0,
                    metrics={"total_profit": -50.0},
                ),
            ],
        )
        assert report.is_stable is False

    def test_empty_report(self) -> None:
        """空レポート"""
        report = StabilityReport(
            param_name="x", base_value=1.0
        )
        assert report.profit_range == 0.0
        assert report.is_stable is True

    def test_summary_format(
        self, sample_report: StabilityReport
    ) -> None:
        """サマリーフォーマット"""
        text = sample_report.summary()
        assert "Parameter Stability: threshold" in text
        assert "base=10.0" in text
        assert "Profit Range:" in text
        assert "All Profitable: True" in text
