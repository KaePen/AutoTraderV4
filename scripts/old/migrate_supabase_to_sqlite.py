"""Supabase → SQLite マイグレーションスクリプト

Supabase（PostgreSQL）の trades テーブルを
ローカル SQLite（data/autotrader.db）に移行する。

使い方:
    uv run python scripts/migrate_supabase_to_sqlite.py

前提:
    - .env に DATABASE_URL=postgresql://... が設定されていること
    - このスクリプトは Supabase にアクセスできる環境で実行すること
    - 移行後は .env から DATABASE_URL を削除する
"""

from __future__ import annotations

import sys
from pathlib import Path

# プロジェクトルートを sys.path に追加
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from autotrader.adapters.database.models import Base, TradeRecord
from autotrader.config.settings import get_settings


def main() -> None:
    """Supabase → SQLite 移行処理"""
    settings = get_settings()
    src_url = settings.database_url

    # 接続先確認
    if src_url.startswith("sqlite"):
        print(
            "ERROR: DATABASE_URL が SQLite になっています。"
            ".env に DATABASE_URL=postgresql://... を設定してください。"
        )
        sys.exit(1)

    masked = src_url.split("@")[-1] if "@" in src_url else src_url
    print(f"移行元 (Supabase): ...@{masked}")

    # 移行先 SQLite
    dst_url = "sqlite:///data/autotrader.db"
    Path("data").mkdir(parents=True, exist_ok=True)
    print(f"移行先 (SQLite)  : {dst_url}")

    # 移行元接続
    src_engine = create_engine(src_url, pool_pre_ping=True)
    SrcSession = sessionmaker(bind=src_engine)

    # 移行先接続・テーブル作成
    dst_engine = create_engine(
        dst_url, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=dst_engine)
    DstSession = sessionmaker(bind=dst_engine)

    # 既存レコード数確認
    with DstSession() as dst_session:
        existing = dst_session.query(TradeRecord).count()
        if existing > 0:
            print(f"\n移行先に既存レコードが {existing} 件あります。")
            ans = input("上書き（スキップ）しますか？ [y/N]: ").strip().lower()
            if ans != "y":
                print("中止しました。")
                sys.exit(0)

    # レコード取得
    print("\nSupabase からレコードを取得中...")
    with SrcSession() as src_session:
        records = src_session.query(TradeRecord).all()
        # セッションから切り離してデータを保持
        data = [
            {
                "trade_id": r.trade_id,
                "ticket": r.ticket,
                "symbol": r.symbol,
                "signal_type": r.signal_type,
                "volume": r.volume,
                "entry_price": r.entry_price,
                "exit_price": r.exit_price,
                "stop_loss": r.stop_loss,
                "take_profit": r.take_profit,
                "profit_loss": r.profit_loss,
                "profit_loss_pips": r.profit_loss_pips,
                "exit_reason": r.exit_reason,
                "opened_at": r.opened_at,
                "closed_at": r.closed_at,
                "is_open": r.is_open,
            }
            for r in records
        ]

    print(f"取得件数: {len(data)} 件")

    if not data:
        print("移行対象レコードがありません。完了。")
        return

    # SQLite に挿入（trade_id で重複スキップ）
    inserted = 0
    skipped = 0
    with DstSession() as dst_session:
        for row in data:
            exists = (
                dst_session.query(TradeRecord)
                .filter(TradeRecord.trade_id == row["trade_id"])
                .first()
            )
            if exists:
                skipped += 1
                continue
            dst_session.add(TradeRecord(**row))
            inserted += 1
        dst_session.commit()

    print(f"\n移行完了: 挿入 {inserted} 件 / スキップ {skipped} 件")
    print(f"SQLite ファイル: {ROOT / 'data' / 'autotrader.db'}")
    print(
        "\n次の手順:"
        "\n  1. .env から DATABASE_URL 行を削除する"
        "\n  2. アプリを再起動して DB初期化が sqlite になることを確認"
        "\n  3. 必要に応じて data/autotrader.db を VPS にコピーする"
    )


if __name__ == "__main__":
    main()
