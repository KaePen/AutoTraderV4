# USDJPY Trade Optimizer Memory

## 2020-2021レンジ期間パフォーマンス低下の根本原因（2026-03分析）

### 根本原因サマリ
1. consensus_threshold=8.0がレンジ相場で高すぎ（シグナル不足）
2. RANGEフィルタ5つが多重適用（トレード数半減）
3. HTF整合性閾値0.8がレンジ期間で厳しすぎ
4. SoftGuardペナルティ重複（trend弱+MTF不整合+オフタイム=0.4）

### 重要ファイルパス
- コンセンサスロジック: `decision/unified/mode_aware_consensus.py`
- トレードボット（フィルタ集約）: `decision/unified/trade_bot.py:908-970`（RANGE系フィルタ）
- 設定: `decision/unified/config.py` (UnifiedBotConfig)
- レジーム検出: `calculator/features/regime_detector.py`
- ソフトガード: `constraint/soft_guard.py`
- 通貨ペア設定: `config/symbol_presets.yaml`

### 改善案優先順位
S1: range_day_score_premium=0.3→0.0, S2: trend_strength閾値0.3→0.15,
S3: HTF閾値0.8→0.5, M1: レジーム適応型閾値, M3: RANGEフィルタ統合

### レポート
- `reports/usdjpy_regime_analysis.md` に詳細分析レポート出力済み
