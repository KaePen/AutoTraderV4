# live/engine.py 分割リファクタリング計画

## Context

`autotrader/live/engine.py` (2,920行) に `LiveTradingEngine` 1クラス・67メソッドが集中。
6つの責務が混在し、保守性・テスタビリティ・拡張性に課題がある。
Facadeパターンで5つのサービスに分割し、公開APIは維持する。

## 分割後の構成

```
autotrader/live/
  engine.py                    # Facade (~400行) ← 2,920行から縮小
  market_data_service.py       # マーケットデータ取得・指標計算 (~250行)
  trade_executor_service.py    # エントリー実行・ポジション登録 (~400行)
  position_sync_service.py     # ポジション管理・同期・DB永続化 (~600行)
  fundamental_service.py       # ファンダメンタル統合 (~450行)
  broadcast_service.py         # UIペイロード構築・配信 (~200行)
  config.py                    # (既存・変更なし)
  tick_entry_optimizer.py      # (既存・変更なし)
  tick_entry_config.py         # (既存・変更なし)
  engine_manager.py            # (既存・軽微修正)
```

## 各モジュールの責務とメソッド割り当て

### 1. MarketDataService (~250行)

MT5からローソク足取得、テクニカル指標計算。

| 元メソッド | 新メソッド名 |
|-----------|------------|
| `_load_historical_data` | `load_historical_data()` |
| `_update_market_data` | `update_market_data()` |
| `_calc_indicators` | `calc_indicators()` |
| `_tick_price_update` | `tick_price_update()` |
| `get_candles` | `get_candles()` |
| `get_candles_before` | `get_candles_before()` |
| `get_indicators` | `get_indicators()` |
| `_extract_indicators` | `extract_indicators()` |

### 2. TradeExecutorService (~400行)

シグナル→ロット計算→MT5発注→PM登録→DB記録。

| 元メソッド | 新メソッド名 |
|-----------|------------|
| `_execute_entry` | `execute_entry()` |
| `_register_new_position` | `register_new_position()` |
| `_should_use_tick_optimizer` | `should_use_tick_optimizer()` |
| `_write_entry_to_db` | `write_entry_to_db()` |
| `_consolidated_to_signal` | `consolidated_to_signal()` |
| `_build_sizer_config` | `build_sizer_config()` (モジュールレベル関数) |
| `_get_pip_size` | `get_pip_size()` (モジュールレベル関数) |
| `_get_pip_value` | `get_pip_value()` (モジュールレベル関数) |

### 3. PositionSyncService (~600行)

MT5ポジション監視・PM評価・管理状態永続化・決済DB記録。

| 元メソッド | 新メソッド名 |
|-----------|------------|
| `_manage_positions` | `manage_positions()` |
| `_execute_action` | `execute_action()` |
| `_sync_positions` | `sync_positions()` |
| `sync_positions_on_toggle` | `sync_positions_on_toggle()` |
| `_handle_external_close` | `handle_external_close()` |
| `_write_close_to_db` | `write_close_to_db()` |
| `_close_ghost_db_records` | `close_ghost_db_records()` |
| `_fetch_ghost_records` | `fetch_ghost_records()` |
| `_apply_ghost_updates` | `apply_ghost_updates()` |
| `_restore_open_trades_from_db` | `restore_open_trades_from_db()` |
| `_load_position_states` | `load_position_states()` |
| `_save_position_state` | `save_position_state()` |
| `_delete_position_state` | `delete_position_state()` |
| `_cleanup_stale_states` | `cleanup_stale_states()` |

### 4. FundamentalService (~450行)

ファンダメンタルデータ収集・ニュース分析・センチメント。

