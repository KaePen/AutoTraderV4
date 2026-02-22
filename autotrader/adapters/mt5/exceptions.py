"""MT5関連例外クラス"""

from __future__ import annotations


class MT5Error(Exception):
    """MT5基底エラー

    Attributes:
        message: エラーメッセージ
        code: MT5エラーコード
    """

    def __init__(
        self, message: str = "", code: int | None = None
    ) -> None:
        self.code = code
        super().__init__(message)


class MT5ConnectionError(MT5Error):
    """MT5接続エラー

    ターミナルへの接続/ログイン失敗時に送出。
    """


class MT5ExecutionError(MT5Error):
    """MT5注文実行エラー

    注文送信/変更/決済の失敗時に送出。

    Attributes:
        retcode: MT5リターンコード
    """

    def __init__(
        self,
        message: str = "",
        code: int | None = None,
        retcode: int | None = None,
    ) -> None:
        self.retcode = retcode
        super().__init__(message, code)


class MT5DataError(MT5Error):
    """MT5データ取得エラー

    ローソク足/ティック/シンボル情報の取得失敗時に送出。
    """
