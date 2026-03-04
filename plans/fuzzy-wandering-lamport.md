# 経済指標アダプティブポーリング: 発表前後3秒間隔

## Context

**問題**: 経済指標の発表結果（actual値）反映に最大60分かかる。トレード判断にはスピードが不可欠。

**原因（2層のボトルネック）**:
1. **MQL5 CalendarExporter**: CSVを30分間隔で書き出し → actual値がCSVに反映されるのが最大30分後
2. **Python FundamentalDataCollector**: CSVを60分間隔で読み込み → 最大60分遅延

**解決**: 両層でアダプティブポーリングを導入。HIGHイベント前後は3秒間隔に自動切替。

## 変更ファイル一覧

| ファイル | 変更量 | 内容 |
|---------|--------|------|
| `scripts/mt5/CalendarExporter.mq5` | ~40行 | アダプティブ間隔（HIGH前後3秒） |
| `autotrader/adapters/fundamental/collector.py` | ~60行 | アダプティブポーリング + MT5/FF分離 |

## 変更内容

### 1. `scripts/mt5/CalendarExporter.mq5`

MQL5側もアダプティブにしないと、Pythonが3秒で読んでもCSVの中身が古いまま。

**a) HIGH イベント接近判定関数を追加:**

```mql5
// 次のHIGHイベントまでの秒数を返す（なければ INT_MAX）
int SecondsToNextHighEvent()
{
   datetime now = TimeCurrent();
   datetime to = now + 3600; // 1時間先まで確認
   MqlCalendarValue values[];
   int count = CalendarValueHistory(values, now, to);
   if(count <= 0) return INT_MAX;

   int min_sec = INT_MAX;
   for(int i = 0; i < count; i++)
   {
      MqlCalendarEvent ev;
      if(!CalendarEventById(values[i].event_id, ev))
         continue;
      if(ev.importance != CALENDAR_IMPORTANCE_HIGH)
         continue;
      // 未発表（actual が LONG_MIN）のみ対象
      if(values[i].actual_value != LONG_MIN)
         continue;
      int diff = (int)(values[i].time - now);
      if(diff >= 0 && diff < min_sec)
         min_sec = diff;
   }
   return min_sec;
}
```

**b) OnStart() ループをアダプティブに:**

```mql5
void OnStart()
{
   PrintFormat("[CalendarExporter] サービス開始 (アダプティブ間隔)");

   while(!IsStopped())
   {
      ExportCalendar();

      // 次のHIGHイベントまでの秒数で間隔を決定
      int sec_to_high = SecondsToNextHighEvent();
      int interval;
      if(sec_to_high <= 300)       // 5分以内
         interval = 3;             // 3秒間隔
      else if(sec_to_high <= 1800) // 30分以内
         interval = 30;            // 30秒間隔
      else
         interval = UpdateIntervalSec; // デフォルト30分

      // スリープ（1秒刻みで停止チェック + 間隔再計算）
      for(int elapsed = 0;
          elapsed < interval && !IsStopped();
          elapsed++)
      {
         Sleep(1000);
      }
   }

   PrintFormat("[CalendarExporter] サービス停止");
}
```

**c) 発表直後のキャッチ: 発表後2分間も3秒ポーリング**

`SecondsToNextHighEvent` を拡張し、直近で発表されたHIGHイベント（actual_value != LONG_MIN かつ event_time が過去2分以内）も3秒トリガーに含める:

```mql5
int SecondsToNextHighEvent()
{
   datetime now = TimeCurrent();
   datetime from = now - 120; // 過去2分も確認（発表直後キャッチ）
   datetime to = now + 3600;
   MqlCalendarValue values[];
   int count = CalendarValueHistory(values, from, to);
   if(count <= 0) return INT_MAX;

   int min_sec = INT_MAX;
   for(int i = 0; i < count; i++)
   {
      MqlCalendarEvent ev;
      if(!CalendarEventById(values[i].event_id, ev))
         continue;
      if(ev.importance != CALENDAR_IMPORTANCE_HIGH)
         continue;

      int diff = (int)(values[i].time - now);

      // 未発表で未来 → 発表前カウントダウン
      if(values[i].actual_value == LONG_MIN && diff >= 0)
      {
         if(diff < min_sec) min_sec = diff;
      }
      // 発表済みで過去2分以内 → 発表直後キャッチ
      else if(values[i].actual_value != LONG_MIN
              && diff >= -120 && diff <= 0)
      {
         min_sec = 0; // 即座に3秒モード
      }
   }
   return min_sec;
}
```

### 2. `autotrader/adapters/fundamental/collector.py`

**a) `_collect_once()` をMT5専用とFF専用に分離:**

