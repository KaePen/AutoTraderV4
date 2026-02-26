"""LLMGeneratorBase テスト"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autotrader.adapters.fundamental.llm_generator_base import (
    SYMBOL_CURRENCIES,
    LLMGeneratorBase,
)


class TestParseJsonResponse:
    """_parse_json_response のテスト"""

    def setup_method(self) -> None:
        """テスト用インスタンス"""
        self.gen = LLMGeneratorBase()

    def test_direct_json(self) -> None:
        """直接JSONパース成功"""
        content = '{"score": 0.5, "summary": "テスト"}'
        result = self.gen._parse_json_response(content)
        assert result == {"score": 0.5, "summary": "テスト"}

    def test_code_block(self) -> None:
        """```json```コードブロック抽出"""
        content = (
            "分析結果:\n```json\n"
            '{"score": 0.3}\n```\n以上です。'
        )
        result = self.gen._parse_json_response(content)
        assert result == {"score": 0.3}

    def test_brace_extraction(self) -> None:
        """{...}ブレース抽出"""
        content = (
            '分析結果: {"score": -0.2, "text": "abc"} 以上'
        )
        result = self.gen._parse_json_response(content)
        assert result == {"score": -0.2, "text": "abc"}

    def test_brace_extraction_nested(self) -> None:
        """1段ネストJSON -> 外側オブジェクト全体を抽出"""
        content = (
            '分析結果: {"score": 0.5, '
            '"session_sentiment": {"asian": 0.1}} 以上'
        )
        result = self.gen._parse_json_response(content)
        assert result["score"] == 0.5
        assert result["session_sentiment"] == {
            "asian": 0.1
        }

    def test_deepseek_think_block(self) -> None:
        """DeepSeek-R1の<think>ブロックを除去してパース"""
        content = (
            "<think>ここは推論部分です。"
            "JSONを生成します。</think>"
            '{"score": 0.4, "summary": "分析結果"}'
        )
        result = self.gen._parse_json_response(content)
        assert result == {
            "score": 0.4,
            "summary": "分析結果",
        }

    def test_deepseek_think_multiline(self) -> None:
        """<think>ブロックが複数行の場合"""
        content = (
            "<think>\nニュースを分析中...\n"
            "センチメントは強気。\n</think>\n"
            '{"score": -0.3}'
        )
        result = self.gen._parse_json_response(content)
        assert result == {"score": -0.3}

    def test_deepseek_think_only(self) -> None:
        """<think>のみでJSONなし -> ValueError"""
        content = "<think>推論のみ</think>"
        with pytest.raises(ValueError, match="JSON解析失敗"):
            self.gen._parse_json_response(content)

    def test_invalid_raises(self) -> None:
        """不正JSON -> ValueError"""
        with pytest.raises(ValueError, match="JSON解析失敗"):
            self.gen._parse_json_response("これはJSONではない")


class TestClip:
    """_clip / _clip_score のテスト"""

    def test_within_range(self) -> None:
        """範囲内の値はそのまま"""
        assert LLMGeneratorBase._clip(0.5, -1.0, 1.0) == 0.5

    def test_above_max(self) -> None:
        """上限クリップ"""
        assert LLMGeneratorBase._clip(1.5, -1.0, 1.0) == 1.0

    def test_below_min(self) -> None:
        """下限クリップ"""
        assert LLMGeneratorBase._clip(-2.0, -1.0, 1.0) == -1.0

    def test_none_returns_default(self) -> None:
        """None -> デフォルト"""
        assert LLMGeneratorBase._clip(None, -1.0, 1.0) == 0.0

    def test_none_custom_default(self) -> None:
        """None + カスタムデフォルト"""
        assert (
            LLMGeneratorBase._clip(
                None, 0.0, 2.0, default=1.0
            )
            == 1.0
        )

    def test_non_numeric_returns_default(self) -> None:
        """非数値 -> デフォルト"""
        assert (
            LLMGeneratorBase._clip("abc", -1.0, 1.0) == 0.0
        )

    def test_clip_score_positive(self) -> None:
        """clip_score: 正常値"""
        assert LLMGeneratorBase._clip_score(0.7) == 0.7

    def test_clip_score_overflow(self) -> None:
        """clip_score: 上限オーバー"""
        assert LLMGeneratorBase._clip_score(1.5) == 1.0

    def test_clip_score_underflow(self) -> None:
        """clip_score: 下限アンダー"""
        assert LLMGeneratorBase._clip_score(-1.5) == -1.0


class TestWriteCsv:
    """_write_csv のテスト"""

    def test_creates_file(self, tmp_path: Path) -> None:
        """CSV書き込み + 内容検証"""
        output = tmp_path / "test.csv"
        rows = [
            {"a": "1", "b": "hello"},
            {"a": "2", "b": "world"},
        ]
        LLMGeneratorBase._write_csv(
            rows, ["a", "b"], output
        )

        assert output.exists()
        lines = output.read_text(encoding="utf-8").strip().split("\n")
        assert lines[0] == "a,b"
        assert lines[1] == "1,hello"
        assert lines[2] == "2,world"

    def test_creates_parent_dirs(
        self, tmp_path: Path
    ) -> None:
        """親ディレクトリ自動作成"""
        output = tmp_path / "sub" / "dir" / "test.csv"
        LLMGeneratorBase._write_csv(
            [{"x": "1"}], ["x"], output
        )
        assert output.exists()


class TestGenerateDateRange:
    """_generate_date_range のテスト"""

    def test_regular_year(self) -> None:
        """通常年365日"""
        days = LLMGeneratorBase._generate_date_range(2023)
        assert len(days) == 365
        assert days[0] == date(2023, 1, 1)
        assert days[-1] == date(2023, 12, 31)

    def test_leap_year(self) -> None:
        """閏年366日"""
        days = LLMGeneratorBase._generate_date_range(2024)
        assert len(days) == 366
        assert date(2024, 2, 29) in days


class TestGetSymbolCurrencies:
    """get_symbol_currencies のテスト"""

    def test_valid_usdjpy(self) -> None:
        """USDJPY -> (USD, JPY)"""
        base, quote = LLMGeneratorBase.get_symbol_currencies(
            "USDJPY"
        )
        assert base == "USD"
        assert quote == "JPY"

    def test_valid_eurusd(self) -> None:
        """EURUSD -> (EUR, USD)"""
        base, quote = LLMGeneratorBase.get_symbol_currencies(
            "EURUSD"
        )
        assert base == "EUR"
        assert quote == "USD"

    def test_invalid_raises(self) -> None:
        """未対応シンボル -> ValueError"""
        with pytest.raises(ValueError, match="未対応シンボル"):
            LLMGeneratorBase.get_symbol_currencies("INVALID")

    def test_all_symbols_mapped(self) -> None:
        """全シンボルが2文字通貨ペアにマッピング"""
        for symbol, (base, quote) in SYMBOL_CURRENCIES.items():
            assert len(base) == 3
            assert len(quote) == 3


class TestCallOllamaWithRetry:
    """_call_ollama_with_retry のテスト"""

    def test_success_first_try(self) -> None:
        """1回目成功"""
        gen = LLMGeneratorBase(max_retries=3)
        with patch.object(
            gen,
            "_call_ollama",
            return_value={"score": 0.5},
        ):
            result = gen._call_ollama_with_retry(
                "test", {"score": 0.0}
            )
        assert result == {"score": 0.5}

    def test_all_retries_fail(self) -> None:
        """全リトライ失敗 -> デフォルト"""
        gen = LLMGeneratorBase(
            max_retries=2, retry_delay_seconds=0.0
        )
        default = {"score": -1.0}
        with patch.object(
            gen,
            "_call_ollama",
            side_effect=RuntimeError("fail"),
        ):
            result = gen._call_ollama_with_retry(
                "test", default
            )
        assert result == default

    def test_retry_then_success(self) -> None:
        """1回失敗後に成功"""
        gen = LLMGeneratorBase(
            max_retries=3, retry_delay_seconds=0.0
        )
        with patch.object(
            gen,
            "_call_ollama",
            side_effect=[
                RuntimeError("fail"),
                {"score": 0.8},
            ],
        ):
            result = gen._call_ollama_with_retry(
                "test", {"score": 0.0}
            )
        assert result == {"score": 0.8}
