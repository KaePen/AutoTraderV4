"""イベント分析LLMジェネレーター

経済指標CSVからシンボル関連イベントを個別抽出し、
LLMで短期インパクト分析を行い、
llm_events_SYMBOL_YYYY.csv に出力する。

1行 = 1イベント（event_time で秒単位の時系列）。
HIGH/MEDIUMはLLM分析（convergence_hours等）+コード計算（surprise/direction）、
LOWはコード計算のみでLLMスキップ。
休日イベントは固定デフォルト値で出力（LLMスキップ）。
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
    ImpactLevel,
)
from autotrader.config.llm_settings import OllamaSettings

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


class LLMEventGenerator(LLMGeneratorBase):
    """イベント分析LLMジェネレーター

    events_YYYY.csv からシンボル関連イベントを個別抽出し、
    LLM分析結果を llm_events_SYMBOL_YYYY.csv に出力する。

    surprise_score / direction_bias は全インパクトレベルで
    コード計算（LLMに任せない）。
    HIGH/MEDIUMイベントはLLMで convergence_hours,
    expected_volatility, trade_caution_level, summary を生成。
    LOWイベントはコード計算のみでLLMスキップ。
    休日イベントは固定デフォルト値で出力。

    Args:
        ollama_settings: Ollama接続設定
        retry_delay_seconds: リトライ待機秒数
        max_retries: LLM呼び出し最大リトライ回数
    """

    def __init__(
        self,
        ollama_settings: OllamaSettings | None = None,
        retry_delay_seconds: float = 2.0,
        max_retries: int = 3,
    ) -> None:
        """初期化

        Args:
            ollama_settings: Ollama接続設定
            retry_delay_seconds: リトライ待機秒数
            max_retries: LLM呼び出し最大リトライ回数
        """
        super().__init__(
            ollama_settings=ollama_settings,
            retry_delay_seconds=retry_delay_seconds,
            max_retries=max_retries,
        )

    def generate_for_symbol_year(
        self,
        symbol: str,
        year: int,
        events: list[EconomicEvent],
        output_dir: str | Path = "data/fundamental",
        overwrite: bool = False,
    ) -> Path:
        """指定シンボル・年のイベントLLM CSVを生成

        1行 = 1イベント。発表済みイベントのみ出力。
        1件処理ごとにCSV保存（resume対応）。

        Args:
            symbol: 対象シンボル（例: USDJPY）
            year: 対象年
            events: 全経済イベントリスト（全通貨含む）
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

        # 対象通貨・年のイベント抽出（時系列ソート）
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
            # 0件でもCSVを作成（ヘッダーのみ）
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
        llm_calls = 0
        skipped = 0

        for idx in range(resume_idx, total):
            ev = targets[idx]
            row = self._analyze_event(
                symbol, base, quote, ev
            )

            if _HOLIDAY_RE.search(ev.event_name):
                skipped += 1
            elif ev.impact in (
                ImpactLevel.HIGH,
                ImpactLevel.MEDIUM,
            ):
                llm_calls += 1
            else:
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
                    f"{done}/{total}件完了 "
                    f"(LLM:{llm_calls}, skip:{skipped})"
                )

        logger.info(
            f"[EventGen] 完了: {output_path} "
            f"({len(rows)}件, LLM:{llm_calls}, "
            f"skip:{skipped})"
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

    def _filter_events(
        self,
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
    # コード計算メソッド（LLMに任せない統一計算）
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
        for keyword, direction in _INVERSE_INDICATORS.items():
            if keyword in name_lower:
                return direction
        return 1

    @staticmethod
    def _compute_surprise_score(
        event: EconomicEvent,
    ) -> float:
        """サプライズスコアをコード計算

        EconomicEvent.surprise_magnitude を [-1, 1] に
        クリップして返す。

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

        surprise_score × indicator_direction で通貨方向を
        算出し、ペア方向 × インパクトスケールで最終値を返す。

        Args:
            event: 対象イベント
            base: 基軸通貨
            quote: 決済通貨

        Returns:
            float: 方向バイアス [-1, 1]
        """
        surprise_score = LLMEventGenerator._compute_surprise_score(
            event
        )
        if surprise_score == 0.0:
            return 0.0

        indicator_dir = (
            LLMEventGenerator._get_indicator_direction(
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
    # イベント分析
    # --------------------------------------------------

    def _analyze_event(
        self,
        symbol: str,
        base: str,
        quote: str,
        event: EconomicEvent,
    ) -> dict:
        """単一イベントを分析

        surprise_score / direction_bias は全レベルで
        コード計算。HIGH/MEDIUM は LLM で残りの項目を分析。
        LOW は LLM スキップ。

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

        # 休日イベント: 通貨別固定値（LLMスキップ）
        if _HOLIDAY_RE.search(event.event_name):
            result = self._holiday_result(event.currency)
            base_row.update(result)
            return base_row

        # コード計算（全インパクトレベル共通）
        surprise_score = self._compute_surprise_score(event)
        direction_bias = self._compute_direction_bias(
            event, base, quote
        )

        if event.impact == ImpactLevel.LOW:
            # LOWインパクト: LLMスキップ
            result = self._low_impact_result(
                surprise_score, direction_bias
            )
            base_row.update(result)
            return base_row

        # HIGH/MEDIUM: LLM分析（convergence等のみ）
        prompt = self._build_event_prompt(
            symbol, base, quote, event,
            surprise_score, direction_bias,
        )
        default = self._default_event_result()
        raw = self._call_ollama_with_retry(prompt, default)
        result = self._build_event_result(
            raw, surprise_score, direction_bias
        )
        base_row.update(result)
        return base_row

    def _build_event_prompt(
        self,
        symbol: str,
        base: str,
        quote: str,
        event: EconomicEvent,
        surprise_score: float,
        direction_bias: float,
    ) -> str:
        """単一イベント分析プロンプトを構築

        surprise_score / direction_bias はコード計算済みのため
        LLM には convergence_hours, expected_volatility,
        trade_caution_level, summary のみ生成を依頼する。

        Args:
            symbol: シンボル
            base: 基軸通貨
            quote: 決済通貨
            event: 対象イベント
            surprise_score: コード計算済みサプライズ
            direction_bias: コード計算済み方向バイアス

        Returns:
            str: プロンプト文字列
        """
        impact_label = _IMPACT_LABELS.get(
            event.impact, event.impact.value
        )
        time_str = event.event_time.strftime(
            "%Y-%m-%d %H:%M"
        )
        actual = (
            f"{event.actual:.2f}"
            if event.actual is not None
            else "未発表"
        )
        forecast = (
            f"{event.forecast:.2f}"
            if event.forecast is not None
            else "予測なし"
        )
        previous = (
            f"{event.previous:.2f}"
            if event.previous is not None
            else "前回なし"
        )

        return f"""{symbol}への経済指標インパクトを分析してください。

分析対象: {symbol} ({base}/{quote})
発表時刻: {time_str} UTC

経済指標:
- 指標名: {event.event_name}
- 通貨: {event.currency}
- インパクト: {impact_label}
- 実績値: {actual}
- 予測値: {forecast}
- 前回値: {previous}

コード計算済み参考値:
- サプライズスコア: {surprise_score:+.4f}
- 方向バイアス: {direction_bias:+.4f}

分析指示:
1. 上記サプライズを踏まえ、インパクトの持続時間を推定
2. ボラティリティへの影響度を評価
3. トレード注意度を判定

以下のJSONを返してください:
{{"convergence_hours": 1.0, "expected_volatility": 1.0, "trade_caution_level": 0, "summary": ""}}

各値の意味:
convergence_hours: インパクト収束推定時間。0.5~72.0
expected_volatility: 通常比ボラティリティ倍率。0.0~2.0
trade_caution_level: 取引注意度。0=通常、1=注意、2=回避推奨
summary: 分析要約。日本語200文字以内"""

    def _build_event_result(
        self,
        data: dict,
        surprise_score: float,
        direction_bias: float,
    ) -> dict:
        """LLMレスポンス+コード計算値からイベント結果dictを構築

        surprise_score / direction_bias はコード計算値を使用。
        LLM レスポンスからは convergence_hours,
        expected_volatility, trade_caution_level, summary
        のみ取得する。

        Args:
            data: LLMレスポンスdict
            surprise_score: コード計算済みサプライズ
            direction_bias: コード計算済み方向バイアス

        Returns:
            dict: バリデーション済み結果
        """
        caution = data.get("trade_caution_level")
        if caution is None or not isinstance(
            caution, (int, float)
        ):
            caution_val = 0
        else:
            caution_val = max(0, min(2, int(caution)))

        return {
            "surprise_score": surprise_score,
            "direction_bias": direction_bias,
            "convergence_hours": self._clip(
                data.get("convergence_hours"),
                0.5,
                72.0,
                0.0,
            ),
            "expected_volatility": self._clip(
                data.get("expected_volatility"),
                0.0,
                2.0,
                1.0,
            ),
            "trade_caution_level": caution_val,
            "is_holiday": False,
            "summary": str(
                data.get("summary", "")
            )[:200],
        }

    @staticmethod
    def _default_event_result() -> dict:
        """LLM失敗時のデフォルト結果

        Returns:
            dict: デフォルト値辞書
        """
        return {
            "convergence_hours": 0.0,
            "expected_volatility": 1.0,
            "trade_caution_level": 0,
            "is_holiday": False,
            "summary": "",
        }

    @staticmethod
    def _holiday_result(currency: str) -> dict:
        """市場休日の通貨別固定デフォルト値

        各市場の休日特性に基づく分析済みデフォルト値。
        FX取引シェアと各セッションの重要度を考慮。

        - USD: NY市場閉鎖。全FX取引の88%にUSD関与。
          流動性激減→回避推奨(caution=2)。
        - GBP: ロンドン市場閉鎖。世界最大FXハブ
          (取引シェア38%)不在→回避推奨。
        - JPY: 東京市場閉鎖。アジアセッション薄商い
          だがロンドン/NYで補完→注意(caution=1)。
        - EUR: 欧州セッション部分閉鎖。ロンドンと
          重複するためGBPほどではないが流動性低下。
        - AUD/NZD: 太平洋セッションは比較的小規模。
          他セッションで十分補完→影響軽微。
        - CAD: NY重複セッション。USD休日と同時が
          多く、単独では中程度の影響。
        - CHF: 欧州セッション。EURと同様の影響度。

        Args:
            currency: 休日対象の通貨コード

        Returns:
            dict: 通貨別休日固定値辞書
        """
        # (volatility, caution, convergence_h, summary)
        params = _HOLIDAY_PARAMS.get(
            currency,
            (0.4, 1, 16.0, "市場休日 - 流動性低下に注意"),
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

        コード計算済みの surprise_score / direction_bias を
        使用。LLMスキップ。

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