```python
async def _fetch_mt5_events(self) -> list[EconomicEvent]:
    """MT5カレンダーCSVのみ取得（軽量・ローカルI/O）"""
    if not self._use_mt5:
        return []
    now = datetime.now(UTC)
    from_date = now - timedelta(hours=1)
    to_date = now + timedelta(days=7)
    try:
        events = await self._mt5_client.fetch_events_async(
            from_date=from_date,
            to_date=to_date,
            currencies=self._currencies,
        )
        return events
    except Exception as e:
        logger.error(
            "[Collector] MT5取得エラー: %s", e
        )
        return []

async def _fetch_ff_events(self) -> list[EconomicEvent]:
    """ForexFactory取得（重量・HTTPスクレイピング）"""
    # 既存の FF + FF休日ロジックをここに移動
    ...
```

**b) `_get_seconds_to_next_high()` 判定メソッド追加:**

```python
def _get_seconds_to_next_high(self) -> float:
    """キャッシュから次のHIGHイベントまでの秒数を算出

    Returns:
        float: 秒数（HIGHなければ float('inf')）
    """
    now = datetime.now(UTC)
    min_sec = float("inf")
    for ev in self._cached_events:
        if ev.impact != ImpactLevel.HIGH:
            continue
        diff = (ev.event_time - now).total_seconds()
        # 未発表で未来 → 発表前カウントダウン
        if ev.actual is None and diff >= 0:
            min_sec = min(min_sec, diff)
        # 発表済みで過去2分以内 → 直後キャッチ
        elif (
            ev.actual is not None
            and -120 <= diff <= 0
        ):
            min_sec = 0
            break
    return min_sec
```

**c) `_collect_loop()` をアダプティブに:**

```python
async def _collect_loop(self) -> None:
    """アダプティブ収集ループ"""
    # 起動直後に全ソースから1回収集
    await self._collect_once()

    last_ff_fetch = datetime.now(UTC)
    _FF_INTERVAL = timedelta(hours=12)

    while self._running:
        try:
            # 次のHIGHイベントまでの秒数で間隔を決定
            sec = self._get_seconds_to_next_high()
            if sec <= 300:       # 5分以内
                interval = 3     # 3秒
            elif sec <= 1800:    # 30分以内
                interval = 30    # 30秒
            else:
                interval = self._interval.total_seconds()

            await asyncio.sleep(interval)

            # MT5は毎回取得（ローカルCSV、軽い）
            mt5_events = await self._fetch_mt5_events()

            # FFは12時間ごと
            now = datetime.now(UTC)
            ff_events: list[EconomicEvent] = []
            if now - last_ff_fetch >= _FF_INTERVAL:
                ff_events = await self._fetch_ff_events()
                last_ff_fetch = now

            # マージ・重複排除・キャッシュ更新
            all_events = mt5_events + ff_events
            if ff_events:
                all_events = self._normalizer.deduplicate(
                    all_events
                )
            self._cached_events = all_events
            self._last_fetch = now

            # 3秒モード時はDEBUG、通常時はINFO
            if interval <= 3:
                logger.debug(
                    "[Collector] 高速更新: "
                    "%d件 (次HIGH: %.0f秒後)",
                    len(all_events), sec,
                )
            else:
                logger.info(
                    "[Collector] %d件のイベントを"
                    "キャッシュ更新",
                    len(all_events),
                )

            # コールバック
            if self._on_update is not None:
                try:
                    result = self._on_update(all_events)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:
                    logger.error(
                        "[Collector] コールバック"
                        "エラー: %s", e
                    )

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(
                "[Collector] 収集ループエラー: %s", e
            )
            await asyncio.sleep(60)
```

## ポーリング間隔まとめ

| 条件 | MQL5 CSV書出し | Python CSV読込 |
|------|---------------|---------------|
| HIGHイベント5分前～発表後2分 | **3秒** | **3秒** |
| HIGHイベント30分前 | **30秒** | **30秒** |
| 通常時 | 30分 | 60分（設定値） |
| ForexFactory | N/A | 12時間（レート制限遵守） |

## 変更しないもの

- `mt5_calendar.py`（CSVリーダー、変更不要）
- `forex_factory.py`（レート制限ロジック、変更不要）
- `schemas.py`（EconomicEvent、ImpactLevel は既存のまま活用）
- `engine.py`（collector呼び出し側は変更不要）
- フロントエンド JS

## 検証方法

1. HIGHイベント5分前にログが3秒間隔で出力されること
2. 通常時はデフォルト間隔でログが出力されること
3. ForexFactoryが12時間ごとにのみ呼ばれること
4. MQL5サービス再起動後、アダプティブ間隔でCSV更新されること
5. WebUIカレンダーでactual値が発表直後に更新されること
6. 既存ユニットテストがPASSすること
