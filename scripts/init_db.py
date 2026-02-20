"""データベース初期化スクリプト

Supabase PostgreSQL または SQLite にテーブルを作成する。

使用方法:
    python scripts/init_db.py

環境変数 DATABASE_URL が設定されていればそのDBに接続する。
設定されていなければ data/autotrader.db (SQLite) に接続する。
"""

from __future__ import annotations

import sys
from pathlib import Path

# srcをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from autotrader.adapters.database.connection import init_db, get_engine
from autotrader.adapters.database.models import Base
from autotrader.config.settings import get_settings


def main() -> None:
    """メイン処理"""
    settings = get_settings()
    db_url = settings.database_url

    # URLの表示（パスワードを隠す）
    display_url = db_url
    if "@" in db_url:
        # postgresql://user:password@host/db → postgresql://***@host/db
        protocol, rest = db_url.split("://", 1)
        if "@" in rest:
            creds, host_db = rest.rsplit("@", 1)
            display_url = f"{protocol}://***@{host_db}"

    print(f"接続先: {display_url}")
    print("テーブルを作成中...")

    try:
        init_db(db_url)
        print("テーブル作成完了:")

        from sqlalchemy import inspect
        engine = get_engine(db_url)
        inspector = inspect(engine)
        for table in inspector.get_table_names():
            print(f"  - {table}")

    except Exception as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
