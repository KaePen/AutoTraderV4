# AutoTraderV4 引継ぎレポート (2026-04-29)

## 1. 今セッションの成果

### 1.1 EDGE_DECAY BT-ライブ乖離の発見と修正

**根本原因**: `PositionManager.evaluate()` の呼び出し頻度がBTとライブで3600倍異なっていた。

- BT: H1足ごとに1回（1時間に1回）→ `bars_held=5` まで **5時間**
- ライブ: 毎秒呼び出し（`check_interval_sec=1.0`）→ `bars_held=5` まで **5秒**

`edge_decay_exit_min_bars=5` の判定が、ライブでは5秒で発動 → 早期損切り → 再エントリーの連鎖が発生。
BTでは5時間保持 → SL_HIT（利確方向）で全勝。この差がPnL方向の逆転を引き起こしていた。

**修正内容**:
- **PR #837**: `edge_decay_exit_min_minutes` を追加し、`entry_time` からの経過時間で判定するよう統一
- **PR #838**: パラメータスイープで `min_minutes=15` が最適と判明（PF 4.45, WR 82.3%）

### 1.2 パラメータスイープ結果 (USDJPY 2026/1-4)

| min_minutes | trades | PnL | WR | PF |
|---|---|---|---|---|
| 0 (bars_held) | 75 | +74,024 | 80.0% | 4.19 |
| **15** | **79** | **+78,172** | **82.3%** | **4.45** |
| 30 | 83 | +69,178 | 78.3% | 2.68 |
| 60 | 67 | +34,341 | 74.6% | 1.75 |
| 300 | 60 | +18,953 | 76.7% | 1.48 |

### 1.3 ライブトレード分析 (49件, 4/1-4/23)

| Exit Reason | 件数 | PnL | 勝率 |
|---|---|---|---|
| **SL_HIT** | **15件** | **-121,741円** | **20%** |
| EDGE_DECAY | 30件 | +20,796円 | 60% |
| STAGNATION | 3件 | -9,617円 | 33% |

- **負けSL_HIT 12件は全てBUY** — 4/21-23の円高局面で逆張り
- エントリースコアは勝ちと変わらない（16.5 vs 16.8）→ スコアでは判別不可
- SL=20pips × 大ロット(0.4-0.8) = 1件あたり-8,000〜-16,400円の壊滅的損失

### 1.4 マルチペアブロッキング仮説の棄却

- `global_max_positions=4` は4/20-23で一度も到達せず → 主因ではなかった
- 実際のブロック要因は `max_same_direction_jpy=3`（JPYペア同方向制限）
- 影響は4日間で2トレード分のみ（構造的限界、単体BTでは再現不可）

### 1.5 データパイプライン整備

**ディレクトリ構造の整理** (全8ペア):
```
{SYMBOL}/
├── raw/                    ← 生データ（MT5からの取得物）
│   ├── ohlcv/              BT使用8TFのCSVのみ (M1,M5,M15,M30,H1,H4,H8,D1)
│   └── ticks/              ティックParquet
└── monthly_cache/          ← BTインプット（インジケータ計算済み）
    ├── {TF}/               月別Parquet (YYYY-MM.parquet)
    ├── ticks/              月別ティック
    └── manifest.json
```

