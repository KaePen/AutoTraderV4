# MT5自動再接続 & データリトライ修正計画

## 問題

デモモードボタンを押さないとライブアナリティクスが表示されない。

### 根本原因

1. **`_tick()`に再接続ロジックがない**: `start()`でMT5接続に失敗すると`_conn.connected=False`のまま永久にスキップされる
2. **データ更新間隔が60秒**: 接続成功後も`_data_update_interval_sec=60.0`でスロットリングされ、初回データ取得が遅延する
3. **market_data空時のリトライなし**: `_bot._market_data`が空のままだと`generate_signal()`が呼ばれず、ダミーの`scores={}`シグナルが返され続ける
4. **デモモードボタンが副作用的に修正**: `toggle_symbol_demo_mode()`が`reset_data_update_timer()`を呼ぶため、結果的にデータ更新がトリガーされて表示が開始される

## 修正内容

### Change 1: `__init__()`に再接続追跡フィールド追加

**ファイル**: `src/autotrader/live/engine.py`

`__init__()`の既存フィールド付近に以下を追加:
```python
self._last_reconnect_attempt: datetime | None = None
self._reconnect_interval_sec: float = 10.0
```

### Change 2: `_tick()`に自動再接続ブロック追加

**ファイル**: `src/autotrader/live/engine.py`

現在の`else: logger.debug("MT5未接続: 市場データ更新スキップ")`を以下に置換:

```python
else:
    need_reconnect = (
        self._last_reconnect_attempt is None
        or (now - self._last_reconnect_attempt)
        .total_seconds()
        >= self._reconnect_interval_sec
    )
    if need_reconnect:
        self._last_reconnect_attempt = now
        logger.info("MT5再接続試行中...")
        try:
            await self._conn.connect()
            logger.info("MT5再接続成功")
            await self._load_historical_data()
            self._account_info = (
                await self._data_provider
                .get_account_info()
            )
            self._last_data_update = now
        except Exception as e:
            logger.debug(
                "MT5再接続失敗: %s", e,
            )
    else:
        logger.debug(
            "MT5未接続: 再接続待機中",
        )
```

### Change 3: market_data空時に即時リトライ

**ファイル**: `src/autotrader/live/engine.py`

market_data空ブロック内、`self._last_tick_time = now`の前に追加:

```python
if self._conn.connected:
    self._last_data_update = None
```

これにより次のtickで即座にデータ再取得が試みられる。

### Change 4: テスト追加

**ファイル**: `tests/unit/live/test_engine.py`

4つの新テスト:
1. `test_未接続時に自動再接続を試行` - connected=Falseのtickで`connect()`が呼ばれる
2. `test_再接続スロットリング` - 10秒未満の再tick時には`connect()`が呼ばれない
3. `test_再接続成功後にデータ取得` - 再接続成功時に`_load_historical_data()`が呼ばれる
4. `test_market_data空時にデータ更新タイマーリセット` - connected=True+market_data空で`_last_data_update=None`になる

## 影響範囲

- `src/autotrader/live/engine.py` のみ変更
- `tests/unit/live/test_engine.py` にテスト追加
- 既存の動作に対して後方互換: 接続成功時の動作は変わらない
