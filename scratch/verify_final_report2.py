# -*- coding: utf-8 -*-
import sys
import pandas as pd

file_path = 'logs/stock_analysis_20260814_204035.xlsx'
xl = pd.ExcelFile(file_path)

print('=== [Sheet 1 보유종목_정밀평가] ===')
df1 = xl.parse('보유종목_정밀평가', skiprows=1)
for idx, r in df1.iterrows():
    code = str(r['종목코드']).zfill(6)
    name = r['종목명']
    mode = r['매매모드']
    rank = r['순위']
    t_sc = r['기술 T점수 (100점 만점)']
    final_sc = r['종합점수']
    prev_stop = r['전일확정 손절가(원)']
    conf_stop = r['금일확정 손절가(원)']
    stop_up = r['손절선 갱신상태']
    act_tgt = r['최종 익절 트레일링 활성가(원)']
    trail_delta = r['익절 트레일링폭(원)']
    data_val = r['데이터 유효성']
    comp = r['데이터완성도(%)']
    print(f"{code} | {name} | 순위:{rank} | 모드:{mode} | T점수:{t_sc} | 종합:{final_sc} | 전일손절:{prev_stop} | 금일손절:{conf_stop} | 갱신:{stop_up} | 활성가:{act_tgt} | 트레일폭:{trail_delta} | 유효성:{data_val} | 완성도:{comp}")

print('\n=== [Sheet 3 전체종목_분석요약] (자이글 발췌) ===')
df3 = xl.parse('전체종목_분석요약', skiprows=1)
zaigle_r = df3[df3['종목코드'].astype(str).str.zfill(6) == '234920']
for idx, r in zaigle_r.iterrows():
    print(r.to_dict())
