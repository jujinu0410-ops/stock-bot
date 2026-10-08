# 차트분석남 08:50 전용 클리핑 (운영 배포 전)

## 흐름

- 기존 Apps Script ycScheduledMonitor 5분 트리거에서 08:50~09:59 KST 동안 확인 (09:46 지연 게시도 포함).
- YouTube /posts 중 당일 KST 게시된 '오늘의 예상 대장테마 · 종목 (M/D)'만 추출. 장중/마감 및 IPO 제외.
- '→ 종목명 · 종목명' 및 '· 종목명(6자리)'에서 직접 언급 종목을 추출. 네이버 자동완성 또는 상세 API로 **이름과 6자리 KOSPI/KOSDAQ 코드가 정확히 맞는 경우만** 수용.
- CHARTNAM_CLIPS 탭에 Date, Code, Name, Theme, Reason, PublishedKST, PostId, Source, ClippedKST를 날짜+코드로 중복 없이 저장.
- 기존 Gmail 파서 결과에 전용 탭의 유효기간 내 종목을 합산한 뒤, 기존 refreshYouTubeCandidatePoolV2 함수만이 CANDIDATES를 갱신.
- 전체 언급종목 메일 마커, 신호 모니터링, 중복 메일 방지, 30일 후보 유효기간은 그대로 유지.

## 실패 및 재시도

- 08:50에 글이 아직 없으면 다음 5분 주기에 재확인. 09:59가 지나면 종료.
- 최초 성공 시 CHARTNAM_CLIPPED_YYYY-MM-DD = Y를 Script Property에 기록.
- 클립 성공 후 시트 후보군 갱신 실패 시 원천 시트에 남으므로 이후 정기 동기화에서 회복 가능.
- 잘못된 코드, 이름 불일치, 해외주식, 미상장은 CANDIDATES에 넣지 않음.
- 유튜브 외부 차단과 실제 Apps Script 호출 가능성, KRX 휴장일 제외는 운영 검증이 남음. 휴장일은 새 게시물이 없으면 변경 없음.

## 배포 주의

GitHub 푸시만으로 Apps Script 운영본이 바뀌지 않음. 실제 프로젝트의 Code.gs를 새 bootstrap으로 clasp push해야 08:50 자동 실행된다. 운영 Script ID가 확인되지 않아 현재 운영 시트/트리거는 변경하지 않음. 첫 거래일 시험 10월 12일 예정.

검증: node tests/test_chartnam_clipper.js
