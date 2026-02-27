"""決定論的イベント分析モジュール

LLMを一切使わず、ヒューリスティックルールのみで
全フィールドを計算するイベント分析クラス。

LLMEventGenerator を継承し、_analyze_event を
オーバーライドして HIGH/MEDIUM でもコード計算のみで処理。
生成速度: 数千件/秒（LLM版の数百倍以上）。
"""

from __future__ import annotations

from autotrader.adapters.fundamental.llm_event_generator import (
    EVENT_CSV_COLUMNS,
    LLMEventGenerator,
    _HOLIDAY_RE,
    _IMPACT_LABELS,
    _IMPACT_SCALE,
)
from autotrader.adapters.fundamental.schemas import (
    EconomicEvent,
    ImpactLevel,
)
from autotrader.config.llm_settings import OllamaSettings

# ── 指標カテゴリ別 収束時間ベース値 ──────────────────
# (キーワードリスト, base_hours, カテゴリ名)
_INDICATOR_CATEGORIES: list[
    tuple[list[str], float, str]
] = [
    # 金融政策: 最も長い持続効果
    (
        [
            "interest rate",
            "rate decision",
            "federal funds",
            "cash rate",
            "bank rate",
            "refinancing rate",
            "overnight rate",
            "policy rate",
            "fomc",
            "boj",
            "boe",
            "ecb",
            "rba",
            "rbnz",
            "snb",
            "monetary policy",
        ],
        48.0,
        "金融政策",
    ),
    # 雇用: 市場注目度が高い
    (
        [
            "non-farm",
            "nonfarm",
            "nfp",
            "employment change",
            "unemployment",
            "jobless",
            "claimant",
            "payroll",
            "adp",
            "jolts",
            "wages",
            "earnings",
            "average hourly",
        ],
        36.0,
        "雇用",
    ),
    # GDP: マクロ基盤
    (
        ["gdp"],
        24.0,
        "GDP",
    ),
    # 物価: インフレ指標
    (
        [
            "cpi",
            "consumer price",
            "ppi",
            "producer price",
            "pce",
            "inflation",
        ],
        20.0,
        "物価",
    ),
    # 貿易収支
    (
        [
            "trade balance",
            "current account",
            "import",
            "export",
        ],
        16.0,
        "貿易収支",
    ),
    # 消費
    (
        ["retail sales", "consumer spending", "consumer confidence"],
        12.0,
        "消費",
    ),
    # 製造業
    (
        [
            "pmi",
            "manufacturing",
            "ism",
            "industrial production",
            "factory",
            "durable goods",
        ],
        10.0,
        "製造業",
    ),
    # 住宅
    (
        [
            "housing",
            "home sales",
            "building permits",
            "construction",
        ],
        8.0,
        "住宅",
    ),
]

# ── 通貨ペア名マッピング（日本語） ──────────────────
_CCY_NAMES: dict[str, str] = {
    "USD": "ドル",
    "EUR": "ユーロ",
    "GBP": "ポンド",
    "JPY": "円",
    "CHF": "スイスフラン",
    "CAD": "カナダドル",
    "AUD": "豪ドル",
    "NZD": "NZドル",
}

# ── 主要指標の日本語名 ──────────────────────────
_EVENT_JP: dict[str, str] = {
    "Non-Farm Employment Change": "非農業部門雇用者数",
    "Unemployment Rate": "失業率",
    "CPI m/m": "消費者物価指数(前月比)",
    "CPI y/y": "消費者物価指数(前年比)",
    "Core CPI m/m": "コアCPI(前月比)",
    "Core CPI y/y": "コアCPI(前年比)",
    "GDP q/q": "GDP(前期比)",
    "Retail Sales m/m": "小売売上高(前月比)",
    "Core Retail Sales m/m": "コア小売売上高",
    "ISM Manufacturing PMI": "ISM製造業PMI",
    "ISM Services PMI": "ISMサービス業PMI",
    "Federal Funds Rate": "FF金利",
    "FOMC Statement": "FOMC声明",
    "Trade Balance": "貿易収支",
    "PPI m/m": "生産者物価指数",
    "PCE Price Index m/m": "PCE物価指数",
    "Core PCE Price Index m/m": "コアPCE物価指数",
    "ADP Non-Farm Employment Change": "ADP雇用統計",
    "JOLTS Job Openings": "JOLTS求人件数",
    "Consumer Confidence": "消費者信頼感指数",
    "Existing Home Sales": "中古住宅販売件数",
    "New Home Sales": "新築住宅販売件数",
    "Building Permits": "建設許可件数",
    "Industrial Production m/m": "鉱工業生産",
    "Durable Goods Orders m/m": "耐久財受注",
    "Manufacturing PMI": "製造業PMI",
    "Services PMI": "サービス業PMI",
    "Official Bank Rate": "BOE政策金利",
    "Main Refinancing Rate": "ECB主要リファイナンス金利",
    "Monetary Policy Statement": "金融政策声明",
    "BOJ Policy Rate": "日銀政策金利",
    "Cash Rate": "RBA政策金利",
    "Official Cash Rate": "RBNZ政策金利",
    "Overnight Rate": "BOC政策金利",
    "SNB Policy Rate": "SNB政策金利",
    "Bank Holiday": "市場休日",
}


