# Signal DBレイヤー完全削除計画

## Context

シグナルは「その瞬間の判断」であり、過去のシグナルを再実行する用途はない。
現状SQLiteにシグナルを永続化しているが、ライブ実行時はインメモリの`engine.signal_history`が主要データソースとして機能しており、DB保存はWebダッシュボードの補助的フォールバックに過ぎない。
Signal DBレイヤーを削除し、シグナルはインメモリのみで管理する設計に簡素化する。

**注**: ドメインエンティティ `Signal`（`core/entities.py`）は削除しない。DBモデル・リポジトリ・サービスのみ対象。

## 変更ファイル一覧

### 1. `src/autotrader/adapters/database/models.py`
- `SignalRecord`クラス全体（L29-71）を削除
- `TradeRecord`から`signal_id` FKカラム（L84）を削除
- `TradeRecord`から`signal`リレーション（L100）を削除
- `TradeRecord.to_dict()`から`signal_id`を削除

### 2. `src/autotrader/adapters/database/repositories.py`
- `SignalRecord`インポートを削除
- `SignalRepository`クラス全体を削除
- `TradeRepository.create()`から`signal_id`パラメータを削除

### 3. `src/autotrader/adapters/database/__init__.py`
- `SignalRecord`, `SignalRepository`のインポートと`__all__`エントリを削除

### 4. `src/autotrader/web/services/signal_service.py`
- ファイル全体を削除

### 5. `src/autotrader/web/routers/signals.py`
- `SignalService`インポートを削除
- `get_db`依存性注入を削除
- `get_current_signals()`と`get_signal_history()`のDBフォールバック分岐を削除
- エンジン未起動時は空リストを返すように簡素化

### 6. `src/autotrader/live/engine.py`
- `SignalRecord`インポートを削除
- `_persist_signal()`メソッド全体を削除
- `_persist_signal()`の呼び出し箇所を削除
- `_persist_trade_open()`内のFK存在チェック（SignalRecord照会）を削除、`signal_id=None`に固定
- `_persist_trade_close()`内の`signal_id`による検索パスを削除/簡素化

### 7. 変更不要ファイル（確認済み）
- `src/autotrader/core/entities.py` - ドメイン`Signal`エンティティは維持
- `src/autotrader/web/schemas/responses.py` - `SignalResponse`はインメモリパスで使用、維持
- `src/autotrader/web/main.py` - signalsルーター登録は維持（`/signals/analysis`はDB不使用）
- `tests/` - signal_idはドメインエンティティのフィールドとして参照、DB層不使用
- `src/autotrader/decision/`, `backtest/`, `adapters/mt5/` - DB層不参照

## 検証

1. `pytest` 全テスト実行 → 全PASS確認
2. インポートエラーがないことを確認
