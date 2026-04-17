# 外部BTフレームワークによる独立検証

## Context

独自バックテストシミュレーター（`backtest/simulator.py`）の信頼性に疑念がある。
BT結果とリアルトレード結果の乖離（前回調査で13要因特定）が解消されておらず、
シミュレーターの約定処理・P&L計算自体が正しいか独立検証したい。

**目的**: 同じシグナルを別の実行エンジンに通し、結果を比較することで
独自シミュレーターの精度を客観的に評価する。

## フレームワーク調査結果

| フレームワーク | ティック対応 | 部分決済 | 外部シグナル | スプレッド | 速度 | メンテ状況 |
|--------------|:----------:|:-------:|:----------:|:---------:|:----:|:---------:|
| **Nautilus Trader** | ◎ ネイティブ | ◎ | ◎ | ◎ bid/ask | 速い(Cython) | ◎ 活発 |
| Backtrader | △ ハック的 | ○ | ○ | × 手動 | 遅い | × 停止(2019) |
| VectorBT | × 不向き | △ Pro版のみ | ○ バーのみ | × フラット | 速い(バー) | △ 有料分岐 |
| Freqtrade | × 非対応 | ○ | × 困難 | △ 設定値 | - | ○ 活発 |
| backtesting.py | × 非対応 | × | △ | × | 速い | △ |

## 推奨: Nautilus Trader

ティックレベルFXバックテストの全要件を満たす唯一のフレームワーク。

**選定理由:**
- `QuoteTick`型でbid/ask価格をネイティブサポート → 実スプレッド再現
- ティックごとの約定判定（SL/TP到達順序が正確）
- 部分決済、トレーリングストップをネイティブサポート
- Cython/Rust製コアで高速（Backtraderの数十倍）
- 活発にメンテナンスされている（2025-2026年も頻繁にリリース）
- FX通貨ペアの正式サポート（`CurrencyPair`インストルメント定義）

## アーキテクチャ

```
┌─ AutoTraderV4（既存）─────────────────┐
│  UnifiedTradeBot.generate_signal()    │
│  → ConsolidatedSignal                 │
└──────────┬────────────────────────────┘
           │ シグナル
           ▼
┌─ Nautilus Trader アダプター（新規）────┐
│  NautilusVerificationStrategy         │
│    ├─ on_quote_tick() → シグナル生成   │
│    ├─ submit_order() → Nautilus約定    │
│    ├─ on_position_changed() → PM連携  │
│    └─ 結果をTrade形式で出力           │
├───────────────────────────────────────┤
│  Nautilus BacktestEngine              │
│    ├─ ティックレベル約定シミュレーション │
│    ├─ 実bid/askスプレッド適用          │
│    ├─ スリッページモデル               │
│    └─ 正確なP&L計算                   │
└───────────────────────────────────────┘
           │
           ▼
┌─ 比較レポート ────────────────────────┐
│  独自BT結果 vs Nautilus結果            │
│  ├─ トレード数の一致                   │
│  ├─ エントリー/エグジット価格の差異     │
│  ├─ P&L差異                          │
│  └─ 勝率・シャープレシオの比較         │
└───────────────────────────────────────┘
```

## 実装計画

### Phase 0: 既存スクリプトの退避

**作業内容:**
- `scripts/` 配下の全16ファイルを `scripts/old/` に移動
- 移動対象: run_backtest.py, run_multi_pair_backtest.py, backtest_queue_runner.py, backtest_web_ui.py, fetch_mt5_data.py, stress_test系, analyze系, 他全て

### Phase 1: 新規スクリプト作成（1ファイルに統合）

**新規ファイル: `scripts/nautilus_backtest.py`**

このスクリプト1つで以下を全て実行:
1. MT5からティックデータ取得（`copy_ticks_range()`）→ Parquet保存
2. Nautilus Trader エンジンのセットアップ
3. AutoTraderV4シグナル生成パイプラインとの接続
4. バックテスト実行
5. 結果出力・比較レポート

