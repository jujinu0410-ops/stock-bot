# 2차-A 표시 전용 설계

실제 운영 기준: service signalfix3-20261106/e6fea25f…, runner market-briefing-runner/f2524921… 및 원격 Apps 16파일. 후속 시장 브리핑 변경도 보존한다. GitHub 실제 main=5268e36; 보고된 ae77c3d는 원격/기존 사본에서 아직 확인되지 않았으므로 이를 기준으로 덮어쓰지 않는다.

## 기존 생성 경로

- services/unified_scan_m30.daily_states → 전일까지 완성 일봉 정식 technical + 당일 provisional; 기존 BUY/DOWN/위험 우선순위 유지.
- scan의 30분봉: 완성 원천 검증 후 기존 indicators의 vwap9_dir RISING/FALLING/FLAT, gold는 VWAP9/VWAP26 위치 관계. 정배열 자체를 가격 상승으로 해석하지 않는다.
- Apps UnifiedWatchlists30의 umPlan/umSignalBrief/umMessage가 실제 통합 메일 표시 경로. 기계 stage, events/reasons, send_allowed, count/sent, eventId 및 plain machine footer 불변.
- 5분은 umTick5mM30의 평가 주기. 기존 실시간 1분봉 guard를 유지하며 새 5분봉 지표/필터 없음. HeldMonitor의 ACTIVE 경로도 coordinator에 위임하므로 이번에 독립 감시 코드는 수정하지 않는다.
- runner는 최신 main → Gmail V2이며, 실제 Job STOCKBOT_REPORT_PROFILE=MARKET_BRIEFING을 확인했다. 현재 정규 출력은 시장지수·수급·계좌 요약이고 BUY/DOWN 통합신호 문구를 표시하지 않는다. 별도 메일 시스템을 보호하기 위해 runner·Cloud Run 서비스는 모두 미수정한다.

## 분류와 표시

- 확실한 일봉 상승: BULL_CONFIRMED, PULLBACK_IN_UPREGIME. 확실한 하락: BEAR_CONFIRMED, BOUNCE_IN_DOWNREGIME, OBV_REBOUND_BASE_DOWN. EARLY_IMPROVEMENT/WEAKENING 및 나머지는 상위 추세 미확정. basis=COMPLETED_DAILY 증거가 있어야 정식 레짐으로 인정.
- 30분 방향은 기존 vwap9_dir만 연결한다. available, 원천, 당일 canonical 완성봉 시각을 확인하고 결측/미래/오래된 봉은 UNKNOWN. gold 정배열과 방향은 별개 필드.
- 다섯 관계를 display-only JSON에 분류한다. 동일 방향은 기존 간결한 본문을 유지하고, 반대 방향/미확정 매수/위험+상승 때만 짧은 보조문구를 붙인다. 위험 문구가 항상 앞서며 반등을 위험 해제로 표현하지 않는다.
- Apps 상태에 directionContext를 부가 보관한다. Cloud Run API가 이미 제공하는 technical/m30 필드만 사용하며 새 조회·새 필터·DB 상태 변경은 없다. 과거 신호의 메시지를 현재 방향으로 다시 해석하지 않는다.
- service 계산/배치 API, 매수·매도 판단값, 위험·ATR 임계값, A~D/E30, 발송·Job·예약 설정은 변경하지 않는다.

## 검증 및 배포

사용자 지정10조건, 과거 상태·이벤트·중복발송 이력 동일성, 실제 Apps mail builder 및 현재 Cloud Run 출력 불변성을 시험한다. 전체 회귀 성공 후에만 원본 Apps 프로젝트 표시 파일1개를 적용한다. 서비스 트래픽은 현 리비전100%, runner 이미지·Job·예약 모두 유지. 새 정상 장중 신호는 다음 거래일 실측 대상으로 구분하며 수동 시험 메일/주문은 호출하지 않는다.
