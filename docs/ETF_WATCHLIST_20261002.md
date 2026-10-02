# ETF 상시 감시판 — 2026-10-02

운영 시트: https://docs.google.com/spreadsheets/d/15WgSe4yOSBqSt6YTRQEETD_Hs6J1WL2Hop9RlzFCGkw/edit#gid=2026100201

- ETF_WATCHLIST 28종목, ETF_DATA 계산 보조 탭.
- 만료일/30일 삭제 없음. 감시 체크를 끄면 종목을 삭제하지 않고 감시만 중단.
- 현재가, 1·5·20거래일 가격 등락률, VWAP9/26 + OBV/OBV9 기술상태.
- 기존 REGIME/REGIME_DATA 수식을 재사용. 상승 빨강, 하락 파랑.
- 시세는 GOOGLEFINANCE 지연시세이며 분배금을 합산한 총수익률이 아님.
- 가격 흐름을 보는 목록이며 자금유입량/AUM 1위/80% 시장 설명력을 검증한 목록이 아님.
- 기존 보유종목 수량, 포트폴리오, 후보 TTL 및 트리거는 변경하지 않음.
- 각 ETF는 종목코드로 계산 데이터와 연결. 정렬해도 다른 ETF의 데이터와 혼합되지 않음.

## 자동 알림 연결 (설치 완료)

보유종목 시트의 확장 프로그램 → Apps Script에서 새 스크립트 파일을 추가하고
`apps_script/etf_watch_addon.gs` 전체를 붙여넣은 다음 `ewInstall`을 한 번 실행.
기존 Code.gs를 교체하지 않는다.

- ewInstall: ETF 5분 트리거 설치/교체, 이전 ewHourly 제거. 기존 hmTick5m 보존.
- ewPreview: 감시 개수/유효 시세/신호 후보만 조회. 메일 및 상태 변경 없음.
- ewTick5m: 평일 KST 09:00~15:30, 5분 간격 확인. △▲▽▼를 2회 연속 확인 후 발송. 중립은 시트 표시만. 동일 종목/신호 하루 1회, 같은 실행의 여러 종목은 한 통으로 묶음.
- 당일 시세이며 시세시각 기준 30분 이내인 VALID 데이터만 알림. 휴일/오래된 시세 제외. 발송 성공 후에만 상태 저장, 실패 시 재시도. 5분 확인은 GOOGLEFINANCE 실시간 수신을 의미하지 않음.
- 수신자: ETF_ALERT_EMAIL → 기존 HELD_ALERT_EMAIL → 실행 계정 이메일.
- ewUninstall: ETF 전용 트리거만 제거.
- 수동 주문/매수 판단은 사용자. Cloud Run/키움/DART 추가 호출 없음.

2026-10-02 수정: ETFWatch.gs를 5분 신호 감시로 변경. Node 검증: 장중 경계, 2회 확인, 중립 제외, 데이터 신선도, 일일 중복 억제, 트리거 이전, 발송 실패 재시도 통과.
운영 검증: ewInstall 완료. 트리거 UI에서 ewTick5m ‘5분마다’ 및 기존 hmTick5m 유지 확인. ewPreview: enabled=28, session=false, valid=0, alertCandidates=0 (장외, 오래된 시세 제외 정상). ewTick5m 수동 장외 실행 완료, 메일 없음.
실제 메일 전달 및 첫 장중 자연 실행은 아직 확인하지 않음.
Bound project: 1zXn_L305g2D687tdhPzFgghvZ8wNSEbPiZnnx3gAbtePgifIHY_cFgVG

## 유니버스

| 구분 | 영역 | 코드 | ETF |
|---|---|---|---|
| 국내 | 코스피 | 069500 | KODEX 200 |
| 국내 | 코스닥 | 229200 | KODEX 코스닥150 |
| 국내 | 반도체 | 091160 | KODEX 반도체 |
| 국내 | 2차전지 | 305720 | KODEX 2차전지산업 |
| 국내 | 바이오 | 244580 | KODEX 바이오 |
| 국내 | 자동차 | 138540 | TIGER 현대차그룹플러스 |
| 국내 | 조선 | 466920 | SOL 조선TOP3플러스 |
| 국내 | 방산 | 449450 | PLUS K방산 |
| 국내 | 전력기기 | 487240 | KODEX AI전력핵심설비 |
| 국내 | 원전 | 434730 | HANARO 원자력iSelect |
| 국내 | 고배당 | 161510 | PLUS 고배당주 |
| 국내 | 로봇 | 445290 | KODEX 로봇액티브 |
| 국내 | 인터넷 | 157490 | TIGER 소프트웨어 |
| 해외 | 미국S&P500 | 360750 | TIGER 미국S&P500 |
| 해외 | 미국나스닥100 | 133690 | TIGER 미국나스닥100 |
| 해외 | 미국빅테크 | 381170 | TIGER 미국테크TOP10 INDXX |
| 해외 | 미국반도체 | 381180 | TIGER 미국필라델피아반도체나스닥 |
| 해외 | 미국전력 | 487230 | KODEX 미국AI전력핵심인프라 |
| 해외 | 비만치료제 | 476070 | KODEX 글로벌비만치료제TOP2 Plus |
| 해외 | 인도 | 453870 | TIGER 인도니프티50 |
| 해외 | 일본 | 195920 | TIGER 일본TOPIX(합성 H) |
| 대체 | 금현물 | 411060 | ACE KRX금현물 |
| 인컴 | 미국배당 | 458730 | TIGER 미국배당다우존스 |
| 인컴 | AI커버드콜 | 490590 | RISE 미국AI밸류체인데일리고정커버드콜 |
| 현금 | CD금리 | 357870 | TIGER CD금리투자KIS(합성) |
| 해외 | 중국테크 | 371160 | TIGER 차이나항셍테크 |
| 대체 | 원유선물 | 261220 | KODEX WTI원유선물(H) |
| 인컴 | 리츠 | 329200 | TIGER 리츠부동산인프라 |

코드/현행 명칭 확인: 네이버 공개 ETF 목록 및 운용사 상품 페이지.
재생성: `python tools/build_etf_watch_sheet.py`는 Sheets batchUpdate 요청 JSON을 출력하며 외부 변경을 실행하지 않는다. 기존 ETF 탭이 있는 시트에는 addSheet 요청을 재실행하지 않는다.
