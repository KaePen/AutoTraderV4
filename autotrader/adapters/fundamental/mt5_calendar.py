"""MT5経済カレンダークライアント

MQL5サービス（CalendarExporter.mq5）が出力するCSVファイルを読み込んで
経済イベントを取得する。
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from loguru import logger

from autotrader.adapters.fundamental.schemas import (
    EconomicEvent,
    EventSource,
    ImpactLevel,
)
from autotrader.adapters.fundamental.normalizer import (
    EconomicEventNormalizer,
)

# CSVファイルの古さ上限（これ以上古いと警告）
_MAX_CSV_AGE_HOURS = 2
# CSVファイル名
_CSV_FILENAME = "calendar_events.csv"
# MT5 Files フォルダ内の相対パス
_CSV_RELATIVE_PATH = f"MQL5/Files/{_CSV_FILENAME}"

# インパクト文字列→ImpactLevelマッピング
_IMPACT_MAP: dict[str, ImpactLevel] = {
    "high": ImpactLevel.HIGH,
    "medium": ImpactLevel.MEDIUM,
    "low": ImpactLevel.LOW,
}


class MT5CalendarClient:
    """MT5経済カレンダークライアント

    MQL5サービスが出力するCSVファイルからイベントを読み込む。
    CSVパスはMT5 terminal_info().data_path から自動検出し、
    MT5未接続時は環境変数 MT5_DATA_PATH をフォールバック。

    Args:
        normalizer: イベント正規化クラス
        csv_path: CSVパスの手動指定（テスト用）
    """

    def __init__(
        self,
        normalizer: EconomicEventNormalizer | None = None,
        csv_path: str | None = None,
    ) -> None:
        """初期化

        Args:
            normalizer: イベント正規化クラス（Noneで自動生成）
            csv_path: CSVパスの手動指定（Noneで自動検出）
        """
        self._normalizer = normalizer or EconomicEventNormalizer()
        self._csv_path_override = csv_path

    def _resolve_csv_path(self) -> Path | None:
        """CSVファイルパスを解決

        優先順位:
        1. コンストラクタで指定された csv_path
        2. MT5 terminal_info().data_path
        3. 環境変数 MT5_DATA_PATH

        Returns:
            Path | None: CSVパス（解決不可はNone）
        """
        # 手動指定
        if self._csv_path_override:
            return Path(self._csv_path_override)

        # MT5 terminal_info から取得
        try:
            import MetaTrader5 as mt5

            info = mt5.terminal_info()
            if info and info.data_path:
                return Path(info.data_path) / _CSV_RELATIVE_PATH
        except (ImportError, AttributeError):
            pass

        # 環境変数フォールバック
        data_path = os.environ.get("MT5_DATA_PATH")
        if data_path:
            return Path(data_path) / _CSV_RELATIVE_PATH

        logger.warning(
            "[MT5Calendar] CSVパスを解決できません。"
            "MT5_DATA_PATH環境変数を設定してください"
        )
        return None

    def fetch_events(
        self,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        currencies: list[str] | None = None,
    ) -> list[EconomicEvent]:
        """CSVからイベントを読み込み

        Args:
            from_date: 取得開始日時（UTC、Noneで現在-1時間）
            to_date: 取得終了日時（UTC、Noneで現在+7日）
            currencies: 対象通貨リスト（Noneで全通貨）

        Returns:
            list[EconomicEvent]: 取得済みイベントリスト
        """
        now = datetime.now(timezone.utc)
        if from_date is None:
            from_date = now - timedelta(days=1)
        if to_date is None:
            to_date = now + timedelta(days=7)

        csv_path = self._resolve_csv_path()
        if csv_path is None:
            return []

        if not csv_path.exists():
            logger.warning(
                f"[MT5Calendar] CSVファイル未検出: {csv_path}"
            )
            return []

        # ファイルの古さチェック
        self._check_csv_freshness(csv_path)

        events: list[EconomicEvent] = []
        fetched_at = datetime.now(timezone.utc)

        try:
            with open(
                csv_path, encoding="utf-16", errors="replace"
            ) as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        event = self._parse_row(
                            row, fetched_at
                        )
                        if event is None:
                            continue
                        # 期間フィルタ
                        if event.event_time < from_date:
                            continue
                        if event.event_time > to_date:
                            continue
                        # 通貨フィルタ
                        if (
                            currencies
                            and event.currency not in currencies
                        ):
                            continue
                        events.append(event)
                    except Exception as e:
                        logger.debug(
                            f"[MT5Calendar] 行パース失敗: {e}"
                        )
                        continue

            logger.info(
                f"[MT5Calendar] {len(events)}件のイベントを取得"
                f" ({from_date.date()} 〜 {to_date.date()})"
            )
            return events

        except Exception as e:
            logger.error(
                f"[MT5Calendar] CSV読み込みエラー: {e}"
            )
            return []

    async def fetch_events_async(
        self,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        currencies: list[str] | None = None,
    ) -> list[EconomicEvent]:
        """経済イベントを非同期取得

        CSV読み込みはI/Oバウンドなのでto_threadでラップ。

        Args:
            from_date: 取得開始日時（UTC）
            to_date: 取得終了日時（UTC）
            currencies: 対象通貨リスト

        Returns:
            list[EconomicEvent]: 取得済みイベントリスト
        """
        return await asyncio.to_thread(
            self.fetch_events,
            from_date=from_date,
            to_date=to_date,
            currencies=currencies,
        )

    def _check_csv_freshness(self, csv_path: Path) -> None:
        """CSVファイルの鮮度を確認

        _MAX_CSV_AGE_HOURS より古い場合は警告ログを出す。

        Args:
            csv_path: CSVファイルパス
        """
        try:
            mtime = datetime.fromtimestamp(
                csv_path.stat().st_mtime, tz=timezone.utc
            )
            age = datetime.now(timezone.utc) - mtime
            if age > timedelta(hours=_MAX_CSV_AGE_HOURS):
                logger.warning(
                    f"[MT5Calendar] CSVが古い可能性"
                    f" (最終更新: {mtime.isoformat()}"
                    f", 経過: {age})"
                )
        except OSError:
            pass

    def _parse_row(
        self, row: dict[str, str], fetched_at: datetime
    ) -> EconomicEvent | None:
        """CSV行をEconomicEventに変換

        Args:
            row: CSVのDictReader行
            fetched_at: 取得時刻

        Returns:
            EconomicEvent | None: 変換済み（変換不可はNone）
        """
        currency = row.get("currency", "").strip()
        if not currency:
            return None

        event_name = row.get("event_name", "").strip()
        if not event_name:
            return None

        # 時刻パース（ISO 8601 UTC）
        time_str = row.get("event_time", "").strip()
        if not time_str:
            return None
        event_time = self._parse_utc_time(time_str)
        if event_time is None:
            return None

        # インパクト
        impact_str = row.get("impact", "low").strip().lower()
        impact = _IMPACT_MAP.get(impact_str, ImpactLevel.LOW)

        # 数値フィールド
        actual = self._parse_float(row.get("actual", ""))
        forecast = self._parse_float(row.get("forecast", ""))
        previous = self._parse_float(row.get("previous", ""))

        raw_id = row.get("event_id", "").strip()
        if not raw_id:
            # hash()は非決定的なのでhashlibで安定IDを生成
            digest = hashlib.md5(
                f"{time_str}{event_name}".encode()
            ).hexdigest()[:16]
            event_id = f"mt5_csv_{digest}"
        else:
            event_id = f"mt5_{raw_id}"

        return EconomicEvent(
            event_id=event_id,
            event_time=event_time,
            currency=currency,
            event_name=event_name,
            impact=impact,
            source=EventSource.MT5,
            fetched_at=fetched_at,
            actual=actual,
            forecast=forecast,
            previous=previous,
        )

    @staticmethod
    def _parse_utc_time(time_str: str) -> datetime | None:
        """UTC時刻文字列をdatetimeに変換

        CalendarExporter.mq5 がブローカーサーバー時刻→UTC変換
        済みで出力するため、Z付き文字列はそのままUTCとして扱う。

        対応フォーマット:
        - 2025-01-15T08:30:00Z (ISO 8601)
        - 2025-01-15 08:30:00

        Args:
            time_str: UTC時刻文字列

        Returns:
            datetime | None: パース済み（失敗はNone）
        """
        for fmt in (
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
        ):
            try:
                dt = datetime.strptime(time_str, fmt)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None

    @staticmethod
    def _parse_float(value: str) -> float | None:
        """文字列をfloatに変換

        空文字・非数値はNoneを返す。

        Args:
            value: 数値文字列

        Returns:
            float | None: 変換結果
        """
        if not value or not value.strip():
            return None
        try:
            return float(value.strip())
        except ValueError:
            return None
