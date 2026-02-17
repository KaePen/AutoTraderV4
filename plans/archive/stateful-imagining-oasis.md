# 改善計画: DAY_TRADE BE導入 + MACD Slope禁止 + TOKYO閾値引上

## Context

SIGNAL_REV安全化+Stagnation Exit実装後の現状:
- PF 2.16, 勝率67.7%, DD 1.42%, Sharpe 5.15, 月間100%
- STAGNATION 540件 → 「入った時点でダメ」が多い（MACDスロープ逆方向帯が主因）
- DAY_TRADEはBE無し → 1R部分利確後もSLが原位置のまま残り、反転でフル損失
- TOKYO penalty=0.15帯にSL/STAGNATION損失が集中

---

## P0-1: DAY_TRADE BE移動導入

### 現状
- `be_enabled_modes = (SWING,)` → DAY_TRADEはBE完全無効
- 早期BE(0.7R)、1R BE共にスキップ → 1R部分利確後も原SLのまま

### 解決策
- `be_enabled_modes`にDAY_TRADEを追加
- `early_breakeven_r`を0.7→0.5に変更（タスク指示「+0.5R到達でSLをBE」）

### 修正箇所

**`src/autotrader/decision/unified/position_manager.py`:**

| 箇所 | 変更 |
|------|------|
| `PositionManagerConfig.be_enabled_modes` L197 | `(SWING,)` → `(SWING, DAY_TRADE)` |
| `PositionManagerConfig.early_breakeven_r` L199 | `0.7` → `0.5` |

### 影響
- DAY_TRADE: 0.5R到達でSL→BE移動、1R到達でBE+30%利確
- SWING: 早期BEが0.7R→0.5Rに前倒し（より早い損失保護）

### テスト
- 既存テスト `test_be_blocked_for_day_trade` → 逆の期待に修正（BE有効に）
- 新規テスト `test_day_trade_early_be_at_0_5r` - 0.5RでBE移動
- 既存テスト `test_swing_early_be_at_0_7r` → 0.5R閾値に修正

---

## P0-2: score_macd_slope <= -2 禁止

### 現状
- `timeframe_evaluator.py`で計算: 順方向+2.5、逆方向-2.0
- `macd_slope <= -2.0`の取引はネットマイナス（STAGNATION多発帯）
- 現在フィルターなし → そのまま取引実行される

### 解決策
- primary_tfのmacd_slopeが-2.0以下なら HOLD

### 修正箇所

**`src/autotrader/decision/unified/trade_bot.py`:**

| 箇所 | 変更 |
|------|------|
| L552以降（RANGE+DAY制限の後） | macd_slopeフィルター追加 |

```python
# MACDスロープ逆方向フィルター
_primary_sig = tf_signals.get(plan.primary_tf)
if _primary_sig and _primary_sig.score_breakdown:
    _macd_slope = _primary_sig.score_breakdown.macd_slope
    if _macd_slope <= -2.0:
        return self._hold_signal(
            f"MACDスロープ逆方向: {_macd_slope:.1f}"
        )
```

### テスト
- バックテストで取引数減少（約458件）とPF向上を確認

---

## P1: TOKYO低ペナルティ帯の閾値引上

### 現状
- 既存TOKYOフィルター: `4<=hour<=6 AND penalty>0 AND score<6.6` → HOLD
- penalty=0.15帯（soft_guard off_hours）にSL/STAGNATIONが集中
- penalty>0だが6.6未満でないと通過してしまう

### 解決策
- TOKYO + 0 < penalty <= 0.2 → consensus閾値+0.2を要求

### 修正箇所

**`src/autotrader/decision/unified/trade_bot.py`:**

| 箇所 | 変更 |
|------|------|
| 既存TOKYOフィルター（L540）の後 | 低ペナルティ帯の追加閾値 |

```python
# TOKYO低ペナルティ帯: 閾値+0.2
if (
    4 <= hour_utc <= 6
    and 0 < sg_result.total_penalty <= 0.2
    and consensus.score < consensus.threshold + 0.2
):
    return self._hold_signal(
        f"TOKYO低penalty閾値: penalty="
        f"{sg_result.total_penalty:.2f}, "
        f"score={consensus.score:.1f}"
        f"<{consensus.threshold + 0.2:.1f}"
    )
```

### テスト
- バックテストでTOKYO帯のSL/STAGNATION減少を確認

---

## 修正ファイル一覧

| ファイル | 変更内容 |
|---------|---------|
| `src/autotrader/decision/unified/position_manager.py` | be_enabled_modesにDAY_TRADE追加、early_breakeven_r→0.5 |
| `src/autotrader/decision/unified/trade_bot.py` | macd_slopeフィルター追加、TOKYO低penalty閾値追加 |
| `tests/unit/decision/unified/test_position_manager.py` | BE関連テスト修正・追加 |

---

## 検証手順

1. `pytest tests/unit/decision/unified/test_position_manager.py -v`
2. `pytest tests/ -v`
3. `.venv/bin/python scripts/run_backtest.py --years 2020-2024`
4. 確認項目:
   - DAY_TRADEのBE_HIT件数（新規発生を確認）
   - STAGNATION件数の減少（macd_slopeフィルター効果）
   - TOKYO帯のSL件数の減少
   - PF/勝率/DDの改善
