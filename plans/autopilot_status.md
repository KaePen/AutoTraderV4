# Autopilot 中間報告 (2026-04-29)

## 完了した作業

### 1. Windows 環境修復
- ✅ EDGE_DECAY 時間ベース判定 (PR #837/#838) を復元
  - `wsl to windows` コミット (225b68f) で巻き戻されていた `edge_decay_exit_min_minutes=15` を `git checkout e55cc30` で再適用
- ✅ インジケータキャッシュ pipeline エラー解消
  - 全8ペアの D1 CSV が 2024.02 以降 9列形式 (TIME 混入) に汚染されていた問題を `scripts/fix_daily_csv.py` で除去
  - 全8ペアの D1 monthly_cache を再構築
  - raw/ticks 月別parquet を monthly_cache/ticks にコピー
  - 全8ペアの indicator_cache (M1+他TF) をプリビルド完了 → 以降 BT は data load 0.4 秒で再開可能

### 2. マルチペアBT基盤の autotrader/backtest/ 移植
旧 `scripts/run_multi_pair_backtest.py` (PR #811 で削除) を再実装:
- ✅ `autotrader/backtest/multi_pair_runner.py` — 時系列インターリーブ + 共有ポートフォリオ (`MultiPairConfig`, `PortfolioState`, `run_multi_pair_period`)
- ✅ `autotrader/backtest/single_pair_runner.py` — 単独ペア BT 薄ラッパ
- ✅ `autotrader/backtest/analysis.py` — トレード分析 + A/B サマリ比較ツール
- ✅ `autotrader/backtest/cli.py` に `run-single`, `run-portfolio` サブコマンド追加
- ✅ `--htf-counter-block`, `--htf-counter-threshold` CLI フラグで A/B 検証可

### 3. BT 結果

#### 単独ペア検証 (USDJPY 2025-06)
| trades | WR | PF | Sharpe | Max DD | Net |
|---|---|---|---|---|---|
| 37 | 81.1% | 2.86 | 8.42 | 0.84% | +¥31,591 |

#### 3ペア×1ヶ月 (USDJPY+EURJPY+GBPJPY, 2025-06)
| trades | WR | PF | Max DD | Net |
|---|---|---|---|---|
| 163 | 79.8% | 3.78 | 0.87% | +¥173,812 (+17.4%) |

#### 8ペア×Q3 2025 (3ヶ月)
| trades | WR | PF | Max DD | Net |
|---|---|---|---|---|
| 714 | 73.9% | 2.70 | 2.69% | **+¥824,627 (+82.5%)** |

ペア別: EURUSD最強 (WR 86.7%, +¥176K), CADJPY最弱 (WR 61.7%, +¥17K)。月次PnL 全月プラス。

ポートフォリオ制約発動: per_pair_max=6786回, global_max=539回, max_same_direction_jpy=251回。

### 4. 改善試作: HTF トレンド逆行ハードブロック
- ✅ `UnifiedBotConfig.htf_counter_block_enabled` / `htf_counter_block_threshold` 新規追加
- ✅ `pipeline.py` Step3ConsensusStep に判定ロジック実装
- ✅ trading_defaults.yaml に追加 (デフォルト off)
- ✅ A/B 検証: 8ペア×Q3 (threshold=0.3) → **発動ゼロ・効果なし**
  - Q3期間中、|htf_alignment| が 0.3 を超える逆行シナリオは存在せず

## 重大課題: BT-ライブ乖離が依然存在

ライブDB の損失原因 (4/21-23 の SL_HIT 巨大損失) を BT で再現しようとしたが、
**同期間で完全に逆の結果**:

| 項目 | BT (8ペア × 2026-04) | ライブ DB (同期間) |
|---|---|---|
| trades | 92 | 49 |
| WR | 75.0% | 44.9% |
| PF | 10.02 | 0.30 |
| Max DD | 0.24% | -11.3% |
| Net PnL | +¥118,885 | **-¥112,942** |

handover.md の「PR #837/#838 で BT-ライブ乖離 10件すべて解消」という主張に対し、
2026-04 マルチペアでは依然として乖離があり、改善活動の判断ができない状態。

考えられる原因:
- 単独ペアのみで検証されており、マルチペア環境固有のずれ
- ConfigLoader 経由の bot_config 読み込みパスがライブと違う
- ティックデータ処理 vs 実 MT5 約定フローの違い (TickSim 未統合等)
- ライブ側に未記録の停止/接続障害

## 次にやるべきこと (ユーザー判断推奨)

### 優先度高
1. **BT-ライブ乖離の追加調査**
   - BT trades.csv を出力させて、ライブの 49 件と trade-by-trade 比較
   - 同じ時刻・同じシグナル方向・同じスコアでBT/ライブ両方が同じ判断をしているかを検証
   - 設定差分監査: `ConfigLoader.load_preset_config()` の結果を BT/ライブ間で diff

2. **CADJPY ペアの弱さの原因調査**
   - 全BT結果で CADJPY が一貫して最弱 (WR 56-62%)
   - シンボル overrides 見直し or 除外検討

### 優先度中
3. **HTF block 改善案の閾値再調整**
   - threshold=0.1 や 0.05 への引き下げで効果が出るか検証
   - またはペア単位 D1 ma_alignment を直接条件に使う

4. **8ペア×フル年 (2025) BT** で長期統計の堅牢性確認 (~2.5時間)

### 優先度低
5. **CLI に prewarm-cache サブコマンド追加** (今後の効率化)

## 成果物 (新規/変更ファイル)

```
autotrader/backtest/
  multi_pair_runner.py    # 新規 (≈540行)
  single_pair_runner.py   # 新規
  analysis.py             # 新規
  cli.py                  # subcommands 追加

config/trading_defaults.yaml
  htf_counter_block_enabled: false
  htf_counter_block_threshold: 0.3

autotrader/decision/unified/
  config.py               # htf_counter_block_* 追加
  pipeline_pkg/pipeline.py # Step3 にHTFブロック実装
  risk/position_manager.py # PR#837 復元
```

## 採番された BT 結果 (tmp/)

- `mp_smoke.json` - 3ペア×1ヶ月 スモーク
- `mp_q3.json` - **8ペア×Q3 ベースライン (PF 2.70)**
- `mp_q3_htfblock.json` - 8ペア×Q3 HTF block ON (差分ゼロ)
- `mp_apr_base.json` - **8ペア×2026-04 (BT-ライブ乖離証拠)**
- `smoke_1m.log` / `mp_*.log` - 詳細ログ
