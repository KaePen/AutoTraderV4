# BT/ライブ エントリーゲート統合計画

## Context

BTとライブで同じデータを使ってもトレード結果が異なる。原因は、シグナル生成パイプライン（`generate_signal()`）は共通だが、その**後段のエントリーゲート**がBT（`year_runner.py` + `simulator.py`）とライブ（`engine.py`）で独立に実装されていること。

コアルール「トレードロジックは単一実装、BT/ライブの違いはデータI/Oのみ」に違反している。

### 乖離の全容

| ゲート | BT側 | ライブ側 | 影響 |
|--------|------|---------|------|
| confidence >= 0.5 | `year_runner.py:406` にハードコード | **なし** | BTのみトレード減少 |
| スプレッドゲート | `year_runner.py:445-466` 独自実装 | `engine.py:840-853` TickEntryOptimizer | 判定ロジックが異なる |
| シンボルポジション上限+ボーナス | `simulator.py:581-591` | `engine.py:1516-1538` | 重複実装、demo_mode分岐がライブのみ |
| グローバルポジション上限 | **なし** | `engine.py:1540-1558` | ライブのみ制限 |
| グローバルエクスポージャー上限 | **なし** | `engine.py:1560-1578` | ライブのみ制限 |
| JPY同方向制限 | **なし** | `engine.py:1580-1602` | ライブのみ制限 |
| DD緊急停止 | **なし** | `engine.py:1489-1498` | ライブのみ制限 |
| マージンチェック | `simulator.py:1146-1149` 80%ハードコード | MT5任せ | BT独自ロジック |

## 方針

`autotrader/constraint/entry_gate.py` に共通の `EntryGateChecker` を新設し、BT/ライブ両方からこれを呼ぶ。既存の `constraint/` ディレクトリの設計パターン（`HardGuard`/`SoftGuard`/`ConstraintResult`）に合わせる。

## Phase 1: 共通エントリーゲートモジュール作成

### 新規ファイル: `autotrader/constraint/entry_gate.py` (~120行)

```python
@dataclass(frozen=True)
class EntryGateContext:
    """エントリーゲート判定に必要な状態スナップショット（I/O非依存）"""
    # シグナル情報
    signal_direction: SignalType
    consensus_score: float | None

    # ポジション状態（呼び出し側がI/Oで取得して渡す）
    symbol_position_count: int
    global_position_count: int        # シングルペアBTでは0
    global_exposure_lot: float        # シングルペアBTでは0.0
    jpy_same_direction_count: int     # 非JPYペアでは0

    # 設定値（UnifiedBotConfig / SimulatorConfig から取得）
    max_positions: int
    bonus_max_positions: int
    bonus_score_threshold: float
    global_max_positions: int         # 0 = 無制限
    global_max_exposure_lot: float    # 0.0 = 無制限
    max_same_direction_jpy: int       # 0 = 無制限
    is_jpy_pair: bool

    # スプレッド
    current_spread_pips: float
    spread_threshold_pips: float | None  # None = ゲート無効

    # DD緊急停止
    dd_emergency_active: bool

    # マージン
    margin_usage_pct: float           # 使用率% (0-100)
    margin_limit_pct: float           # 上限% (デフォルト80)


@dataclass(frozen=True)
class EntryGateResult:
    allowed: bool
    deny_reason: str | None = None
    deny_code: str | None = None


class EntryGateChecker:
    """BT/ライブ共通のエントリーゲート判定（純粋ロジック、I/O無し）"""

    def evaluate(self, ctx: EntryGateContext) -> EntryGateResult:
        # 1. DD緊急停止
        # 2. シンボルポジション上限（ボーナス枠含む）
        # 3. グローバルポジション上限
        # 4. グローバルエクスポージャー上限
        # 5. JPY同方向制限
        # 6. スプレッドゲート
        # 7. マージンチェック
        ...
```

ゲートの順序はライブ側の現行順序（`engine.py:1489-1602`）に合わせる。

