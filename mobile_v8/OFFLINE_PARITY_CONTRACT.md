# Mobile V8 Offline-Parity Contract

## 목적

모바일 온디맨드 V8의 편의성을 높이되, 기존 오프라인 V8의 AI 분석용 원자료 수준을 낮추지 않는다. 이메일 본문은 모바일용 HTML 요약으로 사용하고, 첨부파일은 기존 8개 Markdown 자료층을 1개 Markdown으로 병합한다.

## 완료 기준

정상 완료(`PARITY_READY`)는 아래 핵심 증거층이 모두 충족될 때만 허용한다.

1. 기업 기본정보와 최근 4년 공시 목록이 존재한다.
2. 최근 8개 완료 분기의 동일 canonical scope 재무가 정확히 8개 존재한다.
3. 8Q 핵심값뿐 아니라 해당 분기의 전체 DART 계정 원자료, 계산 상태, 공식/receipt/source manifest를 보존한다.
4. 최종 8Q의 첫 분기 계산에 직전 Q1/H1/9M이 필요하면 해당 분기를 `Dependency inputs`로 추가 조회하고, 가용성·receipt·source manifest·required_by·reason을 보존한다. 필요한 dependency가 없으면 `PARITY_INCOMPLETE`다.
5. 최근 250거래일 OHLCV 원자료가 정확히 250개 존재하고 중복/비정상 OHLC 품질검사를 통과한다.
6. 기존 오프라인 `03_시장데이터_250D.md`의 CSV 구조를 축소하지 않는다. 열 순서는 `date,open,high,low,close,volume,trading_value`를 유지하며 원천값이 없으면 `MISSING`으로 남긴다.
7. 기존 오프라인 `04_시장데이터_설명.md`의 품질 구조를 축소하지 않는다. 최소한 `duplicate count`, `conflict count`, `OHLC warning count`, `quality`, `freshness` 필드를 유지하며 가용하지 않은 값은 `MISSING`으로 남긴다.
8. DART 최신 사업/반기/분기보고서 document index를 조회하고 원문 ZIP 내부 파일명과 source hash를 보존한다.
9. 증권사 리서치는 존재하지 않아도 정상이다. 존재하면 발행일/증권사/제목/상세 URL/PDF URL/다운로드 상태/hash/bytes를 보존하고 DART 사실과 구분한다.
10. `MISSING`과 `PARTIAL`을 추정값으로 대체하지 않는다.

리서치 부재는 과거 오프라인 패키지에서도 허용된 상태이므로 parity 실패 사유가 아니다. 반면 8Q, 필수 dependency, 250D 또는 핵심 DART 증거층이 부족하면 `PARITY_INCOMPLETE`로 처리한다.

## 실제 오프라인 세트 대조 기준

2026-09-07 생성 리노공업(058470) 오프라인 00~07 파일을 실제 기준자료로 대조했다.

- 오프라인 8파일은 요약본이 아니라 V8 evidence를 AI가 읽기 좋게 재포맷한 상세 분석세트다.
- 모바일은 파일 수만 8개에서 1개로 줄이고, 공시/8Q 전체 계정/250D/리서치/DART index 자료층은 유지한다.
- 오프라인 `06`도 XML/PDF 전문 dump는 하지 않고 periodic document index를 제공했다. 모바일은 동일 index에 실제 ZIP hash/bytes/내부 파일명까지 추가한다.
- 직접 대조 과정에서 모바일 03의 `trading_value` 열과 04의 `conflict count`/`freshness`가 빠진 것을 발견해 복구했다.
- 오프라인 02의 영업이익률·분기별 quality 집계처럼 원자료에서 재계산 가능한 파생 표시표를 byte-for-byte 복제하는 것은 parity의 필수조건이 아니다. 대신 모바일은 각 DART 계정의 standalone value, computation status, formula, receipt, scope, currency, source manifest를 직접 보존해 근거층을 유지하거나 강화한다.
- 따라서 parity는 화면 모양의 동일성이 아니라 원자료·provenance·quality evidence의 동급 이상 보존을 뜻한다.

## 통합 첨부 구조

과거의 파일 수만 줄이고 자료층의 순서는 유지한다.

- `00. 분석 인덱스와 데이터 품질`
- `01. 기업개요와 공시 상세`
- `02. 최근 8분기 재무 상세` — Canonical 8Q + 전체 계정 + Dependency inputs + Financial input selection
- `03. 최근 250거래일 시장데이터` — legacy CSV 열(`trading_value` 포함) 보존
- `04. 시장데이터 설명과 품질` — legacy quality 필드(`conflict count`, `freshness` 포함) 보존
- `05. 증권사 리서치 상세`
- `06. DART 정기보고서 원문 핵심자료`
- `07. ChatGPT 분석 요청`
- `부록 A. 기존 V8 / Python Strategy Engine 정량 결과`

## 이메일 형식

- 본문: 휴대폰 Gmail에서 바로 읽는 HTML 핵심 리포트
- 첨부: `{종목코드}_{종목명}_V8_ChatGPT통합원자료_{YYYYMMDD}.md` 1개
- PDF/XML/ZIP 원문 자체는 이메일에 중복 첨부하지 않는다. 대신 원문을 실제 조회해 파일 index, 가용성, hash, bytes 등 증거 메타데이터를 통합 Markdown에 보존한다.

## 변경 금지

이 parity layer는 기존 V8 분석/매수판단/Strategy Engine을 수정하지 않는다. 기존 V8은 계산 엔진으로 유지하고, 원자료 수집·품질·패키징은 `mobile_v8` wrapper에서 담당한다.

배포 전에는 이 계약을 기능적으로 확장하지 않는다. 새 요구가 생겨도 먼저 실제 모바일 Cloud Run end-to-end를 성공시킨 뒤 별도 변경으로 다룬다. 현재 기준에서 source-parity 범위는 동결한다.
