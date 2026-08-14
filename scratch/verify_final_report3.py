# -*- coding: utf-8 -*-
import sys
import pandas as pd

file_path = 'logs/stock_analysis_20260814_204920.xlsx'
xl = pd.ExcelFile(file_path)

print('=== [Sheet 2 DART_실제재무분석 종목 목록] ===')
df2 = xl.parse('DART_실제재무분석', skiprows=1)
for idx, r in df2.iterrows():
    print(f"{r['종목코드']} | {r['종목명']} | {r['공시연도']}년 {r['보고서코드']} {r['공시구분(CFS/OFS)']} | 매출:{r['당일 매출액(원)' if '당일 매출액(원)' in r else '당기 매출액(원)']:,}원 | 영업익:{r['당기 영업이익(원)']:,}원")

print('\n=== [Sheet 1 보유종목_정밀평가 자이글 행] ===')
df1 = xl.parse('보유종목_정밀평가', skiprows=1)
zaigle_r = df1[df1['종목코드'].astype(str).str.zfill(6) == '234920']
for idx, r in zaigle_r.iterrows():
    print(f"순위: {r['순위']} | 모드: {r['매매모드']} | T점수: {r['기술 T점수 (100점 만점)']} | 전략: {r['실전 대응 전략']} | 완성도: {r['데이터완성도(%)']}")
