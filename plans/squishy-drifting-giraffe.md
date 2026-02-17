# STAGNATION削減: RANGE×DAY入口フィルター強化

## Context

STAGNATION（停滞決済）が全取引の20.8%（723件, -¥401,825）を占め、最大の損失源。
そのうちRANGE×DAYが430件（-¥241,250, 60%）に集中。
STAGNATION段階化（45min/0.05R, 60min/0.10R）は既に実装済みで機能中。
**残る改善余地は「入口フィルター強化」で無駄打ちを減らすこと。**

### データ根拠

| 分類 | STAG率 | 備考 |
|------|--------|------|
| bb_width < 0.10 | 25.3% | 低ボラ＝高STAG |
| bb_width 0.30+ | 12.8% | 高ボラ＝低STAG |
| penalty=0 | 14.1% | 現フィルターで捕捉不可 |
| 0 < penalty ≤ 0.2 | 25.5% | penalty有は高STAG |
| TOKYO session | 23.8% | 最もSTAG率高い |
| NEWYORK session | 12.5% | 最もSTAG率低い |
| score 5.5-6.0 | 19.0% | 閾値付近は高STAG |
| score 6.5-7.0 | 14.0% | 高スコアは低STAG |

---

## 実装: 2つの入口フィルター候補

### A: bb_width閾値引き上げ（既存フィルター拡張）

**現状**: RANGE + DAY + penalty > 0 + bb_width < 0.14 → HOLD
**変更**: bb_width閾値を0.14 → **0.20**に引き上げ

- `trade_bot.py` L543-564 の閾値変更
- CLIフラグ: `--range-day-bbw` (デフォルト0.20、`--range-day-bbw 0.14`で旧値)
- 根拠: STAG中央値bb_width=0.195、非STAGは0.229。0.20で境界付近を捕捉

### B: RANGE×DAYスコアプレミアム（新フィルター）

**新規**: RANGE + DAY + consensus_score < **6.0** → HOLD
（DAY_TRADEのベース閾値5.5を、RANGE時のみ6.0に引き上げ）

- `trade_bot.py` の既存RANGE+DAYフィルター群の後に追加
- CLIフラグ: `--range-day-score-premium` (デフォルト0.5、`--no-range-day-score-premium`で無効化)
- 根拠: score 5.5-6.0帯のSTAG率19.0% vs 6.5-7.0帯の14.0%

---

## 変更ファイル

| ファイル | 変更内容 |
|----------|----------|
| `src/autotrader/decision/unified/trade_bot.py` | フィルターA: bb_width閾値パラメータ化、フィルターB: スコアプレミアム追加 |
| `src/autotrader/decision/unified/config.py` | UnifiedBotConfig: `range_day_bbw_threshold`, `range_day_score_premium` 追加 |
| `scripts/run_backtest.py` | CLIフラグ追加: `--range-day-bbw`, `--range-day-score-premium`, `--no-range-day-score-premium` |
| `src/autotrader/backtest/runner.py` | config伝播（必要であれば） |

---

## バックテスト比較計画

4パターンで比較（並列実行可能）:

```bash
# 1. ベースライン（現状）
.venv/bin/python scripts/run_backtest.py --years 2020-2024

# 2. フィルターA のみ（bb_width 0.20）
.venv/bin/python scripts/run_backtest.py --years 2020-2024 --range-day-bbw 0.20

# 3. フィルターB のみ（スコアプレミアム +0.5）
.venv/bin/python scripts/run_backtest.py --years 2020-2024 --range-day-score-premium 0.5

# 4. A+B 両方
.venv/bin/python scripts/run_backtest.py --years 2020-2024 --range-day-bbw 0.20 --range-day-score-premium 0.5
```

### 判定基準

| 指標 | 必須条件 | 理想 |
|------|---------|------|
| PF | ≥ 2.56（現状維持） | > 2.60 |
| STAGNATION件数 | < 430（RANGE×DAY） | < 350 |
| 純利益 | ≥ ¥1,521,500 | 増加 |
| 取引数 | 大幅減でないこと | -200以内 |

---

## 検証

1. 4パターンのバックテスト完走
2. STAGNATION件数・損益の改善確認
3. ベスト候補をデフォルト化、MEMORYに記録
