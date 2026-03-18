# レジーム分類改善 — 段階的拡張計画

## Context

辛口評価「3. 相場レジームへの依存 — TREND/RANGE/LOW_VOLの3分類は粗い」への対策。
現在のレジーム検出はADX+MA整列+ATRの3指標で4分類（TREND/RANGE/HIGH_VOL/LOW_VOL）。
レンジブレイク直後やVショック、方向感のない相場に対応できない。

段階的に3つのレジームを追加し、各段階でBT検証して効果を確認する。

---

## Phase 1: BREAKOUT検出（最優先）

### 目的
RANGE→TRENDの遷移初期を捉え、方向性が明確+モメンタムがある局面で
専用パラメータ（TP拡大・SL緩和）を適用。

### 判定ロジック
```
BREAKOUT条件:
  ① 直近N足（20足）の高値/安値を現在価格が突破
  ② ADX < trend_adx_threshold（まだTRENDと判定されない段階）
  ③ ATR変化率が正（ボラ拡大中）
  → ①②③を全て満たしたらBREAKOUT
  → TREND判定より優先度を高くする（BREAKOUTはTRENDの前駆状態）
```

### 修正ファイル
1. `autotrader/core/enums.py` — `MarketRegime` に `BREAKOUT` 追加
2. `autotrader/calculator/features/regime_detector.py` — BREAKOUT判定ロジック追加
3. `autotrader/decision/unified/config.py` — BREAKOUT関連パラメータ追加
   - `regime_breakout_tp_multiplier: float = 1.5`（TP拡大）
   - `regime_breakout_threshold_add: float = 0.0`（閾値調整なし＝積極エントリー）
4. `autotrader/decision/unified/pipeline_pkg/pipeline.py` — BREAKOUT時のTP/閾値分岐
5. `autotrader/decision/unified/risk/position_manager.py` — BREAKOUT時の停滞時間延長

### BT検証
```json
{"id": "REG-A1-breakout", "type": "multi_pair", "years": "2023-2025",
 "overrides": {"bot": {"regime_breakout_enabled": true}}}
{"id": "REG-A2-baseline", "type": "multi_pair", "years": "2023-2025"}
```
比較: BREAKOUT ON vs OFF でPF/DD/トレード数の変化

---

## Phase 2: ボラティリティ方向検出

### 目的
ATRの「大きい/小さい」だけでなく「拡大中/縮小中」を検出。
スクイーズ（縮小中）はブレイクアウト前兆、拡大中はリスク増大。

### 判定ロジック
```
既存のHIGH_VOL/LOW_VOLを拡張:
  HIGH_VOL + ATR上昇中 → VOLATILITY_EXPANDING（リスク拡大、SL広げ）
  LOW_VOL + ATR下降中 → VOLATILITY_COMPRESSING（スクイーズ、待機）

ATR変化率 = (ATR - ATR_MA20) / ATR_MA20
  > +0.3 → 拡大中
  < -0.2 → 縮小中
```

### 修正ファイル
1. `autotrader/core/enums.py` — 既存のHIGH_VOL/LOW_VOLに方向属性追加（またはサブ状態）
2. `autotrader/calculator/features/regime_detector.py` — ATR変化率の計算追加
3. `RegimeResult` に `volatility_direction: str` フィールド追加（"expanding"/"compressing"/"neutral"）
4. SoftGuard — EXPANDING時にペナルティ加算
5. PM — EXPANDING時のSL幅調整

### BT検証
Phase 1の結果を踏まえて、BREAKOUT + ボラ方向の複合効果を確認

---

## Phase 3: CHOPPY（方向感なし）検出

### 目的
トレンドでもレンジでもない、ランダムウォークに近い状態を検出。
この局面ではトレードを強く抑制する。

### 判定ロジック
```
CHOPPY条件:
  ① Choppiness Index (14期間) > 61.8（フィボナッチ値）
  ② ADX < 20（方向性なし）
  ③ MA整列度 < 0.15（MAがバラバラ）
  → 全て満たしたらCHOPPY
  → コンセンサス閾値を大幅上乗せ（+3.0〜+5.0）で実質トレード禁止
```

### 修正ファイル
1. `autotrader/core/enums.py` — `MarketRegime.CHOPPY` 追加
2. `autotrader/calculator/features/regime_detector.py` — Choppiness Index計算+判定
3. `autotrader/calculator/indicators.py` — `choppiness_index()` 関数追加（pandas_taで計算可能）
4. Pipeline — CHOPPY時の閾値上乗せ

### BT検証
Phase 1+2の結果を踏まえて、全3レジーム追加の複合効果を確認

---

## 実装順序

```
Phase 1 (BREAKOUT)
  → worktree作成 → 実装 → テスト → PR → BT検証
  → 効果確認後にPhase 2へ

Phase 2 (ボラ方向)
  → Phase 1の結果を踏まえて実装 → BT検証

Phase 3 (CHOPPY)
  → Phase 1+2の結果を踏まえて実装 → BT検証
  → 最終的な全レジーム統合BT
```

各Phaseは独立したPRで、前Phaseの検証結果に基づいて次Phaseの設計を調整する。

## 検証方法

- 各Phase完了後に8ペアマルチBT（2023-2025、実spread）でON/OFF比較
- レジーム別breakdown（regime別WR/PF/trades）で効果を定量評価
- OOS期間（2020-2022）でも検証して過学習チェック