### 修正: `autotrader/constraint/__init__.py`
- `EntryGateChecker`, `EntryGateContext`, `EntryGateResult` をエクスポート

## Phase 2: BT側の統合

### 修正: `autotrader/backtest/year_runner.py`

1. **`confidence >= 0.5` フィルター削除** (line 406)
   - ライブにはこのフィルターが存在しない
   - SoftGuardとコンセンサス閾値で品質ゲートは既に実装済み
   - 変更: `if consolidated.confidence >= 0.5:` → 条件を削除、直接Signal構築

2. **BT Spread Gate 削除** (lines 441-466)
   - `EntryGateChecker` のスプレッドゲートに置き換え

3. **`EntryGateChecker` 呼び出し追加** (line 466付近、Signal構築後)
   ```python
   if signal is not None:
       gate_ctx = EntryGateContext(
           signal_direction=signal.signal_type,
           consensus_score=signal.consensus_score,
           symbol_position_count=len(simulator.get_open_positions()),
           # シングルペアBTではグローバル制限は0（無制限）
           global_position_count=0,
           global_exposure_lot=0.0,
           jpy_same_direction_count=0,
           max_positions=sim_config.max_positions,
           bonus_max_positions=sim_config.bonus_max_positions,
           bonus_score_threshold=sim_config.bonus_score_threshold,
           global_max_positions=0,
           global_max_exposure_lot=0.0,
           max_same_direction_jpy=0,
           is_jpy_pair=symbol.endswith("JPY"),
           current_spread_pips=_spread_pips,
           spread_threshold_pips=bot_config.sg_spread_threshold_pips,
           dd_emergency_active=False,
           margin_usage_pct=...,  # simulator.stateから計算
           margin_limit_pct=80.0,
       )
       result = entry_gate.evaluate(gate_ctx)
       if not result.allowed:
           _emitter.emit_signal_blocked(...)
           signal = None
   ```

### 修正: `autotrader/backtest/simulator.py`

1. **`process_candle()` のポジション枠チェック** (lines 581-591)
   - `entry_pre_approved: bool = False` パラメータ追加
   - `entry_pre_approved=True` の場合、ポジション枠チェックをスキップ（year_runnerが事前チェック済み）
   - 既存の外部呼び出し元（fast_backtest等）は `False` のまま → 後方互換

2. **`_execute_pending_entry()` のポジション枠チェック** (lines 622-631)
   - 同様に `entry_pre_approved` フラグで制御
   - ただし pending → 実行の間に他ポジションが開く可能性があるため、安全ネットとして残す

3. **`_open_position()` のマージンチェック** (lines 1146-1149)
   - 80%ハードコードを `config.margin_limit_pct` に置き換え（デフォルト80.0）

## Phase 3: ライブ側の統合

### 修正: `autotrader/live/engine.py`

`_execute_entry()` メソッド (lines 1483-1602) を修正:

1. **ホットリロードチェック** (lines 1500-1504) → そのまま残す（ライブインフラ固有）
2. **MT5ポジション取得** (lines 1507-1514) → そのまま残す（データI/O）
3. **インラインゲートロジック** (lines 1516-1602) → `EntryGateChecker` 呼び出しに置換