| 元メソッド | 新メソッド名 |
|-----------|------------|
| `_init_fundamental` | `init_fundamental()` |
| `_init_calendar_only` | `init_calendar_only()` |
| `_start_fundamental_tasks` | `start_tasks()` |
| `_stop_fundamental_tasks` | `stop_tasks()` |
| `get_news_for_symbol` | `get_news_for_symbol()` |
| `_on_rss_news` | `on_rss_news()` |
| `_blend_news_sentiment` | `blend_news_sentiment()` |
| `_run_morning_update` | `run_morning_update()` |
| `_handle_post_event_analysis` | `handle_post_event_analysis()` |
| `_tick()` 内インライン処理 | `get_fundamental_context()` (新規抽出) |

### 5. BroadcastService (~200行)

WebSocket向けUIペイロード構築・配信。`EngineState` dataclassで状態を受け取る。

| 元メソッド | 新メソッド名 |
|-----------|------------|
| `_broadcast_tick_update` | `broadcast_tick_update()` |
| `_build_tick_payload` | `build_tick_payload()` |

### 6. engine.py (Facade, ~400行)

サブサービスのDI組み立て、公開API提供、`_tick()`オーケストレーション。

残すもの: `__init__`, 全プロパティ(14個), `start/stop`, `_main_loop`, `_tick`, `change_symbol`, 設定更新, public API委譲

## 依存関係

```
         LiveTradingEngine (Facade)
              │ owns all
    ┌─────────┼─────────────────────┐
    │         │         │           │
    ▼         ▼         ▼           ▼
 Market    Trade    Position   Fundamental   Broadcast
  Data    Executor   Sync      Service       Service
 Service  Service   Service

サービス間の直接依存なし（Facade経由のみ）
共有インスタンス: MT5DataProvider, MT5TradeExecutor, PositionManager, UnifiedTradeBot
```

## web/ 側の修正（3箇所のみ）

| ファイル | 変更 |
|---------|------|
| `web/routers/fundamental.py` | `engine._fundamental_collector` → `engine.fundamental_collector` |
| `web/routers/signals.py` | `engine._config.symbol` → `engine.config_symbol` |
| `live/engine_manager.py` | `_fundamental_collector`, `_rss_collector` → publicプロパティ経由 |

## 実装順序（一括PRだが内部は段階的に回帰確認）

1. **FundamentalService抽出** — 最も独立性が高い
2. **MarketDataService抽出** — `_tick()` 先頭、下流依存なし
3. **BroadcastService抽出** — `_tick()` 末尾、上流依存なし
4. **TradeExecutorService + PositionSyncService抽出** — `_pm` 共有のため同時
5. **Facade化完了** — engine.pyスリム化、web/側3箇所修正
6. **テスト修正** — パッチパス変更対応

## 修正対象ファイル一覧

- `autotrader/live/engine.py` (主対象・大幅縮小)
- `autotrader/live/market_data_service.py` (新規)
- `autotrader/live/trade_executor_service.py` (新規)
- `autotrader/live/position_sync_service.py` (新規)
- `autotrader/live/fundamental_service.py` (新規)
- `autotrader/live/broadcast_service.py` (新規)
- `autotrader/live/engine_manager.py` (軽微修正)
- `autotrader/web/routers/fundamental.py` (1行修正)
- `autotrader/web/routers/signals.py` (1行修正)
- `tests/unit/live/test_engine*.py` (パッチパス修正)

## リスクと対策

| リスク | 対策 |
|-------|------|
| 共有ミュータブル状態（_pm, _bot） | Facade `_tick()` で呼び出し順序保証 |
| `_manage_positions` 200行で巨大 | Service内で `_build_cache_entry` + `_evaluate_and_act` に分割 |
| `_tick()` 内ファンダメンタルインライン処理 | `FundamentalService.get_fundamental_context()` に抽出 |
| 遅延importの整理 | サービスコンストラクタでimportに変更 |
| テストのパッチパス変更 | 全テストのパッチパスを新モジュールに更新 |

## 検証方法

1. 各ステップ後に `pytest tests/unit/live/` 実行で回帰確認
2. 全ステップ完了後に `pytest tests/` でフルテスト実行
3. `ruff check autotrader/live/` でリンティング確認
4. web/側の修正は `pytest tests/unit/web/` で確認
