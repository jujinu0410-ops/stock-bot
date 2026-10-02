# 보유종목 메일 정책 — 2026-10-02

사용자 합의: 관찰·일반 변화 제외, 매수조짐·매수확인 및 하락·매도·손절 위험 경고 유지.

운영 Apps Script 프로젝트 `1zXn_L305g2D687tdhPzFgghvZ8wNSEbPiZnnx3gAbtePgifIHY_cFgVG`의 `HeldMonitor5m_v1.gs` 내용을 `apps_script/held_monitor_5m_v3.gs`와 일치시킴. 새 스크립트 파일로 중복 추가하지 않는다.

메일 허용: SIGNAL_EARLY_UP, SIGNAL_CONF_UP, BASE_PAIR_UP, SIGNAL_EARLY_DOWN, SIGNAL_CONF_DOWN, BASE_PAIR_DOWN, ATR_DOWN_1_0, STOP_BREACH, STOP_BREACH_INITIAL.
메일 제외: 관찰·정보·단순 상승 ATR·손절선 회복·보조 크로스 및 기타 허용 목록 밖 사건. WATCH 등급 전체를 제외하면 매수조짐까지 빠지므로 사건 키로 필터한다.

필터는 경보 판정 결과와 메일 발송 함수에 모두 적용. 시트 국면 계산·상태 기록은 유지. 기존 2회 확인, 하루 중복 억제, 손절 30분 cooldown, 같은 실행 다종목 1통 묶음 유지. gate version 3을 유지하여 기존 상태를 초기화하지 않음. 기존 hmTick5m 트리거 그대로 사용, 재설치 불필요.
ETF는 이전에 설치한 ewTick5m 그대로: 중립 제외, △▲▽▼만 2회 확인 후 알림. 이번 작업에서 ETF 코드/시트/예약은 변경하지 않음.

검증: Node 테스트로 관찰 무발송, 매수조짐·확인 유지, 하락/손절 유지, 일일 중복 억제, 손절 cooldown, 배치 내 관찰 제외, 무발송 미리보기 통과. `hmPreviewMailPolicy`는 실제 메일 및 시트 변경 없이 운영 코드의 필터를 검증한다.
실제 장중 자연 실행과 메일 수신은 다음 거래일 확인 대상.
