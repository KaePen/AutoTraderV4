"""MT5定数定義

テスト容易性のため全定数をハードコードする。
"""

from __future__ import annotations

# --- Timeframeマッピング ---
# MT5内部ID → Timeframe文字列
TIMEFRAME_MAP: dict[str, int] = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "M30": 30,
    "H1": 16385,
    "H4": 16388,
    "D1": 16408,
    "W1": 32769,
}

# 逆引き（MT5 ID → 文字列）
TIMEFRAME_REVERSE: dict[int, str] = {
    v: k for k, v in TIMEFRAME_MAP.items()
}

# --- 注文タイプ ---
ORDER_TYPE_BUY = 0
ORDER_TYPE_SELL = 1
ORDER_TYPE_BUY_LIMIT = 2
ORDER_TYPE_SELL_LIMIT = 3
ORDER_TYPE_BUY_STOP = 4
ORDER_TYPE_SELL_STOP = 5

# --- トレードアクション ---
TRADE_ACTION_DEAL = 1       # 成行注文
TRADE_ACTION_PENDING = 5    # 指値注文
TRADE_ACTION_SLTP = 6       # SL/TP変更
TRADE_ACTION_MODIFY = 7     # 注文変更
TRADE_ACTION_REMOVE = 8     # 注文削除
TRADE_ACTION_CLOSE_BY = 10  # 反対売買決済

# --- 注文充填タイプ ---
ORDER_FILLING_FOK = 0       # Fill or Kill
ORDER_FILLING_IOC = 1       # Immediate or Cancel
ORDER_FILLING_RETURN = 2    # Return（残量は指値として残る）

# --- 注文有効期限タイプ ---
ORDER_TIME_GTC = 0          # Good Till Cancel
ORDER_TIME_DAY = 1          # 当日のみ
ORDER_TIME_SPECIFIED = 2    # 指定日時まで

# --- リターンコード ---
TRADE_RETCODE_DONE = 10009          # 成功
TRADE_RETCODE_DONE_PARTIAL = 10010  # 部分約定
TRADE_RETCODE_PLACED = 10008        # 注文設定完了
TRADE_RETCODE_REQUOTE = 10004       # リクォート
TRADE_RETCODE_REJECT = 10006        # リジェクト
TRADE_RETCODE_INVALID = 10013       # 無効リクエスト
TRADE_RETCODE_NO_MONEY = 10019      # 資金不足
TRADE_RETCODE_MARKET_CLOSED = 10018 # 市場閉鎖
TRADE_RETCODE_CONNECTION = 10031    # 接続エラー

# 成功とみなすリターンコード
SUCCESS_RETCODES = {
    TRADE_RETCODE_DONE,
    TRADE_RETCODE_DONE_PARTIAL,
    TRADE_RETCODE_PLACED,
}

# --- ポジションタイプ ---
POSITION_TYPE_BUY = 0
POSITION_TYPE_SELL = 1

# --- ディールタイプ ---
DEAL_TYPE_BUY = 0
DEAL_TYPE_SELL = 1

# --- ディールエントリー ---
DEAL_ENTRY_IN = 0    # エントリー
DEAL_ENTRY_OUT = 1   # イグジット
DEAL_ENTRY_INOUT = 2 # リバース
DEAL_ENTRY_STATE = 3 # ステータス変更

# --- コピーティック用フラグ ---
COPY_TICKS_ALL = 0
COPY_TICKS_INFO = 1
COPY_TICKS_TRADE = 2

# --- デフォルト値 ---
DEFAULT_MAGIC_NUMBER = 20240001  # AutoTraderV4識別用
DEFAULT_DEVIATION = 20           # 許容スリッページ（ポイント）
DEFAULT_SYMBOL = "USDJPY"
