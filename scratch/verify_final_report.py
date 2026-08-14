# -*- coding: utf-8 -*-
import sys
import pandas as pd
sys.stdout.reconfigure(encoding='utf-8')

xl = pd.ExcelFile('logs/stock_analysis_20260814_200258.xlsx')

print('=== [Sheet 1 보유종목_정밀평가] ===')
df1 = xl.parse(0)
for idx, r in df1.iterrows():
    code = str(r.iloc[1]).zfill(6)
    name = r.iloc[2]
    init_stop = r.iloc[32]
    conf_stop = r.iloc[35]
    target = r.iloc[38]
    budget = r.iloc[44]
    excess = r.iloc[48]
    cmd = r.iloc[49]
    qty = r.iloc[50]
    print(f"{code} | {name} | 초기손절:{init_stop} | 확정손절:{conf_stop} | 익절:{target} | 위험예산:{budget} | 20%초과:{excess} | 권고:{cmd} ({qty}주)")

print('\n=== [Sheet 2 DART_실제재무분석] ===')
df2 = xl.parse(1)
for idx, r in df2.iterrows():
    code = str(r.iloc[0]).zfill(6)
    name = r.iloc[1]
    if code in ('219550', '234920') or '자이글' in str(name) or '디와이디' in str(name):
        fs = r.iloc[4]
        rev = r.iloc[5]
        op = r.iloc[8]
        st = r.iloc[13]
        print(f"{code} | {name} | 구분:{fs} | 매출:{rev} | 영업익:{op} | 검증:{st}")

print('\n=== [Sheet 3 전체종목_분석요약] ===')
df3 = xl.parse(2)
for idx, r in df3.iterrows():
    code = str(r.iloc[0]).zfill(6)
    name = r.iloc[1]
    if code in ('219550', '234920') or '자이글' in str(name) or '디와이디' in str(name):
        fsc = r.iloc[4]
        tsc = r.iloc[10]
        v4 = r.iloc[12]
        print(f"{code} | {name} | F점수:{fsc} | T점수:{tsc} | V4연동:{v4}")
