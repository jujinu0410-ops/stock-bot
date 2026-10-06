from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any, Iterable


def _v(value: Any) -> str:
    if value is None or value == "":
        return "MISSING"
    return str(value).replace("\n", " ").replace("|", r"\|")


def _raw(value: Any) -> str:
    if value is None or value == "":
        return "MISSING"
    return str(value)


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {k: _jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _table(headers: list[str], rows: Iterable[Iterable[Any]]) -> list[str]:
    out = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    for row in rows:
        out.append("| " + " | ".join(_v(x) for x in row) + " |")
    return out


def _append_kv(lines: list[str], values: Iterable[tuple[str, Any]]) -> None:
    for key, value in values:
        lines.append(f"- {key}: {_v(value)}")


def render_chatgpt_markdown(
    *,
    evidence: dict[str, Any],
    bundle: Any,
    strategy: Any,
    generated_at: datetime,
) -> str:
    company = evidence["company"]
    disclosures = evidence["disclosures"]
    financials = evidence["financials"]
    market = evidence["market"]
    research = evidence["research"]
    periodic = evidence["periodic_documents"]

    lines: list[str] = [
        f"# {evidence['stock_code']}_{evidence['stock_name']} V8 ChatGPT 통합원자료",
        "",
        "> 기존 오프라인 V8의 00~07 AI 분석용 자료층을 파일 수만 1개로 병합한 자료입니다.",
        "> DART 사실자료, 네이버 시장원자료, 증권사 리서치 의견을 구분하며 MISSING/PARTIAL을 추정으로 채우지 않습니다.",
        "",
        "# 00. 분석 인덱스와 데이터 품질",
        "",
    ]
    _append_kv(lines, [
        ("회사", evidence["stock_name"]),
        ("종목코드", evidence["stock_code"]),
        ("corp_code", evidence["corp_code"]),
        ("생성시각", evidence["generated_at"]),
        ("offline parity status", evidence["status"]),
        ("parity issues", ", ".join(evidence["issues"]) if evidence["issues"] else "NONE"),
        ("canonical scope", financials["canonical_scope"]),
        ("financial profile", financials.get("financial_profile", "GENERAL")),
        ("8Q quarter count", financials["quarter_count"]),
        ("8Q status", financials["status"]),
        ("공시 수", disclosures["count"]),
        ("공시 수집범위", f"{disclosures['begin_date']} ~ {disclosures['end_date']}"),
        ("시장데이터 candle count", market["count"]),
        ("시장데이터 quality", market["quality"]),
        ("시장데이터 최신일", market["latest"]),
        ("리서치 상태", research.get("quality_status", "MISSING")),
        ("리서치 건수", research.get("report_count", 0)),
        ("DART 정기보고서 다운로드", f"{periodic.get('downloaded_count', 0)}/{periodic.get('document_count', 0)}"),
    ])
    lines += [
        "",
        "## 패키지 가용성",
        "",
    ]
    lines += _table(
        ["자료층", "상태", "건수/비고"],
        [
            ("01 기업개요/공시", "AVAILABLE" if disclosures["count"] else "NOT_AVAILABLE", disclosures["count"]),
            ("02 재무8Q", financials["status"], financials["quarter_count"]),
            ("03 시장250D", market["quality"], market["count"]),
            ("05 증권사리서치", research.get("quality_status", "NOT_AVAILABLE"), research.get("reason", "MISSING")),
            ("06 DART 원문 index", "AVAILABLE" if periodic.get("downloaded_count") == 3 else "PARTIAL", periodic.get("downloaded_count", 0)),
        ],
    )
    lines += [
        "",
        "## Source manifest 요약",
        "",
    ]
    _append_kv(lines, [
        ("corpCode.xml", evidence.get("corp_map_manifest")),
        ("company.json", company.get("source_manifest")),
        ("공시 list.json pages", ", ".join(disclosures.get("source_manifests") or []) or "MISSING"),
        ("시장 원자료", market.get("source_manifest")),
        ("리서치 목록", research.get("source_manifest", "MISSING")),
    ])

    lines += [
        "",
        "# 01. 기업개요와 공시 상세",
        "",
        "## 기업 기본정보",
        "",
    ]
    for key in (
        "corp_name", "corp_name_eng", "corp_code", "stock_code", "ceo_nm", "corp_cls",
        "est_dt", "jurir_no", "bizr_no", "adres", "hm_url", "ir_url", "phn_no", "fax_no",
        "induty_code", "acc_mt", "source_manifest",
    ):
        lines.append(f"- {key}: {_v(company.get(key))}")

    lines += [
        "",
        f"## 확보된 공시 목록 전체 ({disclosures['count']}건)",
        "",
    ]
    lines += _table(
        ["공시일", "공시명", "receipt", "report type", "amendment state", "제출인", "비고", "source manifest"],
        (
            (
                row.get("rcept_dt"), row.get("report_nm"), row.get("rcept_no"),
                row.get("report_type"), row.get("amendment_state"), row.get("flr_nm"),
                row.get("rm"), row.get("source_manifest"),
            )
            for row in disclosures["rows"]
        ),
    )

    lines += [
        "",
        "# 02. 최근 8분기 재무 상세",
        "",
    ]
    _append_kv(lines, [
        ("canonical scope", financials["canonical_scope"]),
        ("status", financials["status"]),
        ("quarter count", financials["quarter_count"]),
        ("quality warnings", "; ".join(financials.get("warnings") or []) or "NONE"),
    ])
    lines += [
        "",
        "## Canonical 최근 8분기",
        "",
    ]
    if financials.get("financial_profile") == "BANK_HOLDING":
        lines += [
            "> BANK_HOLDING 프로파일: 금융지주는 제조업형 단일 Revenue 계정을 필수값으로 보지 않습니다.",
            "> 핵심 검증은 영업이익·당기순이익·총자산·자기자본을 사용하며 OCF는 참고값입니다.",
            "",
        ]
    lines += _table(
        ["분기", "매출", "영업이익", "당기순이익", "영업현금흐름", "자산", "부채", "자본", "quality", "scope", "receipt", "manifest"],
        (
            (
                q.get("quarter"), q.get("revenue"), q.get("operating_income"), q.get("profit_loss"),
                q.get("operating_cash_flow"), q.get("assets"), q.get("liabilities"), q.get("equity"),
                q.get("quality"), q.get("scope"), q.get("receipt"), q.get("source_manifest"),
            )
            for q in financials["quarters"]
        ),
    )
    lines += [
        "",
        "## Dependency inputs",
        "",
    ]
    dependencies = financials.get("dependencies") or []
    if dependencies:
        lines += _table(
            ["scope", "fiscal year", "report code", "label", "dependency only", "required by", "reason", "status", "receipt", "manifest"],
            (
                (
                    dep.get("scope"), dep.get("fiscal_year"), dep.get("report_code"), dep.get("label"),
                    dep.get("dependency_only"), dep.get("required_by"), dep.get("reason"), dep.get("status"),
                    dep.get("receipt"), dep.get("source_manifest"),
                )
                for dep in dependencies
            ),
        )
    else:
        lines.append("- NONE")

    lines += [
        "",
        "## Financial input selection",
        "",
    ]
    _append_kv(lines, [
        ("selection_reason", financials.get("selection_reason")),
        ("available_report_count", financials.get("available_report_count")),
        ("selected_normalized_manifest_ids", ", ".join(financials.get("selected_manifests") or []) or "MISSING"),
        ("canonical_scope", financials.get("canonical_scope")),
    ])

    for report in financials["reports"]:
        lines += [
            "",
            f"## {report['year']}-{report['quarter']} 전체 DART 계정 상세",
            "",
        ]
        _append_kv(lines, [
            ("report code", report["report_code"]),
            ("receipt", report["receipt"]),
            ("scope", report["scope"]),
            ("source manifest", report["source_manifest"]),
            ("raw row count", len(report["raw_items"])),
        ])
        lines += [
            "",
            "### 계산된 standalone 계정",
            "",
        ]
        lines += _table(
            [
                "statement", "account id", "account name", "account detail", "raw current",
                "raw cumulative", "standalone value", "status", "formula", "receipt",
                "scope", "currency", "source manifest",
            ],
            (
                (
                    row.get("statement"), row.get("account_id"), row.get("account_name"), row.get("account_detail"),
                    row.get("raw_current"), row.get("raw_cumulative"), row.get("standalone_value"),
                    row.get("computation_status"), row.get("formula"), row.get("receipt"), row.get("scope"),
                    row.get("currency"), row.get("source_manifest"),
                )
                for row in report["rows"]
            ),
        )

    lines += [
        "",
        "## 계산 semantics",
        "",
        "- IS/CIS Q1: 당기 또는 누적 current amount를 standalone으로 사용",
        "- IS/CIS Q2/Q3: DART가 3개월 당기값과 누적값을 함께 제공하면 3개월 당기값을 우선 사용",
        "- IS/CIS Q4: FY 누적 - Q3 9M 누적을 우선 적용",
        "- CF: 누적값에서 직전 누적값을 차감해 standalone 분기값 계산",
        "- BS/STOCK: 해당 분기말 period-end 값",
        "- 최종 8Q 밖의 직전 분기가 de-cumulation에 필요하면 Dependency inputs로 별도 보존",
        "- 계산할 수 없는 계정은 MISSING/ACCOUNT_MAPPING_UNRESOLVED로 유지",
        "",
        "# 03. 최근 250거래일 시장데이터",
        "",
        f"- 행 수: {market['count']}",
        "- 정렬: 오래된 날짜 → 최신 날짜",
        "- 기술지표 계산용 OHLCV 원자료",
        "```csv",
        "date,open,high,low,close,volume",
    ]
    for candle in market["candles"]:
        lines.append(",".join(_raw(candle.get(key)) for key in ("date", "open", "high", "low", "close", "volume")))
    lines += [
        "```",
        "",
        "# 04. 시장데이터 설명과 품질",
        "",
    ]
    _append_kv(lines, [
        ("source", market["source"]),
        ("source manifest", market["source_manifest"]),
        ("earliest trading date", market["earliest"]),
        ("latest trading date", market["latest"]),
        ("candle count", market["count"]),
        ("duplicate count", market["duplicate_count"]),
        ("OHLC warning count", market["ohlc_warning_count"]),
        ("quality", market["quality"]),
    ])
    lines += [
        "",
        "# 05. 증권사 리서치 상세",
        "",
        "외부 증권사 분석자료와 의견이며 DART 사실자료와 구분해야 합니다.",
        "",
    ]
    _append_kv(lines, [
        ("quality_status", research.get("quality_status")),
        ("reason", research.get("reason")),
        ("report_count", research.get("report_count", 0)),
        ("failed_report_count", research.get("failed_report_count", 0)),
        ("source_manifest", research.get("source_manifest", "MISSING")),
    ])
    lines += [
        "",
        "## 확보된 리포트 metadata 전체",
        "",
    ]
    reports = research.get("reports") or []
    if reports:
        lines += _table(
            ["날짜", "증권사", "제목", "detail status", "상세 URL", "PDF status", "PDF URL", "PDF hash", "PDF bytes"],
            (
                (
                    report.get("date"), report.get("broker"), report.get("title"), report.get("detail_status"), report.get("detail_url"),
                    report.get("pdf_status"), report.get("pdf_url"), report.get("pdf_hash"), report.get("pdf_bytes"),
                )
                for report in reports
            ),
        )
    else:
        lines.append("- NOT_AVAILABLE / NO_REPORTS_FOUND")

    lines += [
        "",
        "# 06. DART 정기보고서 원문 핵심자료",
        "",
        "## 최신 사업/반기/분기보고서 document index",
        "",
    ]
    lines += _table(
        ["종류", "보고서명", "receipt", "filing date", "download status", "ZIP hash", "ZIP bytes", "원문 파일 목록"],
        (
            (
                doc.get("kind"), doc.get("report_nm"), doc.get("receipt"), doc.get("filing_date"),
                doc.get("download_status"), doc.get("zip_hash"), doc.get("zip_bytes"),
                "<br>".join(doc.get("files") or []) or "MISSING",
            )
            for doc in periodic["documents"]
        ),
    )
    lines += [
        "",
        "- XML/ZIP 원문 자체는 이메일에 중복 첨부하지 않습니다.",
        "- 실제 DART document.xml을 조회한 뒤 ZIP hash, bytes, 내부 파일 목록을 보존합니다.",
        "",
        "# 07. ChatGPT 분석 요청",
        "",
        "## 분석 우선순위",
        "",
        "1. 00 데이터 품질과 최신성",
        "2. 02 최근 8Q 재무와 전체 계정 상세",
        "3. 01 기업개요/공시와 정정 상태",
        "4. 03~04 250D 시장원자료와 품질",
        "5. 05 증권사 리서치(의견 자료)",
        "6. 06 DART 정기보고서 원문 index",
        "7. 부록 A의 기존 V8 및 Strategy Engine 결과",
        "",
        "## 분석 항목",
        "",
        "1. 최근 8Q 매출·영업이익·순이익·영업현금흐름 추세",
        "2. 재무상태와 현금흐름의 질",
        "3. 최근 주요 공시 및 정정/희석/수주/투자 위험",
        "4. 실적 개선·악화 요인",
        "5. 증권사 전망의 공통점과 차이(사실과 의견 분리)",
        "6. 250D 가격·거래량·추세 위치",
        "7. V8 산업/기술/수주 판단과 원자료의 일치 여부",
        "8. 현재 가격에서 기대수익과 하방위험 비교",
        "",
        "## 원칙",
        "",
        "- MISSING 값을 추정하지 마십시오.",
        "- 증권사 의견을 사실로 취급하지 마십시오.",
        "- DART 사실과 Research 의견을 구분하십시오.",
        "- V8 계산값도 원자료와 교차검증하십시오.",
        "- 기대수익보다 위험이 크면 매수를 권하지 마십시오.",
        "- 최종 결론은 매수/대기/제외 중 하나로 명확히 제시하십시오.",
        "",
        "# 부록 A. 기존 V8 / Python Strategy Engine 정량 결과",
        "",
        "## V8BriefingBundle 전체 직렬화",
        "```json",
        json.dumps(_jsonable(bundle), ensure_ascii=False, indent=2, default=str),
        "```",
        "",
        "## Python Strategy Engine 전체 직렬화",
        "```json",
        json.dumps(_jsonable(strategy), ensure_ascii=False, indent=2, default=str),
        "```",
        "",
        f"- 통합파일 생성시각: {generated_at.isoformat()}",
        f"- offline parity status: {evidence['status']}",
        "",
    ]
    return "\n".join(lines)