class DeterministicEventAnalyzer(LLMEventGenerator):
    """決定論的イベント分析クラス

    LLMを使わず、全フィールドをヒューリスティックで計算。
    LLMEventGenerator の surprise_score / direction_bias 計算は
    そのまま継承し、convergence_hours / expected_volatility /
    trade_caution_level / summary をルールベースで生成する。

    数千件/秒の処理が可能で、バッチ生成に最適。

    Args:
        ollama_settings: 未使用（互換性のため保持）
        retry_delay_seconds: 未使用
        max_retries: 未使用
    """

    def __init__(
        self,
        ollama_settings: OllamaSettings | None = None,
        retry_delay_seconds: float = 2.0,
        max_retries: int = 3,
    ) -> None:
        """初期化（LLM設定は無視される）"""
        super().__init__(
            ollama_settings=ollama_settings,
            retry_delay_seconds=retry_delay_seconds,
            max_retries=max_retries,
        )

    # --------------------------------------------------
    # 指標カテゴリ判定
    # --------------------------------------------------

    @staticmethod
    def _get_indicator_category(
        event_name: str,
    ) -> tuple[float, str]:
        """指標名からカテゴリ別の収束ベース時間を判定

        Args:
            event_name: 指標名

        Returns:
            tuple[float, str]: (base_hours, category_name)
        """
        name_lower = event_name.lower()
        for keywords, base_hours, category in _INDICATOR_CATEGORIES:
            for kw in keywords:
                if kw in name_lower:
                    return base_hours, category
        # 不明カテゴリ: デフォルト6時間
        return 6.0, "その他"

    # --------------------------------------------------
    # ヒューリスティック計算
    # --------------------------------------------------

    @staticmethod
    def _compute_convergence_hours(
        event: EconomicEvent,
        surprise_score: float,
    ) -> float:
        """収束時間をヒューリスティックで計算

        カテゴリ別ベース時間 × インパクト係数 ×
        サプライズ強度調整で推定。

        Args:
            event: 対象イベント
            surprise_score: 計算済みサプライズ

        Returns:
            float: 収束推定時間（0.5〜72.0）
        """
        base_hours, _ = (
            DeterministicEventAnalyzer._get_indicator_category(
                event.event_name
            )
        )

        # インパクト係数
        impact_mul = {
            ImpactLevel.HIGH: 1.0,
            ImpactLevel.MEDIUM: 0.6,
            ImpactLevel.LOW: 0.2,
        }.get(event.impact, 0.2)

        # サプライズ強度による追加（大サプライズほど長引く）
        surprise_extra = abs(surprise_score) * base_hours * 0.5

        hours = base_hours * impact_mul + surprise_extra
        return max(0.5, min(72.0, round(hours, 1)))

    @staticmethod
    def _compute_expected_volatility(
        event: EconomicEvent,
        surprise_score: float,
    ) -> float:
        """期待ボラティリティをヒューリスティックで計算

        通常比の倍率を返す。
        HIGH指標: 1.2〜1.5倍, MEDIUM: 1.0〜1.2倍,
        LOW: 0.5倍固定。

        Args:
            event: 対象イベント
            surprise_score: 計算済みサプライズ

        Returns:
            float: ボラティリティ倍率（0.0〜2.0）
        """
        if event.impact == ImpactLevel.HIGH:
            base = 1.2
            extra = abs(surprise_score) * 0.3
        elif event.impact == ImpactLevel.MEDIUM:
            base = 1.0
            extra = abs(surprise_score) * 0.2
        else:
            return 0.5

        return min(2.0, round(base + extra, 2))

    @staticmethod
    def _compute_trade_caution_level(
        event: EconomicEvent,
    ) -> int:
        """取引注意度をヒューリスティックで計算

        HIGH: 1（注意）, MEDIUM: 1（注意）, LOW: 0（通常）。
        休日はLLMEventGenerator側で別処理される。

        Args:
            event: 対象イベント

        Returns:
            int: 0=通常, 1=注意, 2=回避推奨
        """
        if event.impact in (
            ImpactLevel.HIGH,
            ImpactLevel.MEDIUM,
        ):
            return 1
        return 0

    @staticmethod
    def _generate_summary(
        symbol: str,
        event: EconomicEvent,
        surprise_score: float,
        direction_bias: float,
    ) -> str:
        """日本語サマリーをテンプレートで生成

        Args:
            symbol: 対象シンボル
            event: 対象イベント
            surprise_score: 計算済みサプライズ
            direction_bias: 計算済み方向バイアス

        Returns:
            str: 200文字以内の日本語サマリー
        """
        ccy_name = _CCY_NAMES.get(event.currency, event.currency)
        evt_jp = _EVENT_JP.get(event.event_name, event.event_name)
        _, category = (
            DeterministicEventAnalyzer._get_indicator_category(
                event.event_name
            )
        )

        if event.impact == ImpactLevel.LOW:
            return f"低インパクト指標({evt_jp})"

        # サプライズ方向テキスト
        if (
            event.actual is not None
            and event.forecast is not None
        ):
            if surprise_score > 0.05:
                surp_text = "予想を上回り"
            elif surprise_score < -0.05:
                surp_text = "予想を下回り"
            else:
                surp_text = "概ね予想通りで"
        else:
            surp_text = "結果発表後"

        # ペア方向テキスト
        if direction_bias > 0.1:
            dir_text = f"{symbol}は上昇圧力"
        elif direction_bias < -0.1:
            dir_text = f"{symbol}は下落圧力"
        else:
            dir_text = f"{symbol}への影響は限定的"

        # 高インパクト時の注意喚起
        vol_note = (
            "ボラティリティ上昇に注意。"
            if event.impact == ImpactLevel.HIGH
            else ""
        )

        return (
            f"{ccy_name}{evt_jp}は{surp_text}、"
            f"{dir_text}。{vol_note}"
        )[:200]

    # --------------------------------------------------
    # _analyze_event オーバーライド（LLM不使用）
    # --------------------------------------------------

    def _analyze_event(
        self,
        symbol: str,
        base: str,
        quote: str,
        event: EconomicEvent,
    ) -> dict:
        """単一イベントを決定論的に分析（LLM不使用）

        全インパクトレベルでコード計算のみ。
        LLM呼び出しは一切行わない。

        Args:
            symbol: シンボル
            base: 基軸通貨
            quote: 決済通貨
            event: 対象イベント

        Returns:
            dict: 分析結果行
        """
        # 共通カラム
        base_row = {
            "event_time": event.event_time.isoformat(),
            "currency": event.currency,
            "event_name": event.event_name,
            "impact": event.impact.value,
            "actual": event.actual,
            "forecast": event.forecast,
            "previous": event.previous,
        }

        # 休日イベント: 親クラスの通貨別固定値
        if _HOLIDAY_RE.search(event.event_name):
            result = self._holiday_result(event.currency)
            base_row.update(result)
            return base_row

        # コード計算
        surprise_score = self._compute_surprise_score(event)
        direction_bias = self._compute_direction_bias(
            event, base, quote
        )

        if event.impact == ImpactLevel.LOW:
            # LOWインパクト: 親クラスと同じ
            result = self._low_impact_result(
                surprise_score, direction_bias
            )
            base_row.update(result)
            return base_row

        # HIGH/MEDIUM: ヒューリスティック計算（LLM不使用）
        convergence = self._compute_convergence_hours(
            event, surprise_score
        )
        volatility = self._compute_expected_volatility(
            event, surprise_score
        )
        caution = self._compute_trade_caution_level(event)
        summary = self._generate_summary(
            symbol, event, surprise_score, direction_bias
        )

        base_row.update(
            {
                "surprise_score": surprise_score,
                "direction_bias": direction_bias,
                "convergence_hours": convergence,
                "expected_volatility": volatility,
                "trade_caution_level": caution,
                "is_holiday": False,
                "summary": summary,
            }
        )
        return base_row
