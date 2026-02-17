# マルチタイムフレーム戦略とログ出力改善計画

## 目標

1. 1分足スキャルピング〜中長期足トレードの切り替え対応
2. 戦略ごとのポジション数制御
3. ログファイル出力機能の追加

---

## 現状の問題点

### 問題1: 15分足でのみトレードしている

| 原因箇所 | 問題 |
|----------|------|
| `runner.py:661` | `_load_all_timeframes()`でM1, M5を意図的に除外 |
| `runner.py:709` | `_run_unified_year()`でM15データを基準にループ |
| 結果 | M1, M5のシグナルが完全に無視される |

```python
# 現状のコード
timeframes_to_load = ["M15", "H1", "H4", "D1"]  # M1, M5がない
df = self._m15_df  # M15が基準タイムフレーム
```

### 問題2: ポジション制御が全戦略共通

- `SimulatorConfig.max_positions = 1`（固定）
- 戦略別の制御機能なし

### 問題3: ログファイル出力なし

- イベントシステムはあるがファイル出力リスナーがない
- `loguru`インストール済みだが未使用

---

## 実装計画

### Phase 1: マルチタイムフレーム対応

**修正: `src/autotrader/backtest/runner.py`**

1. `_load_all_timeframes()`にM1データ読み込みを追加
2. 新規メソッド`_run_multi_timeframe_year()`を作成
3. 各タイムフレームの粒度でシグナル評価を実行

```python
# 変更後のデータロード
timeframes_to_load = ["M1", "M5", "M15", "H1", "H4", "D1"]

# M1を基準タイムフレームに変更（最小粒度）
df = self._m1_df if self._m1_df is not None else self._m15_df
```

**新しい実行フロー:**
```
M1データでループ
  ↓
現在時刻で各TFの有効性をチェック
  ↓
有効なTFのみでシグナル評価
  ↓
戦略ごとにエントリー判断
```

### Phase 2: 戦略別ポジション制御

**修正: `src/autotrader/decision/unified/config.py`**

```python
@dataclass
class TimeframeConfig:
    """時間足ごとの設定"""
    timeframe: str
    max_positions: int = 1
    enabled: bool = True

@dataclass
class UnifiedBotConfig:
    timeframe_configs: list[TimeframeConfig] = field(default_factory=list)
```

**修正: `src/autotrader/backtest/simulator.py`**

```python
# 戦略/TF別のポジション追跡
open_positions_by_strategy: dict[str, list[Position]]

def can_open_position(self, strategy_id: str, max_positions: int) -> bool:
    current = len(self.open_positions_by_strategy.get(strategy_id, []))
    return current < max_positions
```

### Phase 3: ログファイル出力

**新規: `src/autotrader/backtest/file_listener.py`**

```python
class FileEventListener(EventListener):
    """ファイル出力リスナー"""

    def __init__(self, log_dir: str = "logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = self.log_dir / f"backtest_{timestamp}.log"

    def on_event(self, event: BacktestEvent) -> None:
        line = self._format_event(event)
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")
```

**修正: `src/autotrader/backtest/runner.py`**

```python
def __init__(self, ..., log_to_file: bool = True):
    if log_to_file:
        self._emitter.add_listener(FileEventListener())
```

---

## 修正対象ファイル

| ファイル | 操作 | 内容 |
|----------|------|------|
| `src/autotrader/backtest/runner.py` | 編集 | M1データ読み込み、実行ループ変更 |
| `src/autotrader/decision/unified/config.py` | 編集 | 戦略別設定追加 |
| `src/autotrader/backtest/simulator.py` | 編集 | 戦略別ポジション追跡 |
| `src/autotrader/backtest/file_listener.py` | 新規 | ファイルログリスナー |
| `src/autotrader/backtest/events.py` | 編集 | __init__にエクスポート追加 |

---

## 実装順序

```
Phase 3 (ログ出力)        → 独立して先に実装可能
Phase 1 (マルチTF対応)    → データ読み込み・ループ変更
Phase 2 (戦略別制御)      → Phase 1完了後
```

Phase 3を先に実装することで、Phase 1/2の動作確認にログを活用できる。

---

## 検証方法

```bash
# 1. ログファイル出力確認
uv run python scripts/run_backtest.py --years 2020-2020
ls -la logs/

# 2. M1データでのバックテスト確認
# logs/backtest_*.log で各TFのシグナルを確認

# 3. WebUI確認
uv run python scripts/run_webui.py --port 8080
# バックテスト実行後、logs/にファイルが生成されることを確認

# 4. ログ内容確認
cat logs/backtest_*.log | grep "シグナル"
# M1, M5, M15, H1, H4, D1 すべてのTFでシグナルが記録されていること
```

---

## 注意事項

### M1データのサイズ

- M1データは1年で約50万行（15分足の15倍）
- メモリ使用量とパフォーマンスに注意
- オプションでM1有効/無効を切り替え可能にする

### 後方互換性

- 既存のM15ベースのバックテストも引き続き動作
- `use_m1=False`オプションで従来動作を維持

