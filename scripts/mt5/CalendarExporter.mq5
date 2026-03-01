//+------------------------------------------------------------------+
//| CalendarExporter.mq5                                              |
//| MT5経済カレンダーをCSVにエクスポートするサービス                  |
//|                                                                    |
//| セットアップ:                                                      |
//|   1. このファイルを MQL5/Services/ にコピー                       |
//|   2. MT5でコンパイル                                              |
//|   3. ナビゲーター → サービス → CalendarExporter → 開始           |
//|                                                                    |
//| 出力: MQL5/Files/calendar_events.csv                              |
//| 更新間隔: アダプティブ（通常30分、HIGH前後3秒）                  |
//+------------------------------------------------------------------+
#property service
#property copyright "AutoTraderV4"
#property version   "1.00"
#property description "経済カレンダーCSVエクスポーター"

// 更新間隔（秒）
input int UpdateIntervalSec = 1800; // 30分
// 過去取得日数
input int LookbackDays = 1;
// 未来取得日数
input int ForwardDays = 7;
// 出力ファイル名
input string OutputFile = "calendar_events.csv";

//+------------------------------------------------------------------+
//| インパクトレベルを文字列に変換                                    |
//+------------------------------------------------------------------+
string ImpactToString(ENUM_CALENDAR_EVENT_IMPORTANCE importance)
  {
   switch(importance)
     {
      case CALENDAR_IMPORTANCE_HIGH:
         return "high";
      case CALENDAR_IMPORTANCE_MODERATE:
         return "medium";
      case CALENDAR_IMPORTANCE_LOW:
         return "low";
      default:
         return "low";
     }
  }

//+------------------------------------------------------------------+
//| 値を適切にフォーマット（MT5は値を100万倍で格納）                 |
//+------------------------------------------------------------------+
string FormatValue(long raw_value)
  {
   if(raw_value == LONG_MIN)
      return "";
   // MT5カレンダーの値は 1,000,000 倍で格納されている
   double val = (double)raw_value / 1000000.0;
   return DoubleToString(val, 3);
  }

//+------------------------------------------------------------------+
//| ブローカーサーバー時刻とGMTの差分（秒）を取得                    |
//| ※TimeCurrent()はサーバー時刻、TimeGMT()はUTC                    |
//+------------------------------------------------------------------+
int GetBrokerGMTOffsetSec()
  {
   return (int)(TimeCurrent() - TimeGMT());
  }

//+------------------------------------------------------------------+
//| datetime を真のUTC ISO形式文字列に変換                           |
//| MT5カレンダーの時刻はブローカーサーバー時刻のため                |
//| GMTオフセットを減算して真のUTCに変換する                          |
//+------------------------------------------------------------------+
string DatetimeToUTCString(datetime dt)
  {
   // ブローカーサーバー時刻 → UTC に変換
   datetime utc_dt = dt - GetBrokerGMTOffsetSec();
   MqlDateTime mdt;
   TimeToStruct(utc_dt, mdt);
   return StringFormat("%04d-%02d-%02dT%02d:%02d:%02dZ",
                       mdt.year, mdt.mon, mdt.day,
                       mdt.hour, mdt.min, mdt.sec);
  }

//+------------------------------------------------------------------+
//| CSVフィールドをエスケープ（カンマ・引用符対策）                   |
//+------------------------------------------------------------------+
string EscapeCSV(string text)
  {
   if(StringFind(text, ",") >= 0
      || StringFind(text, "\"") >= 0
      || StringFind(text, "\n") >= 0)
     {
      StringReplace(text, "\"", "\"\"");
      return "\"" + text + "\"";
     }
   return text;
  }

//+------------------------------------------------------------------+
//| カレンダーデータをCSVに書き出し                                   |
//+------------------------------------------------------------------+
bool ExportCalendar()
  {
   // 取得範囲（サーバー時刻 — CalendarValueHistoryはサーバー時刻を期待）
   datetime from_time = TimeCurrent() - LookbackDays * 86400;
   datetime to_time   = TimeCurrent() + ForwardDays * 86400;

   // カレンダー値を取得
   MqlCalendarValue values[];
   int count = CalendarValueHistory(values, from_time, to_time);
   if(count <= 0)
     {
      PrintFormat("[CalendarExporter] イベントなし or エラー: %d",
                  GetLastError());
      return false;
     }

   // ファイルを開く（上書きモード、UTF-16→Python側でUTF-16対応）
   // FILE_UNICODE = UTF-16LE（MQL5デフォルト）でUnicode文字を正確に保存
   int handle = FileOpen(OutputFile,
                         FILE_WRITE | FILE_CSV | FILE_UNICODE,
                         ',');
   if(handle == INVALID_HANDLE)
     {
      PrintFormat("[CalendarExporter] ファイルオープン失敗: %s (%d)",
                  OutputFile, GetLastError());
      return false;
     }

   // ヘッダー行
   FileWrite(handle,
             "event_id",
             "event_time",
             "currency",
             "event_name",
             "impact",
             "actual",
             "forecast",
             "previous");

   int written = 0;
   for(int i = 0; i < count; i++)
     {
      // イベント情報を取得
      MqlCalendarEvent event_info;
      if(!CalendarEventById(values[i].event_id, event_info))
         continue;

      // 国情報から通貨を取得
      MqlCalendarCountry country_info;
      if(!CalendarCountryById(event_info.country_id, country_info))
         continue;

      // 通貨コードが空ならスキップ
      if(StringLen(country_info.currency) == 0)
         continue;

      // CSV行を書き出し
      string event_id_str   = IntegerToString(values[i].event_id);
      string event_time_str = DatetimeToUTCString(values[i].time);
      string currency_str   = country_info.currency;
      string name_str       = EscapeCSV(event_info.name);
      string impact_str     = ImpactToString(event_info.importance);
      string actual_str     = FormatValue(values[i].actual_value);
      string forecast_str   = FormatValue(values[i].forecast_value);
      string previous_str   = FormatValue(values[i].prev_value);

      FileWrite(handle,
                event_id_str,
                event_time_str,
                currency_str,
                name_str,
                impact_str,
                actual_str,
                forecast_str,
                previous_str);
      written++;
     }

   FileClose(handle);
   PrintFormat("[CalendarExporter] %d件をエクスポート (%s 〜 %s)",
               written,
               DatetimeToUTCString(from_time),
               DatetimeToUTCString(to_time));
   return true;
  }

//+------------------------------------------------------------------+
//| 次のHIGHイベントまでの秒数を返す（なければ INT_MAX）             |
//| 発表後2分以内のHIGHイベントも検出（actual値キャッチ用）          |
//+------------------------------------------------------------------+
int SecondsToNextHighEvent()
  {
   datetime now = TimeCurrent();
   datetime from = now - 120; // 過去2分も確認（発表直後キャッチ）
   datetime to = now + 3600;  // 1時間先まで確認
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

//+------------------------------------------------------------------+
//| サービスメイン関数                                                |
//+------------------------------------------------------------------+
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
//+------------------------------------------------------------------+