```bash
# 使い方
# ティックデータ取得
uv run python scripts/nautilus_backtest.py fetch --symbol USDJPY --start 2026-01-01 --end 2026-03-31

# バックテスト実行
uv run python scripts/nautilus_backtest.py run --symbol USDJPY --start 2026-01-01 --end 2026-03-31

# 結果比較（旧BTとの比較用）
uv run python scripts/nautilus_backtest.py compare --native results/xxx --nautilus results/yyy
```

### Phase 2: データ基盤（ティックデータ取得）

スクリプト内の`fetch`サブコマンドで実装:

1. MT5 Python APIの`copy_ticks_range()`でティック履歴をエクスポート
   - フィールド: time_msc, bid, ask, volume, flags
   - 保存形式: Parquet（容量効率、高速読み込み）
2. 通貨ペアごとにデータ保存: `data/{SYMBOL}/ticks_{YYYY}.parquet`
3. 取得期間: まず直近3ヶ月（2026-01〜2026-03）で検証

**データ量の目安:**
- USDJPY 1ヶ月: 約200-500万ティック（Parquetで50-100MB）
- 8通貨ペア × 3ヶ月: 約1.5-3GB

### Phase 3: Nautilus Trader セットアップ & ストラテジー実装

スクリプト内の`run`サブコマンドで実装:

1. `nautilus_trader`パッケージのインストール（`pip install nautilus_trader`）
2. FXインストルメント定義（USDJPY等の通貨ペア設定）
3. ティックデータをNautilus形式（`QuoteTick`）に変換するローダー
4. `BacktestEngine`の初期設定（手数料モデル、約定モデル）

**設計方針: 2段階検証**

#### 段階A: シンプルSL/TP検証（まず信頼性の基準を確立）
- AutoTraderV4のシグナル（方向、SL、TP、ロット）のみを使用
- エグジットはSL/TPヒットのみ（PositionManager不使用）
- **目的**: 最もシンプルな約定ロジックで両者の一致を確認

```python
class SimpleVerificationStrategy(Strategy):
    def on_quote_tick(self, tick: QuoteTick):
        if self._is_bar_close(tick):
            candle = self._aggregate_to_candle(tick)
            signal = self.trade_bot.generate_signal(tick.ts_init, candle)
            if signal.direction in (BUY, SELL):
                self._submit_entry_with_sl_tp(signal, tick)
```

#### 段階B: フルPM検証（段階A一致後に拡張）
- PositionManager連携を追加
- 部分決済、トレーリング、ステグネーション等を順次追加

### Phase 4: 結果比較・レポート

スクリプト内の`compare`サブコマンドで実装:

| 指標 | 許容差異 | 意味 |
|------|---------|------|
| トレード数 | 完全一致 | シグナル→エントリーの判定が同じか |
| エントリー価格 | ±0.5pips | 約定タイミングの差 |
| エグジット価格 | ±1.0pips | SL/TP判定の差 |
| 総P&L | ±5% | システム全体の信頼性 |
| 勝率 | ±2% | 統計的一致 |

### Phase 5: 判断・次のアクション

- **一致（差異5%以内）**: 独自シミュレーターは信頼できる → BT/ライブ乖離は13の外部要因が原因
- **不一致（差異5%超）**: シミュレーターに問題あり → Nautilus結果を正として修正箇所を特定

## 依存パッケージ

```toml
# pyproject.toml に追加
[project.optional-dependencies]
verification = [
    "nautilus_trader>=1.200.0",
]
```

## リスクと対策

| リスク | 対策 |
|--------|------|
| Nautilusの学習コスト | 段階Aで最小限の実装から開始 |
| ティックデータの容量（数GB） | Parquet圧縮、3ヶ月限定で開始 |
| シグナル生成にOHLCバーが必要 | ティック→バー集約ロジックを実装 |
| 8TF同時分析のメモリ | 最初はUSDJPY単一ペアで検証 |
