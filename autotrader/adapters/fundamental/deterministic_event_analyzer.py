"""決定論的イベント分析モジュール

LLMを一切使わず、ヒューリスティックルールのみで
全フィールドを計算するイベント分析クラス。

バッチCSV生成とリアルタイム単一イベント分析の両方に対応。
生成速度: 数千件/秒（LLM版の数百倍以上）。
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

from loguru import logger

from autotrader.adapters.fundamental.llm_generator_base import (
    LLMGeneratorBase,
)
from autotrader.adapters.fundamental.schemas import (
    EconomicEvent,
    EventLLMRecord,
    ImpactLevel,
)

# 休日判定パターン
_HOLIDAY_RE = re.compile(r"holiday", re.IGNORECASE)

# イベントCSVカラム定義（1行=1イベント）
EVENT_CSV_COLUMNS = [
    "event_time",
    "currency",
    "event_name",
    "impact",
    "actual",
    "forecast",
    "previous",
    "surprise_score",
    "direction_bias",
    "convergence_hours",
    "expected_volatility",
    "trade_caution_level",
    "is_holiday",
    "summary",
]

# インパクト表記
_IMPACT_LABELS: dict[ImpactLevel, str] = {
    ImpactLevel.HIGH: "高インパクト",
    ImpactLevel.MEDIUM: "中インパクト",
    ImpactLevel.LOW: "低インパクト",
}

# 実績が予想を上回ったとき、その通貨にとって弱気(-1)な指標
# デフォルト: +1（大半の指標は「高い=通貨高」）
_INVERSE_INDICATORS: dict[str, int] = {
    "unemployment": -1,
    "jobless": -1,
    "claimant": -1,
}

# インパクト別スケーリング係数
_IMPACT_SCALE: dict[ImpactLevel, float] = {
    ImpactLevel.HIGH: 0.8,
    ImpactLevel.MEDIUM: 0.5,
    ImpactLevel.LOW: 0.3,
}

# 通貨別休日パラメータ
# (expected_volatility, trade_caution_level,
#  convergence_hours, summary)
_HOLIDAY_PARAMS: dict[
    str, tuple[float, int, float, str]
] = {
    "USD": (
        0.2,
        2,
        24.0,
        "米国市場休日 - 流動性激減・取引回避推奨",
    ),
    "GBP": (
        0.3,
        2,
        20.0,
        "英国市場休日 - ロンドンFXハブ不在"
        "・流動性大幅低下",
    ),
    "JPY": (
        0.5,
        1,
        12.0,
        "日本市場休日 - アジアセッション薄商い"
        "・ロンドン以降正常化",
    ),
    "EUR": (
        0.4,
        1,
        16.0,
        "欧州市場休日 - 欧州セッション"
        "・流動性低下",
    ),
    "AUD": (
        0.6,
        1,
        8.0,
        "豪州市場休日 - 影響軽微",
    ),
    "NZD": (
        0.6,
        1,
        8.0,
        "NZ市場休日 - 影響軽微",
    ),
    "CAD": (
        0.5,
        1,
        12.0,
        "加国市場休日 - NY重複セッション"
        "・流動性低下",
    ),
    "CHF": (
        0.4,
        1,
        16.0,
        "スイス市場休日 - 欧州セッション"
        "・流動性低下",
    ),
}

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
        [
            "retail sales",
            "consumer spending",
            "consumer confidence",
        ],
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


class DeterministicEventAnalyzer(LLMGeneratorBase):
    """決定論的イベント分析クラス

    LLMを使わず、全フィールドをヒューリスティックで計算。
    バッチCSV生成とリアルタイム単一イベント分析の両方に対応。

    数千件/秒の処理が可能。
    """

    def __init__(self) -> None:
        """初期化（LLM設定は不要）"""
        super().__init__(
            ollama_settings=None,
            retry_delay_seconds=0,
            max_retries=0,
        )

    # --------------------------------------------------
    # リアルタイム単一イベント分析API
    # --------------------------------------------------

    def analyze_single_event(
        self,
        symbol: str,
        event: EconomicEvent,
    ) -> EventLLMRecord:
        """単一イベントをリアルタイム分析し EventLLMRecord を返す

        ライブトレード用。MT5から取得した経済イベントを
        即座に分析し、FundamentalContext合成に使える形式で返す。

        Args:
            symbol: 対象シンボル（例: USDJPY）
            event: 分析対象の経済イベント

        Returns:
            EventLLMRecord: 分析結果レコード
        """
        base, quote = self.get_symbol_currencies(symbol)
        row = self._analyze_event(symbol, base, quote, event)

        return EventLLMRecord(
            event_time=event.event_time,
            currency=row["currency"],
            event_name=row["event_name"],
            impact=row["impact"],
            surprise_score=float(row.get(
                "surprise_score", 0.0,
            )),
            direction_bias=float(row.get(
                "direction_bias", 0.0,
            )),
            convergence_hours=float(row.get(
                "convergence_hours", 0.0,
            )),
            expected_volatility=float(row.get(
                "expected_volatility", 1.0,
            )),
            trade_caution_level=int(row.get(
                "trade_caution_level", 0,
            )),
            is_holiday=bool(row.get("is_holiday", False)),
        )

    # --------------------------------------------------
    # バッチCSV生成
    # --------------------------------------------------

    def generate_for_symbol_year(
        self,
        symbol: str,
        year: int,
        events: list[EconomicEvent],
        output_dir: str | Path = "data/fundamental",
        overwrite: bool = False,
    ) -> Path:
        """指定シンボル・年のイベントCSVを生成

        1行 = 1イベント。発表済みイベントのみ出力。
        1件処理ごとにCSV保存（resume対応）。

        Args:
            symbol: 対象シンボル
            year: 対象年
            events: 全経済イベントリスト
            output_dir: 出力ディレクトリ
            overwrite: 既存ファイル上書き

        Returns:
            Path: 生成したCSVパス
        """
        output_path = (
            Path(output_dir)
            / f"llm_events_{symbol}_{year}.csv"
        )

        base, quote = self.get_symbol_currencies(symbol)

        # 対象通貨・年のイベント抽出
        relevant = sorted(
            self._filter_events(
                events, (base, quote), year
            ),
            key=lambda ev: ev.event_time,
        )

        # 発表済みイベント + 休日イベントを対象
        targets = [
            ev
            for ev in relevant
            if ev.is_released
            or _HOLIDAY_RE.search(ev.event_name)
        ]

        logger.info(
            f"[EventGen] {symbol}/{year}: "
            f"全{len(events)}件→関連{len(relevant)}件"
            f"→分析対象{len(targets)}件"
        )

        # resume: 既存CSVの処理済み件数を取得
        existing_rows = self._read_existing_rows(
            output_path, overwrite
        )
        resume_idx = len(existing_rows)

        if resume_idx >= len(targets):
            if not output_path.exists():
                self._write_csv(
                    [], EVENT_CSV_COLUMNS, output_path
                )
            logger.info(
                f"[EventGen] スキップ（完了済み）: "
                f"{output_path} ({resume_idx}件)"
            )
            return output_path

        if resume_idx > 0:
            logger.info(
                f"[EventGen] resume: {resume_idx}件処理済み"
                f"→残り{len(targets) - resume_idx}件"
            )

        rows = list(existing_rows)
        total = len(targets)
        skipped = 0

        for idx in range(resume_idx, total):
            ev = targets[idx]
            row = self._analyze_event(
                symbol, base, quote, ev
            )
            skipped += 1
            rows.append(row)

            # 毎回保存（resume対応）
            self._write_csv(
                rows, EVENT_CSV_COLUMNS, output_path
            )

            done = idx + 1
            if done % 10 == 0 or done == total:
                logger.info(
                    f"[EventGen] {symbol}/{year}: "
                    f"{done}/{total}件完了"
                )

        logger.info(
            f"[EventGen] 完了: {output_path} "
            f"({len(rows)}件)"
        )
        return output_path

    def _read_existing_rows(
        self,
        output_path: Path,
        overwrite: bool,
    ) -> list[dict]:
        """既存CSVの処理済み行を読み込み

        Args:
            output_path: CSVパス
            overwrite: 上書きモード

        Returns:
            list[dict]: 処理済み行
        """
        if overwrite:
            return []
        return self._read_existing_csv(
            output_path, EVENT_CSV_COLUMNS
        )

    @staticmethod
    def _filter_events(
        events: list[EconomicEvent],
        currencies: tuple[str, str],
        year: int,
    ) -> list[EconomicEvent]:
        """対象シンボル・年のイベントのみ抽出

        Args:
            events: 全イベント
            currencies: (base, quote)
            year: 対象年

        Returns:
            list[EconomicEvent]: フィルタ済み
        """
        return [
            ev
            for ev in events
            if ev.currency in currencies
            and ev.event_time.year == year
        ]

    # --------------------------------------------------
    # コード計算メソッド
    # --------------------------------------------------

    @staticmethod
    def _get_indicator_direction(
        event_name: str,
    ) -> int:
        """指標名から方向性を判定

        実績が予想を上回ったとき、通貨にとって
        強気(+1)か弱気(-1)かを返す。

        Args:
            event_name: 指標名

        Returns:
            int: +1（高い=通貨高）or -1（高い=通貨安）
        """
        name_lower = event_name.lower()
        for keyword, direction in (
            _INVERSE_INDICATORS.items()
        ):
            if keyword in name_lower:
                return direction
        return 1

    @staticmethod
    def _compute_surprise_score(
        event: EconomicEvent,
    ) -> float:
        """サプライズスコアをコード計算

        Args:
            event: 対象イベント

        Returns:
            float: サプライズスコア [-1, 1]
        """
        surprise = event.surprise_magnitude
        if surprise is None:
            return 0.0
        return max(-1.0, min(1.0, surprise))

    @staticmethod
    def _compute_direction_bias(
        event: EconomicEvent,
        base: str,
        quote: str,
    ) -> float:
        """方向バイアスをコード計算

        Args:
            event: 対象イベント
            base: 基軸通貨
            quote: 決済通貨

        Returns:
            float: 方向バイアス [-1, 1]
        """
        surprise_score = (
            DeterministicEventAnalyzer._compute_surprise_score(
                event
            )
        )
        if surprise_score == 0.0:
            return 0.0

        indicator_dir = (
            DeterministicEventAnalyzer._get_indicator_direction(
                event.event_name
            )
        )
        # 通貨方向: サプライズ × 指標方向
        currency_direction = surprise_score * indicator_dir

        # ペア方向: 基軸通貨なら正、決済通貨なら反転
        if event.currency == base:
            pair_bias = currency_direction
        else:
            pair_bias = -currency_direction

        # インパクトスケーリング
        scale = _IMPACT_SCALE.get(event.impact, 0.3)
        result = pair_bias * scale
        return max(-1.0, min(1.0, result))

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
        for keywords, base_hours, category in (
            _INDICATOR_CATEGORIES
        ):
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

        # サプライズ強度による追加
        surprise_extra = (
            abs(surprise_score) * base_hours * 0.5
        )

        hours = base_hours * impact_mul + surprise_extra
        return max(0.5, min(72.0, round(hours, 1)))

    @staticmethod
    def _compute_expected_volatility(
        event: EconomicEvent,
        surprise_score: float,
    ) -> float:
        """期待ボラティリティをヒューリスティックで計算

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
        ccy_name = _CCY_NAMES.get(
            event.currency, event.currency,
        )
        evt_jp = _EVENT_JP.get(
            event.event_name, event.event_name,
        )
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
    # 休日・LOWインパクト結果
    # --------------------------------------------------

    @staticmethod
    def _holiday_result(currency: str) -> dict:
        """市場休日の通貨別固定デフォルト値

        Args:
            currency: 休日対象の通貨コード

        Returns:
            dict: 通貨別休日固定値辞書
        """
        params = _HOLIDAY_PARAMS.get(
            currency,
            (
                0.4, 1, 16.0,
                "市場休日 - 流動性低下に注意",
            ),
        )
        vol, caution, conv_h, summary = params
        return {
            "surprise_score": 0.0,
            "direction_bias": 0.0,
            "convergence_hours": conv_h,
            "expected_volatility": vol,
            "trade_caution_level": caution,
            "is_holiday": True,
            "summary": summary,
        }

    @staticmethod
    def _low_impact_result(
        surprise_score: float,
        direction_bias: float,
    ) -> dict:
        """LOWインパクトイベントのデフォルト結果

        Args:
            surprise_score: コード計算済みサプライズ
            direction_bias: コード計算済み方向バイアス

        Returns:
            dict: 計算結果辞書
        """
        return {
            "surprise_score": surprise_score,
            "direction_bias": direction_bias,
            "convergence_hours": 1.0,
            "expected_volatility": 0.5,
            "trade_caution_level": 0,
            "is_holiday": False,
            "summary": "低インパクト指標",
        }

    # --------------------------------------------------
    # _analyze_event（LLM不使用）
    # --------------------------------------------------

    def _analyze_event(
        self,
        symbol: str,
        base: str,
        quote: str,
        event: EconomicEvent,
    ) -> dict:
        """単一イベントを決定論的に分析（LLM不使用）

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

        # 休日イベント: 通貨別固定値
        if _HOLIDAY_RE.search(event.event_name):
            result = self._holiday_result(event.currency)
            base_row.update(result)
            return base_row

        # コード計算
        surprise_score = self._compute_surprise_score(
            event,
        )
        direction_bias = self._compute_direction_bias(
            event, base, quote
        )

        if event.impact == ImpactLevel.LOW:
            result = self._low_impact_result(
                surprise_score, direction_bias
            )
            base_row.update(result)
            return base_row

        # HIGH/MEDIUM: ヒューリスティック計算
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
