P0：TP_EARLY を “刺さる時だけ”に限定（これが一番効く）

今は TP_EARLY が 多すぎて、伸びる玉まで刈っています。

おすすめの条件（過剰フィットしにくい順）：

遅い玉だけTP_EARLY

例：time_to_mfe_minutes >= 90（または holding>=90） の時だけ
※TP_EARLYは平均 time_to_mfe が約61分なので、90分でかなり絞れます

「伸びる兆候」が出たらTP_EARLY禁止

例：mfe_r >= 1.0 を一度でも満たしたら TP_EARLYしない
（伸びる玉はTRAIL/TP2へ）

TP_EARLYは“停滞系”の救済としてだけ使う

例：holding>=60 && mfe_r<0.8 のときだけ

目的は「TP_EARLYを0にする」ではなく、“刈りすぎ”だけ止めることです。

P0：DAY_TRADE×RANGEのSL平均が悪化しているので、軽い保険を追加

DAY_TRADE×RANGEの SL平均損失が少し重くなっています。
ここは 0.3R保険が効きます：

+0.3R到達 → SLを -0.1R まで引き上げ

+0.5R到達 → 15〜25%部分利確 → 残りBE