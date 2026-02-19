# ログ分析に基づく3改善実装計画

## Context

バックテストログの詳細分析から3つの弱点が特定された：
1. **JST 18-21時 (UTC 9-12)** のエントリーが弱い（PF 1.10-1.19）
2. **RANGE** レジームの成績が **TREND** より劣る（PF 2.09 vs 3.04）
3. **STAGNATION** 終了が損失の最大要因（-1,215,175）

現在のベスト: PF 2.62, 勝率 58.3%, DD 1.16%

---

## 改善1: Weak Hours RANGEフィルター (A+B統合)

**目的**: JST 18-21時のRANGEエントリーをスコアプレミアムで抑制

### 実装

**config.py** (`UnifiedBotConfig` L138-139の後に追加):
```python
weak_hours_enabled: bool = True
weak_hours_score_premium: float = 1.0
```

**trade_bot.py** (L604の後、MACDスロープフィルター前に挿入):
```python
# Weak Hours RANGEフィルター (JST 18-21 = UTC 9-12)
if (
    self.config.weak_hours_enabled
    and 9 <= hour_utc <= 12
    and regime_result.regime == MarketRegime.RANGE
    and consensus.score < consensus.threshold
        + self.config.weak_hours_score_premium
):
    return self._hold_signal(
        f"WeakHours RANGE: hour={hour_utc}, "
        f"score={consensus.score:.1f}"
        f"<{consensus.threshold + self.config.weak_hours_score_premium:.1f}"
    )
```

**run_backtest.py** (CLIフラグ追加):
```
--no-weak-hours          # 無効化
--weak-hours-premium X   # スコアプレミアム（デフォルト1.0）
```

**ロジック**: UTC 9-12 + RANGE → 通常閾値+1.0のスコアが必要。TRENDは制限なし。

---

## 改善2: 通常Stagnation厳格化 (C)

**目的**: 非RANGE×DAYのStagnation判定を前倒しし損失を削減

### 実装

**position_manager.py** (`PositionManagerConfig` のデフォルト変更):
```python
# 変更前
stagnation_exit_minutes: float = 120.0
stagnation_min_mfe_r: float = 0.2

# 変更後
stagnation_exit_minutes: float = 90.0
stagnation_min_mfe_r: float = 0.15
```

**run_backtest.py** (CLIフラグ追加):
```
--stag-exit-minutes X    # 通常Stagnation時間（デフォルト90）
--stag-min-mfe X         # 通常Stagnation MFE閾値（デフォルト0.15）
```

**PositionManagerConfig構築** (run_backtest.py L663):
```python
pm_config = PositionManagerConfig(
    ...
    stagnation_exit_minutes=getattr(args, "stag_exit_minutes", 90.0),
    stagnation_min_mfe_r=getattr(args, "stag_min_mfe", 0.15),
    ...
)
```

**効果**: 120分→90分、MFE 0.2R→0.15Rで、進捗なし取引を30分早く打ち切り。

---

## 改善3: RANGE×DAY Stagnation Stage前倒し (C追加)

**目的**: RANGE×DAY固有のStagnation段階をさらに早める

### 実装

**run_backtest.py** (デフォルト値変更):
```python
# Stage1: 45min/0.05R → 35min/0.03R
"--range-stag-s1-min", default=35.0
"--range-stag-s1-mfe", default=0.03

# Stage2: 60min/0.10R → 50min/0.08R
"--range-stag-s2-min", default=50.0
"--range-stag-s2-mfe", default=0.08
```

---

## 修正対象ファイル

| ファイル | 変更内容 |
|---------|---------|
| `src/autotrader/decision/unified/config.py` | `weak_hours_enabled`, `weak_hours_score_premium` 追加 |
| `src/autotrader/decision/unified/trade_bot.py` | Weak Hours RANGEフィルター追加（L604後） |
| `src/autotrader/decision/unified/position_manager.py` | Stagnationデフォルト変更 90min/0.15R |
| `scripts/run_backtest.py` | CLIフラグ追加 + デフォルト変更 |

---

## 検証

```bash
# 改善後（デフォルト設定）
python scripts/run_backtest.py --start 2022 --end 2024

# 比較: 改善無効化（旧パラメータ）
python scripts/run_backtest.py --start 2022 --end 2024 \
    --no-weak-hours \
    --stag-exit-minutes 120 --stag-min-mfe 0.2 \
    --range-stag-s1-min 45 --range-stag-s1-mfe 0.05 \
    --range-stag-s2-min 60 --range-stag-s2-mfe 0.10
```

直近3年 (2022-2024) の年別成績を表示し、現在ベストと比較。
