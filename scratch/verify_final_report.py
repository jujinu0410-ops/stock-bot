import sys
import pandas as pd

file_path = 'logs/stock_analysis_20260814_201728.xlsx'
xl = pd.ExcelFile(file_path)

print('=== [Sheet 1 보유종목_정밀평가] ===')
df1 = xl.parse(0)
for idx, r in df1.iterrows():
    # 0: 순위, 1: 종목코드, 2: 종목명, 5: 매매모드, 31: 초기손절가, 34: 금일확정손절가, 36: 원시활성가, 37: 최종활성가, 38: 활성여부, 39: 트레일폭, 40: 트레일선, 43: 계좌위험예산, 44: 위험목표수량, 47: 20%초과수량, 48: 권고주문방향, 49: 실제권고수량, 50: 수동주문
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

print('\n=== [Sheet 2 DART_실제재무분석] ===')
df2 = xl.parse(1)
for idx, r in df2.iterrows():
    code = str(r.iloc[0]).zfill(6)
    name = r.iloc[1]
    fs = r.iloc[4]
    rev = r.iloc[5]
    op = r.iloc[8]
    st = r.iloc[13]
    print(f"{code} | {name} | 구분:{fs} | 매출:{rev:,}원 | 영업익:{op:,}원 | 상태:{st}")

print('\n=== [Sheet 3 전체종목_분석요약] ===')
df3 = xl.parse(2)
for idx, r in df3.iterrows():
    code = str(r.iloc[0]).zfill(6)
    name = r.iloc[1]
    f_sc = r.iloc[4]
    t_sc = r.iloc[10]
    v4 = r.iloc[12]
    print(f"{code} | {name} | F점수:{f_sc} | T점수:{t_sc} | V4연동:{v4}")
