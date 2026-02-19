# Supabase DB移行計画

## Context

WebUIのDB基盤をPostgreSQL（未構築）からSupabaseに変更する。
現状はSQLite（`data/autotrader.db`）がデフォルトで動作しており、
PostgreSQL依存（asyncpg, psycopg2-binary）はpyproject.tomlに宣言のみで
実コードでは一切使われていない。

利用頻度が低くシンプルな運用を重視するため、
**Supabase Python SDK は使わず、SupabaseのPostgreSQL接続URLを
既存SQLAlchemyに渡すだけ**の最小変更アプローチを採用する。

## 現状の DB アーキテクチャ

### テーブル構成（既存 SQLAlchemy モデル）
| テーブル | 用途 | 書き込み元 |
|---------|------|----------|
| `signals` | シグナル記録 | *(現在未使用、将来LiveEngine)* |
| `trades` | トレード記録 | *(現在未使用、将来LiveEngine)* |
| `backtest_results` | CLIバックテスト結果 | BacktestRunner（CLI） |
| `audit_logs` | 操作ログ | *(現在未使用)* |

### 接続管理（2箇所に重複）
- `src/autotrader/adapters/database/connection.py` — CLI/汎用用
- `src/autotrader/web/dependencies.py` — WebAPI FastAPI Depends用

### 現在の問題点
- `asyncpg`, `psycopg2-binary` が依存に宣言されているが未使用
- `check_same_thread=False` などSQLite固有の設定が残存
- `asyncpg` は非同期ドライバだが同期SQLAlchemyのみ使用（不整合）

## 移行アプローチ

### 接続方式: SQLAlchemy + psycopg2 (Supabase PostgreSQL URL)

Supabaseの接続URL形式:
```
postgresql://postgres:[PASSWORD]@db.[PROJECT_REF].supabase.co:5432/postgres
```

この `DATABASE_URL` 環境変数を設定するだけで既存SQLAlchemyがそのまま動作する。
テーブルはアプリ起動時の `Base.metadata.create_all()` でSupabase上に自動作成される。

## 変更ファイル

### 1. `pyproject.toml`
- `asyncpg>=0.29.0` を削除（同期SQLAlchemyでは不要）
- `psycopg2-binary>=2.9.0` は維持（PostgreSQL接続ドライバとして使用）

### 2. `src/autotrader/adapters/database/connection.py`
- SQLite固有の `connect_args={"check_same_thread": False}` を
  URLがSQLiteの場合のみ適用するよう条件分岐に修正

### 3. `src/autotrader/web/dependencies.py`
- 同上、`check_same_thread` をSQLite限定の条件分岐に修正

### 4. `.env`（新規作成、.gitignore対象）
```env
DATABASE_URL=postgresql://postgres:[PASSWORD]@db.[PROJECT_REF].supabase.co:5432/postgres
```

### 5. `.env.example`（新規作成、git管理）
```env
DATABASE_URL=postgresql://postgres:PASSWORD@db.PROJECT_REF.supabase.co:5432/postgres
```

### 6. `.gitignore`
- `.env` が未記載であれば追加

## Supabase側の準備（ユーザー提供情報）

ユーザーが用意するもの：
- Supabase接続文字列（PostgreSQL URL形式）
- `.env` ファイルに設定するだけでOK

テーブルは `create_all()` で自動作成されるため、
Supabase Dashboard でのSQL事前実行は不要。

## スコープ外（対応しない）

- LiveEngine からのリアルタイムDB書き込み（別タスク）
- Supabase Realtime / Row Level Security
- Supabase Python SDK の導入
- Alembicマイグレーション導入

## 検証方法

1. `DATABASE_URL` を設定して `uvicorn autotrader.web.main:app` 起動
2. 起動ログにDB接続エラーが出ないことを確認
3. Supabase Dashboard → Table Editor で4テーブルが作成されることを確認
4. `GET /api/v1/health` が `200 OK` を返すことを確認
5. `pytest` で既存テストがパスすることを確認（テストはSQLiteを使用）
