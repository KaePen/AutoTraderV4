# AutoTraderV4 アーキテクチャルール

## 最重要原則: バックテストとリアルトレードのロジック共用

トレードロジック（シグナル生成・ポジション管理・リスク制御）は**単一の実装**を持ち、
バックテストとリアルトレードの両方から同じコードを呼び出す。

### バックテストとリアルトレードの違い

トレードロジック自体は共通だが、**実行基盤**が異なる。

```
バックテスト: CSV → 前処理 → [共通トレードロジック] → CSV/メトリクス出力
                    ↑ 並列実行・最適化スキャフォールド
リアルトレード: MT5 → [共通トレードロジック] → MT5注文実行
```

- **バックテスト固有**: データ入出力（CSV読み書き）、前処理（データ変換・正規化）、
  並列実行（マルチプロセス最適化）、事前計算のバッチ実行・キャッシュ（`PrecomputeEngine`）、
  シミュレーション実行、メトリクス集計、結果出力
- **リアル固有**: MT5接続・データ取得・注文実行、ティックエントリ最適化
- **共通（変更禁止）**: `decision/`（シグナル生成）、`calculator/`（指標計算）、
  `constraint/`（ガード条件）、`core/`（エンティティ・インターフェース）

### バックテストモジュールに書いてよいもの

- データ入出力（CSV読み込み・結果書き出し）
- 前処理（データ変換・正規化・期間フィルタリング）
- 事前計算のバッチ実行・Parquetキャッシュ（指標計算ロジック自体は `calculator/` に置く）
- 並列実行・最適化スキャフォールド（マルチプロセス制御）
- バックテスト実行ループ（足ごとの繰り返し制御）
- パフォーマンス計測・メトリクス集計

### バックテストモジュールに書いてはいけないもの

- トレード判定ロジック（エントリー/エグジット条件）
- 独自のインジケータ計算（`calculator/`に置く）
- 独自のフィルタリングロジック（`constraint/`に置く）
- リアルトレードと異なる損益計算方式

### やむを得ない差異の扱い方

処理の都合上バックテスト固有の分岐が必要な場合:

1. **リアルトレード側にフラグを持たせる**（`is_backtest: bool` 等の設定値）
2. リアルトレード側からバックテストの処理を切り替え可能にする
3. バックテスト側に「リアルでは使わない特殊ロジック」を追加しない
4. 差異が生じた箇所にはコメントで理由を明記する

```python
# 良い例: 共通ロジック内で設定値による分岐
class EngineConfig:
    use_simulated_spread: bool = False  # バックテスト時True

# 悪い例: バックテスト側に独自判定ロジック
class BacktestEngine:
    def _custom_exit_logic(self):  # ← 禁止
        ...
```

## 設定の単一ソース: リアルトレード側のコンフィグを使う

トレードパラメータの設定はリアルトレード側の設定体系を**唯一の正（Single Source of Truth）**とする。
バックテスト側で独自の設定体系を作らない。

### 通貨ペア別設定

- **定義場所**: `config/symbol_presets.yaml` → `SymbolPreset`（`config/trading_params.py`）
- **取得**: `get_preset("USDJPY")` で通貨ペア固有のパラメータを取得
- **内容**: spread, SL/TP, pip_value, ロット制限, リスク設定, ポジション数上限
- バックテストでもリアルでも同じ `get_preset()` を呼び出す

### 共通トレード設定

- **ボット設定**: `UnifiedBotConfig`（`decision/unified/config.py`）
- **ポジション管理**: `PositionManagerConfig`（`decision/unified/position_manager.py`）
- **YAML読み込み**: `ConfigLoader.load_live_config()`（`config/config_loader.py`）

### バックテストでの設定の使い方

```python
# 良い例: リアル側の設定を直接使う
preset = get_preset("USDJPY")
bot_config = UnifiedBotConfig(min_consensus_score=6.5)

# 悪い例: バックテスト固有の設定クラスを作る
class BacktestTradingConfig:  # ← 禁止
    spread: float = 1.0
```

バックテスト固有の設定（データ範囲、並列数、出力先等）のみ `backtest/config.py` に置く。
トレードロジックに影響するパラメータは必ずリアル側の設定クラスを参照する。

## レイヤー構造

```
core/interfaces/    ← 抽象インターフェース（DataProvider, TradeExecutor）
core/entities.py    ← 共通エンティティ（Signal, Trade, Position, Candle）
calculator/         ← 指標計算（テクニカル・マーケット構造）
constraint/         ← トレード制約（HardGuard, SoftGuard, フィルタ）
decision/           ← シグナル生成・ポジション管理（UnifiedTradeBot等）
backtest/           ← データI/O・シミュレーション実行・メトリクス
live/               ← MT5接続・リアルタイム実行
adapters/           ← 外部サービスアダプタ（MT5, DB, Ollama）
config/             ← 設定管理
```

## 新機能追加時の判断基準

新しいロジックを追加する場合、以下の問いに答える:

1. **リアルトレードでも使うか？** → Yes なら `decision/` or `calculator/` or `constraint/` に置く
2. **データの入出力に関するものか？** → Yes なら `backtest/` or `live/` に置く
3. **外部サービスとの通信か？** → Yes なら `adapters/` に置く
4. **上記のどれにも当てはまらない** → `core/` に置く
