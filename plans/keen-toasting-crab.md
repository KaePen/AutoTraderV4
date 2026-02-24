# fix: USDJPY以外の通貨ペアで自動トレード実行可能にする

## Context

ライブエンジンは初期化時に `config.symbol`（デフォルト `"USDJPY"`）で全コンポーネントを固定し、
ランタイムでシンボルを切り替える仕組みがない。WebUIから別シンボルのauto-tradeをONにしても、
`set_symbol_auto_trade()` が symbol 引数を無視し `_enable_auto_trade` フラグのみ変更するため、
データ取得・シグナル生成・注文すべてがUSDJPYのまま動作する。

## 方針

- エンジンは**単一シンボル処理**を維持（マルチシンボルは将来課題）
- `frozen=True` の `LiveTradingConfig` は変更しない（イミュータブル設定は良い設計）
- 既存の `_enable_auto_trade` と同じパターンで `_active_symbol` インスタンス変数を管理
- `change_symbol()` メソッドでランタイムシンボル切替 + コンポーネント再初期化

---

## Phase 1: エンジンコア — `_active_symbol` 導入

**ファイル**: `autotrader/live/engine.py`

### 1-1. `__init__` に `_active_symbol` 追加（行96付近）
```python
self._active_symbol = config.symbol
```

### 1-2. `active_symbol` プロパティ追加
```python
@property
def active_symbol(self) -> str:
    return self._active_symbol
```

### 1-3. `self._config.symbol` → `self._active_symbol` に全置換（約26箇所）

主要箇所:
- 行173: `symbol_auto_trade_states`
- 行180: `symbol_demo_mode_states`
- 行267: `update_bot_config` の sizer再構築
- 行368: `start()` ログ
- 行426: `_tick_price_update` tick取得
- 行451: broadcast symbol
- 行485: ファンダメンタル
- 行814: `_consolidated_to_signal`
- 行834, 880: データ取得
- 行970, 1043: `_execute_entry`
- 行1082, 1110, 1417: キャッシュ/broadcast
- 行1316: `_handle_external_close`
- 行1367, 1377: pip計算
- 行1459, 1462: ATR/ローソク足取得
- 行1769: `_sync_positions`
- 行2123, 2194: ファンダメンタル

---

## Phase 2: `change_symbol()` メソッド実装

**ファイル**: `autotrader/live/engine.py`

```python
async def change_symbol(self, symbol: str) -> None:
    """アクティブシンボルを変更しコンポーネントを再初期化"""
```

処理フロー:
1. ティック監視キャンセル（`_tick_optimizer.cancel_monitoring()`）
2. `self._active_symbol = symbol`
3. PositionSizer 再構築: `_build_sizer_config(self._bot.config, symbol)`
4. TickEntryOptimizer 再構築: 新インスタンスを symbol で生成
5. MT5TradeExecutor._symbol 更新（注文は signal.symbol を使うが、デフォルト値更新）
6. キャッシュリセット: `_last_signal`, `_last_analysis`, `_last_tick_data` 等
7. エンジン実行中なら: 過去データ再読込 + ポジション同期

---

## Phase 3: `set_symbol_auto_trade()` 修正

**ファイル**: `autotrader/live/engine.py`（行192-206）

- `async` 化
- シンボルが現在と異なる場合 → `await self.change_symbol(symbol)` を呼ぶ

**影響**: 呼び出し元の `trading.py`（行310）に `await` 追加

---

## Phase 4: WebUI ルーター修正

**ファイル**: `autotrader/web/routers/trading.py`

### 4-1. `toggle_symbol_auto_trade`（行310）
- `engine.set_symbol_auto_trade(symbol, enable)` → `await engine.set_symbol_auto_trade(symbol, enable)`

### 4-2. シンボル切替エンドポイント新設
```python
@router.post("/switch-symbol")
async def switch_symbol(symbol: str, engine=Depends(get_live_engine)):
    await engine.change_symbol(symbol)
```

### 4-3. `switch_account`（行464付近）
- 新エンジン作成時にリクエストの symbol を使えるよう修正（環境変数フォールバック維持）

---

## Phase 5: テスト

**ファイル**: `tests/unit/live/test_engine.py`

| テスト | 内容 |
|--------|------|
| `test_change_symbol` | `active_symbol` が変更されること |
| `test_change_symbol_sizer_rebuild` | pip_value が新シンボル用に更新 |
| `test_change_symbol_tick_optimizer` | TickEntryOptimizer が新シンボルで再作成 |
| `test_change_symbol_cache_reset` | last_signal 等がリセット |
| `test_set_symbol_auto_trade_changes_symbol` | 異なるシンボルで change_symbol が呼ばれる |
| `test_set_symbol_auto_trade_same_symbol` | 同一シンボルでは change_symbol 不要 |

**ファイル**: `tests/unit/web/test_trading.py`
| テスト | 内容 |
|--------|------|
| `test_switch_symbol_endpoint` | POST `/switch-symbol` の成功確認 |

---

## 変更ファイル一覧

| ファイル | 変更内容 |
|---------|---------|
| `autotrader/live/engine.py` | `_active_symbol` + `change_symbol()` + 26箇所置換 + `set_symbol_auto_trade` async化 |
| `autotrader/web/routers/trading.py` | エンドポイント修正・追加 |
| `tests/unit/live/test_engine.py` | 新テスト6件 |
| `tests/unit/web/test_trading.py` | 新テスト1件 |

## リスクと対策

- **旧シンボルのオープンポジション**: `_manage_positions()` は `get_open_positions_async(None)` で全シンボルを管理するため問題なし
- **非同期競合**: `change_symbol()` 実行中にメインループが動く → キャッシュリセットとシンボル更新を先に行い、重い処理（データ再読込）は後で実行
- **未定義シンボル**: `get_preset()` がデフォルト値を返す + MT5のシンボル存在チェックを追加

## 検証方法

1. テスト実行: `pytest tests/unit/live/test_engine.py tests/unit/web/test_trading.py -v`
2. 手動検証: WebUIでEURUSD等に切り替え → ティック取得・シグナル生成・注文が新シンボルで動作確認
