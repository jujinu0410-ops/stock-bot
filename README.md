# 국내주식 단기·스윙 자동 분석 시스템 (Stock Analysis System)

키움 REST API 및 DART API 연동을 위한 SQLite 기반 분석 DB 스키마 및 기본/기술적 분석 엔진 모듈입니다.

## 1. 프로젝트 구조

```
stock_analysis_system/
├── config/
│   ├── __init__.py
│   └── settings.py          # DB 경로, 테이블/인덱스 스키마 정의
├── data/                    # SQLite 데이터베이스 (stock_system.db)
├── logs/                    # 시스템 및 수집/분석 로그 (stock_system.log)
├── src/
│   ├── __init__.py
│   ├── database/
│   │   ├── __init__.py
│   │   └── db_manager.py    # DB 초기화, 인덱스 생성, CRUD 및 예외/롤백 관리
│   ├── analysis/
│   │   ├── __init__.py
│   │   ├── technical_analysis.py   # 기술적 분석 (일목, VWAP, BB, DMI, OBV, Chaikin, 수급 연속성 -> T점수)
│   │   └── fundamental_analysis.py # 기본적 분석 (실적성장, 현금흐름, 수주, 재무안정성, 밸류 -> F점수 및 85% 완성도 검증)
│   └── utils/
│       ├── __init__.py
│       └── logger.py        # 로깅 설정 (콘솔 & 파일 출력)
├── main.py                  # 엔드투엔드 초기화 및 실행 검증 스크립트
└── README.md                # 사용 가이드
```

## 2. 주요 기능 및 스키마

### SQLite DB 테이블
1. `stock_info`: 종목코드, 종목명, 시장구분(KOSPI/KOSDAQ), 업종, 시가총액, 유통주식수
2. `dart_financials`: DART 분기별 재무제표 (매출액, 영업이익, 순이익, 영업현금흐름, 부채비율, 수주잔고, 완성도)
3. `kiwoom_daily`: 키움 일봉 및 외국인/기관 순매수 수급 데이터
4. `trading_signals`: F점수, T_raw, T환산점수, 1~3차 종합점수, 포지션 상태, 신호 유형 및 판단 사유
5. `portfolio_positions`: 포트폴리오 잔고, 평균단가, 허용손실액, 손절가, 익절가

### 점수 산출 로직
- **기술적 분석 (`TechnicalAnalysis`)**:
  - 7개 핵심 보조지표(일목 9/20/50, VWAP 9/26, BB 26/1.7, DMI/ADX 14, OBV, Chaikin 13/26, 3일 쌍쓸이 수급) 연산
  - 최근 3거래일 흐름 종합하여 `T_raw` (-100 ~ +100점) 산출
  - 공식 적용: `T = (T_raw + 100) / 2` (0 ~ 100점 변환)
  - 지표 누락 시 임의 점수 부여 금지 및 완성도 감점 처리
- **기본적 분석 (`FundamentalAnalysis`)**:
  - 6개 평가 범주(실적성장 25, 현금흐름 20, 수주/공시 20, 재무안정성 15, 밸류에이션 15, 지배구조 5) 총 100점 F점수
  - 데이터 완성도 공식: `(확인된 항목 배점 합계 / 100) * 100` (%)
  - **데이터 완성도 85% 미만 시 1차 정상 매수 금지 플래그 (`is_eligible_stage1 = False`) 반환**

## 3. 실행 방법

```bash
cd C:\Users\jooji\.gemini\antigravity\scratch\stock_analysis_system
python main.py
```