**コード修正**:
- `data_pipeline.py`: CSV検索パスに `raw/ohlcv` を最優先で追加
- `runner.py`: `chart_dir` 解決を `raw/ohlcv → chart/ → data_dir` の順に変更
- `scripts/run_pipeline_windows.py`: Windows並列実行スクリプト新規作成
- `scripts/fetch_mt5_all_data.py`: M1/M5の月単位チャンク取得対応 (PR #839)

**データ状態**:
- OHLCV: 8ペア × 8TF × 2010-2026/04/28 結合済み
- ティック: 8ペア × 2025/03-2026/04 Parquet
- インジケータキャッシュ: USDJPYのみ完了、残り7ペアはWindows側で実行中

---

## 2. BT-リアル乖離 全原因一覧 (10件)

| # | 原因 | 影響 | 状態 |
|---|------|------|------|
| 1 | PrecomputeEngine カラム名不一致 | 致命的 | ✅ PR#832 |
| 2 | HTFアライメントデータ未設定 | 致命的 | ✅ PR#832 |
| 3 | consensus_threshold BT:18→Live:14 | 大 | ✅ ConfigLoader使用で解消 |
| 4 | PM edge_decay_exit_threshold差 | 大 | ✅ ConfigLoader使用で解消 |
| 5 | max_positions BT:2→Live:1 | 大 | ✅ from_preset()で解消 |
| 6 | tick約定 vs bar close約定 | 大 | ✅ PR#836 M1 exit精密判定 |
| 7 | TickSimエントリー最適化の逆効果 | 大 | ✅ PR#836 entry_optimization=False |
| 8 | エッジ検定の年初リセット | 中 | ✅ PR#836 sequential実行 |
| 9 | EDGE_DECAY bars_held呼び出し頻度差 | 致命的 | ✅ PR#837 時間ベース判定 |
| 10 | JPY同方向制限+max_positions連鎖 | 小 | ⚠️ 構造的限界 |

---

## 3. 現在のBT推奨実行方法

```python
from autotrader.backtest.runner import BacktestConfig, BacktestRunner
from autotrader.backtest.tick_simulator import TickSimConfig
from autotrader.config.config_loader import ConfigLoader

loader = ConfigLoader()
bot_config, pm_config = loader.load_live_config()

tick_cfg = TickSimConfig(enabled=True)
bt = BacktestConfig.from_preset("USDJPY", tick_sim_config=tick_cfg)

runner = BacktestRunner(config=bt, verbose=False)
result = runner.run_unified(
    start_year=2025, end_year=2026,
    config=bot_config, pm_config=pm_config,
    sequential=True,  # bot状態年間引き継ぎ有効
)
```

**重要な設定**:
- `edge_decay_exit_min_minutes: 15` (trading_defaults.yaml)
- `TickSimConfig(enabled=True)` でM1精密exit有効
- `sequential=True` でbot状態（edge validator等）の年間引き継ぎ

---

## 4. 次のステップ（優先順）

### 4.1 インジケータキャッシュ完了確認
Windows側で `python scripts/run_pipeline_windows.py` 実行中。
全8ペア完了後、バックテスト実行可能。

### 4.2 ティックベースでのボット改善
ライブデータ分析から判明した課題:

1. **SL_HIT損失の巨大さ** — 負けSL_HIT平均SL=21.7pips, 平均損失-10,000円超
   - 勝ちSL_HIT平均SL=12.1pips → SLが広いほど負ける傾向
   - SL距離に応じたロット調整、または SLキャップの検討

2. **下落トレンドでの逆張りBUY繰り返し** — 負けSL_HIT 12件全てBUY (4/21-23円高局面)
   - コンセンサススコアはトレンド方向を反映できていない
   - トレンドフィルター強化 or レジーム連動のエントリー抑制

3. **EDGE_DECAY min_minutes=15の本番検証** — BTでは最適だがライブで同等に機能するか確認必要

### 4.3 Windows開発環境移行
- WSL→Windowsへの移行準備中
- `powershell.exe -Command` でWSLからWindows実行可能
- バックテストキューランナーはWindows側で実行推奨

---

## 5. 関連PR一覧

| PR | 内容 | 状態 |
|---|---|---|
| #832 | PrecomputeEngine + HTFアライメント修正 | ✅ merged |
| #835 | TickSim M1 DatetimeIndex修正 | ✅ merged |
| #836 | M1精密exit + エントリー最適化OFF + 状態累積 | ✅ merged |
| #837 | EDGE_DECAY時間ベース判定 | ✅ merged |
| #838 | edge_decay_exit_min_minutes最適化 (300→15) | ✅ merged |
| #839 | M1/M5 OHLCV月単位チャンク取得 | ✅ merged |
| be8688b | データパス raw/ohlcv対応 + Windowsスクリプト | ✅ pushed |

---

## 6. ファイル構成メモ

### 設定ファイル
- `config/trading_defaults.yaml` — PM設定（edge_decay_exit_min_minutes=15等）
- `config/symbol_presets.yaml` — 通貨ペア別設定（SL/TP, リスク, フィルタ）
- `config/symbol_overrides.yaml` — ポートフォリオ制約（max_same_direction_jpy=3等）

### 主要コード
- `autotrader/decision/unified/risk/position_manager.py` — EDGE_DECAY判定ロジック
- `autotrader/backtest/runner.py` — BT実行エンジン（chart_dir解決含む）
- `autotrader/backtest/data_pipeline.py` — OHLCV/ティックパイプライン
- `autotrader/live/engine.py` — ライブエンジン（check_interval_sec=1.0）

### データ
- `D:\Projects\AutoTraderV4_data\data\` — Windows側データ
- `/home/yamas/projects/AutoTraderV4_data/data/` — Linux側データ（同一内容）

### ライブトレードDB
- `tmp/autotrader.db` — 49件のライブトレード記録（trades テーブル）
