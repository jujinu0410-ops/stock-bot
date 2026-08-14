# -*- coding: utf-8 -*-
import sys
import pandas as pd

file_path = 'logs/stock_analysis_20260814_202916.xlsx'
xl = pd.ExcelFile(file_path)

print('=== [Sheet 1 보유종목_정밀평가] ===')
df1 = xl.parse(0)
for idx, r in df1.iterrows():
    code = str(r.iloc[1]).zfill(6)
    name = r.iloc[2]
    mode = r.iloc[5]
    init_stop = r.iloc[31]
    conf_stop = r.iloc[34]
    target_raw = r.iloc[36]
    target_tick = r.iloc[37]
    act_status = r.iloc[38]
    trail_delta = r.iloc[39]
    trail_line = r.iloc[40]
    budget = r.iloc[43]
    target_qty = r.iloc[44]
    excess = r.iloc[47]
    cmd = r.iloc[48]
    qty = r.iloc[49]
    manual = r.iloc[50]
    print(f"{code} | {name} | 모드:{mode} | 초기손절:{init_stop} | 확정손절:{conf_stop} | 원시활성:{target_raw} | 최종활성:{target_tick} | 활성상태:{act_status} | 트레일폭:{trail_delta} | 트레일선:{trail_line} | 위험목표:{target_qty} | 권고:{cmd} ({qty}주) | 수동:{manual}")
