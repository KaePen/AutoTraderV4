# ロット管理安全マージン改善計画

## Context
現在のPositionSizerは5層の安全装置を備えているが、以下6点の懸念がある:
1. SLスリッページ未考慮（急変動時にSL設定値を超えて約定）
2. 注意域(equity_caution)のステップ関数（50%境界で急にロット半減）
3. 連敗調整のステップ関数（5連敗で急に0.5x）
4. max_lot_per_trade=2.0がスリッページ未考慮
5. エクスポージャー上限5.0lotが同方向リスクを考慮していない
6. DD調整が5%閾値で非連続

## 修正対象ファイル
- `src/autotrader/decision/unified/position_sizer.py` (メイン)
- `src/autotrader/core/interfaces/position_sizing.py` (SizingContext拡張)
- `src/autotrader/decision/unified/config.py` (UnifiedBotConfig)
- `scripts/run_backtest.py` (CLI引数)
- `tests/unit/decision/unified/test_position_sizer.py` (テスト追加)

## 改善内容

### 1. SLスリッページバッファ
**PositionSizerConfig** に `slippage_buffer_pips: float = 2.0` 追加。
ロット計算時に `sl_pips + slippage_buffer_pips` を使用:
```
lot = risk_budget / ((sl_pips + slippage_buffer_pips) * pip_value)
```
これにより実質SLが2pips大きいものとして計算し、スリッページ分の安全余裕を確保。

### 2. 注意域を段階的減衰に変更
現在: `equity_ratio <= 0.50` → 一律0.5x
変更: `equity_ratio`が1.0→0.30の範囲で線形に減衰:
```python
# 100%→50%: フルサイズ（1.0x）
# 50%→30%: 線形減衰（1.0x→0.25x）
if equity_ratio > caution_pct:
    caution_adjust = 1.0
elif equity_ratio <= floor_pct:
    blocked
else:
    # 線形補間: caution→floorで1.0→0.25
    ratio = (equity_ratio - floor_pct) / (caution_pct - floor_pct)
    caution_adjust = 0.25 + ratio * 0.75
```

### 3. 連敗調整を段階的に変更
現在: `< 5 → 1.0`, `>= 5 → 0.5`
変更: 3連敗から段階的に減額:
```python
if consecutive_losses < 3:
    return 1.0
elif consecutive_losses >= 8:
    return 0.3  # 下限
else:
    # 3→8で1.0→0.3の線形補間
    ratio = (consecutive_losses - 3) / 5
    return 1.0 - ratio * 0.7
```
`consecutive_loss_threshold`は廃止し、`consecutive_loss_start: int = 3`、`consecutive_loss_max: int = 8`、`consecutive_loss_min_adjust: float = 0.3`を追加。

### 4. max_lot_per_tradeをスリッページ考慮で調整
`max_risk_pct_absolute`を使って動的に上限を計算:
```python
# 絶対リスク上限からの逆算上限
max_lot_from_risk = (
    equity * max_risk_pct_absolute
) / ((sl_pips + slippage_buffer_pips) * pip_value)
lot = min(lot, config.max_lot_per_trade, max_lot_from_risk)
```
静的な`max_lot_per_trade=2.0`は安全弁として残しつつ、リスクベースの動的上限を追加。

### 5. 同方向エクスポージャーリスク
**SizingContext**に`open_same_direction_lot: float = 0.0`を追加。
同方向ポジションが多い場合、エクスポージャー制限を厳しくする:
```python
# 同方向エクスポージャー制限（全体の60%まで）
max_same_dir = config.max_total_exposure_lot * 0.6
remaining_same_dir = max_same_dir - context.open_same_direction_lot
lot = min(lot, remaining_same_dir)
```
`max_same_direction_ratio: float = 0.6` を PositionSizerConfig に追加。

### 6. DD調整の平滑化
現在: 5%以下は1.0、5%超過から線形減額
変更: 2%から徐々に開始（微減）、5%超過から本格減額:
```python
if dd <= 0.02:
    return 1.0
elif dd <= dd_threshold(0.05):
    # 2%→5%で1.0→0.9の緩やかな減額
    ratio = (dd - 0.02) / 0.03
    return 1.0 - ratio * 0.1
else:
    # 5%→20%で0.9→0.3の本格減額
    excess = dd - dd_threshold
    reduction = min(excess / max_excess, 1.0) * dd_max_reduction
    return 0.9 - reduction * 0.6
```
`dd_early_threshold: float = 0.02` を PositionSizerConfig に追加。

## CLIフラグ追加
- `--slippage-buffer` (float, default=2.0): スリッページバッファpips

## テスト追加
- `test_slippage_buffer`: スリッページバッファ有無でロット比較
- `test_gradual_caution_zone`: 注意域の段階的減衰
- `test_gradual_consecutive_loss`: 連敗の段階的減額
- `test_same_direction_exposure`: 同方向エクスポージャー制限
- `test_smooth_dd_adjustment`: DD調整の平滑化
- `test_dynamic_max_lot_from_risk`: リスクベース動的上限

## 検証
1. `pytest tests/unit/decision/unified/test_position_sizer.py -v` 全パス
2. `pytest tests/ --tb=short` 全テストパス
3. 16年バックテスト実行で結果比較（PF/DD/勝率）
