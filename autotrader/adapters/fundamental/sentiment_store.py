"""通貨ペア別センチメント永続化ストア

JSONL形式でセンチメントレコードを保存・復元する。
"""
from __future__ import annotations

import json
import logging
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class SentimentRecord:
    """センチメントレコード

    Args:
        timestamp: 記録時刻(ISO形式文字列)
        score: センチメントスコア (-1.0~+1.0)
        method: 分析手法 ("keyword" or "llm")
        confidence: 確信度 (0.0~1.0)
        news_count: 分析ニュース件数
        top_headlines: 上位見出し(最大3件)
    """

    timestamp: str
    score: float
    method: str  # "keyword" | "llm"
    confidence: float
    news_count: int
    top_headlines: list[str]


class SentimentStore:
    """通貨ペア別センチメント永続化

    保存先: data/{SYMBOL}/sentiment.jsonl
    """

    def __init__(
        self,
        data_dir: str = "data",
        rotation_days: int = 7,
    ) -> None:
        """初期化

        Args:
            data_dir: データディレクトリパス
            rotation_days: ローテーション日数
        """
        self._data_dir = Path(data_dir)
        self._rotation_days = rotation_days

    def _get_file_path(self, symbol: str) -> Path:
        """シンボル別ファイルパスを取得

        Args:
            symbol: 通貨ペア

        Returns:
            Path: JSONLファイルパス
        """
        return self._data_dir / symbol / "sentiment.jsonl"

    def save(
        self, symbol: str, record: SentimentRecord
    ) -> None:
        """レコードを追記保存

        Args:
            symbol: 通貨ペア
            record: センチメントレコード
        """
        path = self._get_file_path(symbol)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    asdict(record), ensure_ascii=False
                )
                + "\n"
            )
        # ローテーション
        self._rotate_if_needed(symbol)

    def load_latest(
        self, symbol: str
    ) -> SentimentRecord | None:
        """最新レコードを読み込み

        Args:
            symbol: 通貨ペア

        Returns:
            SentimentRecord | None: 最新レコードまたはNone
        """
        path = self._get_file_path(symbol)
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if not lines:
                return None
            last_line = lines[-1].strip()
            if not last_line:
                return None
            data = json.loads(last_line)
            return SentimentRecord(**data)
        except Exception as e:
            logger.warning(
                "[SentimentStore] 読み込みエラー: %s", e
            )
            return None

    def load_history(
        self, symbol: str, hours: int = 24
    ) -> list[SentimentRecord]:
        """過去N時間のレコードを読み込み

        Args:
            symbol: 通貨ペア
            hours: 取得期間（時間）

        Returns:
            list[SentimentRecord]: レコードリスト
        """
        path = self._get_file_path(symbol)
        if not path.exists():
            return []
        cutoff = datetime.now(
            timezone.utc
        ) - timedelta(hours=hours)
        records: list[SentimentRecord] = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    rec = SentimentRecord(**data)
                    rec_time = datetime.fromisoformat(
                        rec.timestamp
                    )
                    if rec_time >= cutoff:
                        records.append(rec)
        except Exception as e:
            logger.warning(
                "[SentimentStore] 履歴読み込みエラー: %s",
                e,
            )
        return records

    def _rotate_if_needed(self, symbol: str) -> None:
        """rotation_days超の古いレコードを削除

        Args:
            symbol: 通貨ペア
        """
        path = self._get_file_path(symbol)
        if not path.exists():
            return
        cutoff = datetime.now(
            timezone.utc
        ) - timedelta(days=self._rotation_days)
        try:
            new_lines: list[str] = []
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line_stripped = line.strip()
                    if not line_stripped:
                        continue
                    try:
                        data = json.loads(line_stripped)
                        rec_time = datetime.fromisoformat(
                            data["timestamp"]
                        )
                        if rec_time >= cutoff:
                            new_lines.append(
                                line_stripped + "\n"
                            )
                    except (
                        json.JSONDecodeError,
                        KeyError,
                        ValueError,
                    ):
                        continue
            # アトミック書き込み（tmpファイル→リネーム）
            tmp_path = path.with_suffix(".jsonl.tmp")
            with open(
                tmp_path, "w", encoding="utf-8"
            ) as f:
                f.writelines(new_lines)
            shutil.move(str(tmp_path), str(path))
        except Exception as e:
            logger.warning(
                "[SentimentStore] ローテーションエラー: %s",
                e,
            )
