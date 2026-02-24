"""ゴーストポジションのDB修正スクリプト

MT5で既に決済済みだがDBで is_open=true のまま残存している
レコードを検出し、is_open=false に更新する。

注意: エンジン停止中に実行すること。

使い方:
    python scripts/fix_ghost_positions.py [--dry-run] [--symbol USDJPY]
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autotrader.adapters.database.connection import get_session
from autotrader.adapters.database.models import TradeRecord
from autotrader.config.settings import get_settings


def main() -> None:
    """ゴーストレコードの検出・修正"""
    parser = argparse.ArgumentParser(
        description="DBゴーストポジション修正"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="変更を適用せず検出のみ行う",
    )
    parser.add_argument(
        "--symbol",
        default=None,
        help="対象通貨ペア (例: USDJPY). 未指定時は全シンボル",
    )
    args = parser.parse_args()

    print(
        "警告: このスクリプトはエンジン停止中に"
        "実行してください。"
    )

    db_url = get_settings().database_url
    now = datetime.now(timezone.utc)

    with get_session(db_url) as db:
        # is_open=trueのレコードを取得
        query = db.query(TradeRecord).filter(
            TradeRecord.is_open.is_(True)
        )
        if args.symbol:
            query = query.filter(
                TradeRecord.symbol == args.symbol
            )
        open_records = query.all()

        if not open_records:
            print("is_open=true のレコードなし。処理不要。")
            return

        print(
            f"is_open=true レコード: {len(open_records)}件"
        )
        print("-" * 60)

        for r in open_records:
            print(
                f"  ticket={r.ticket}"
                f"  trade_id={r.trade_id}"
                f"  symbol={r.symbol}"
                f"  {r.signal_type}"
                f"  vol={r.volume}"
                f"  entry={r.entry_price}"
                f"  opened={r.opened_at}"
            )

        print("-" * 60)

        if args.dry_run:
            print(
                "[dry-run] 変更は適用されません。"
                " --dry-runを外して再実行してください。"
            )
            return

        confirm = input(
            f"上記 {len(open_records)}件を"
            " is_open=false に更新しますか？ (y/N): "
        )
        if confirm.lower() != "y":
            print("キャンセルしました。")
            return

        for r in open_records:
            r.is_open = False
            r.exit_reason = "GHOST_CLEANUP"
            r.closed_at = now
            print(
                f"  更新: ticket={r.ticket}"
                f" trade_id={r.trade_id}"
            )

        print(
            f"\n{len(open_records)}件を"
            " is_open=false に更新しました。"
        )


if __name__ == "__main__":
    main()