```python
async def _execute_entry(self, signal: Signal) -> None:
    # ホットリロード中はスキップ（ライブインフラ固有）
    if self._entry_blocked:
        ...
        return

    # MT5ポジション取得（データI/O）
    positions = await self._executor.get_open_positions_async(...)
    if positions is None:
        ...
        return

    # 共通エントリーゲート判定
    cfg = self._bot.config
    gate_ctx = EntryGateContext(
        signal_direction=signal.signal_type,
        consensus_score=signal.consensus_score,
        symbol_position_count=len(positions),
        global_position_count=(
            self._get_global_position_count()
            if self._get_global_position_count else 0
        ),
        global_exposure_lot=(...),
        jpy_same_direction_count=(...),
        max_positions=(
            cfg.demo_max_positions if cfg.demo_mode else cfg.max_positions
        ),
        bonus_max_positions=getattr(cfg, "bonus_max_positions", 0),
        bonus_score_threshold=getattr(cfg, "bonus_score_threshold", 7.0),
        global_max_positions=self._global_max_positions,
        global_max_exposure_lot=self._global_max_exposure_lot,
        max_same_direction_jpy=self._max_same_direction_jpy,
        is_jpy_pair=self._active_symbol.endswith("JPY"),
        current_spread_pips=self._bot._current_spread_pips,
        spread_threshold_pips=getattr(cfg, "sg_spread_threshold_pips", None),
        dd_emergency_active=(
            self._engine_manager.dd_emergency_active
            if self._engine_manager else False
        ),
        margin_usage_pct=0.0,   # MT5がマージン管理
        margin_limit_pct=0.0,   # MT5に委譲
    )
    result = self._entry_gate.evaluate(gate_ctx)
    if not result.allowed:
        self._last_entry_skip_reason = result.deny_reason
        logger.info("[%s] エントリーゲート: %s", self._active_symbol, result.deny_code)
        return

    # 以降のロット計算・MT5注文実行は変更なし
    ...
```

## Phase 4: テスト

### 新規: `tests/unit/constraint/test_entry_gate.py`

`EntryGateChecker` の全7ゲートに対するユニットテスト:
- 各ゲート単独のallow/denyケース
- 複数ゲートの組み合わせ（最初にヒットしたゲートで停止）
- ボーナスポジション枠の計算
- 境界値テスト（ちょうど上限の場合）

### 既存テスト修正
- `tests/golden/test_backtest_golden.py` — confidence 0.5フィルター削除によりトレード数が変わる可能性 → ゴールデンファイル更新
- `tests/unit/backtest/` — simulator関連テストでentry_pre_approvedパラメータ追加

### 回帰検証
- USDJPYシングルペア 2023-2025 BTを実行し、ライブと同一ゲートで結果が一貫することを確認
- confidence >= 0.5 フィルター削除の影響を定量評価（トレード数増加幅）

## 変更しないもの

- `generate_signal()` パイプライン — 既に共通、変更不要
- `PositionManager.evaluate()` — 既に共通、変更不要
- `PositionSizer.calculate()` — ライブのSizingContext構築はengine.pyに残る（データI/O固有）
- `HardGuard` / `SoftGuard` — パイプライン内で使用、変更不要
- `TickEntryOptimizer` — ライブのティック精度最適化はデータI/O層の最適化（トレード判定ではない）
- ファンダメンタルイベントスキップ — パイプライン前のデータ前処理として、BT/ライブ各自で残す（Phase 2で統合可能だが今回スコープ外）

## ファイル変更サマリ

| ファイル | 操作 | 規模 |
|---------|------|------|
| `autotrader/constraint/entry_gate.py` | **新規** | ~120行 |
| `autotrader/constraint/__init__.py` | 修正 | エクスポート追加 |
| `autotrader/backtest/year_runner.py` | 修正 | confidence削除 + spread gate削除 + gate呼び出し追加 |
| `autotrader/backtest/simulator.py` | 修正 | entry_pre_approved追加 + margin設定化 |
| `autotrader/live/engine.py` | 修正 | _execute_entry()のインラインゲート → gate呼び出し |
| `tests/unit/constraint/test_entry_gate.py` | **新規** | ~200行 |
| `tests/golden/test_backtest_golden.py` | 修正 | ゴールデン値更新 |

## 実装順序

1. `entry_gate.py` 作成 + ユニットテスト → テスト全通過確認
2. `year_runner.py` 修正（confidence削除 + gate統合）→ BT回帰テスト
3. `simulator.py` 修正（entry_pre_approved追加）→ テスト通過確認
4. `engine.py` 修正（インラインゲート → gate呼び出し）→ テスト通過確認
5. ゴールデンテスト更新
6. USDJPY 2023-2025 BT実行で回帰確認
