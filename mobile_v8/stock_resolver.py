from __future__ import annotations

import io
import json
import logging
import os
import re
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DART_BASE = "https://opendart.fss.or.kr/api"


class MappingConflictError(ValueError):
    """Raised when an internal alias, hardcoded map, or fallback conflicts with the canonical master."""
    pass


def normalize_lookup_key(text: str) -> str:
    """Normalize stock name or query string for robust dictionary matching.
    - Strips (주), ㈜, 주식회사 at edges
    - Lowercases ASCII
    - Removes all internal whitespaces
    """
    cleaned = (text or "").strip()
    cleaned = re.sub(r"(?:\(주\)|㈜)", "", cleaned).strip()
    cleaned = re.sub(r"^주식회사\s*", "", cleaned).strip()
    cleaned = re.sub(r"\s*주식회사$", "", cleaned).strip()
    return cleaned.replace(" ", "").lower()


# ---------------------------------------------------------------------------
# 1. Authoritative Core Verified Stock Master (fallback when offline / no DART)
# Strictly cross-verified against official DART CORPCODE filings.
# ---------------------------------------------------------------------------
CORE_VERIFIED_STOCKS: Dict[str, str] = {
    # (code: canonical_official_name)
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "005380": "현대자동차",
    "000270": "기아",
    "051910": "LG화학",
    "373220": "LG에너지솔루션",
    "207940": "삼성바이오로직스",
    "068270": "셀트리온",
    "196170": "알테오젠",
    "035720": "카카오",
    "035420": "NAVER",
    "012450": "한화에어로스페이스",
    "000490": "대동",
    "004960": "한신공영",
    "011700": "한신기계공업",
    "013030": "하이록코리아",
    "034020": "두산에너빌리티",
    "047050": "포스코인터내셔널",
    "047770": "코데즈컴바인",
    "055490": "테이팩스",
    "140670": "알에스오토메이션",
    "206650": "유바이오로직스",
    "234920": "자이글",
    "219550": "디와이디",
    "241520": "DSC인베스트먼트",
    "267260": "HD현대일렉트릭",
    "348340": "뉴로메카",
    # CRITICAL: 007660 is 이수페타시스, 006650 is 대한유화
    "007660": "이수페타시스",
    "006650": "대한유화",
    "036930": "주성엔지니어링",
    "010140": "삼성중공업",
    "011790": "SKC",
    "086520": "에코프로",
    "247540": "에코프로비엠",
    "003670": "포스코퓨처엠",
    "012330": "현대모비스",
    "105560": "KB금융",
    "055550": "신한지주",
    "005490": "POSCO홀딩스",
    "323410": "카카오뱅크",
    "259960": "크래프톤",
    "453450": "그리드위즈",
}

# Legacy compatibility mapping
CANONICAL_STOCK_MAP: Dict[str, Tuple[str, str]] = {
    code: (code, name) for code, name in CORE_VERIFIED_STOCKS.items()
}

# ---------------------------------------------------------------------------
# 2. ETF Master (Non-DART exchange-traded funds)
# ---------------------------------------------------------------------------
ETF_MASTER: Dict[str, str] = {
    "161510": "PLUS 고배당주",
    "371460": "TIGER 차이나전기차",
    "490590": "RISE 미국AI밸류체인",
}

# ---------------------------------------------------------------------------
# 3. Pure Alias Normalization Layer
# Maps colloquial or short aliases to canonical official company/ETF names.
# Aliases do NOT own 6-digit stock codes directly to prevent human error drift.
# ---------------------------------------------------------------------------
ALIAS_TO_CANONICAL_NAME: Dict[str, str] = {
    "삼바": "삼성바이오로직스",
    "네이버": "NAVER",
    "에스케이하이닉스": "SK하이닉스",
    "하이닉스": "SK하이닉스",
    "현대차": "현대자동차",
    "에스케이씨": "SKC",
    "포스코홀딩스": "POSCO홀딩스",
    "이수페타": "이수페타시스",
    "lg엔솔": "LG에너지솔루션",
    "엘지에너지솔루션": "LG에너지솔루션",
    "한신기계": "한신기계공업",
    "tiger차이나전기차": "TIGER 차이나전기차",
    "plus고배당주": "PLUS 고배당주",
    "rise미국ai밸류체인": "RISE 미국AI밸류체인",
    "rise미국ai밸류체인데일리고정커버드콜": "RISE 미국AI밸류체인",
    "대한유화공업": "대한유화",
}


class CanonicalMaster:
    """Encapsulates canonical mapping with fail-closed integrity checks."""

    def __init__(self) -> None:
        self.code_to_name: Dict[str, str] = {}
        self.norm_name_to_code: Dict[str, Tuple[str, str]] = {}

    def get_name_by_code(self, code: str) -> str:
        return self.code_to_name.get(code, "")

    def lookup_by_normalized_name(self, norm_name: str) -> Optional[Tuple[str, str]]:
        return self.norm_name_to_code.get(norm_name)

    def add_entry(
        self,
        code: str,
        name: str,
        *,
        source: str = "master",
        check_conflict: bool = True,
    ) -> None:
        cleaned_code = str(code).strip()
        cleaned_name = str(name).strip()
        if not re.fullmatch(r"\d{6}", cleaned_code) or not cleaned_name:
            return

        norm_key = normalize_lookup_key(cleaned_name)

        if check_conflict:
            # 1. Check if name already points to a different stock code
            if norm_key in self.norm_name_to_code:
                existing_code, existing_name = self.norm_name_to_code[norm_key]
                if existing_code != cleaned_code:
                    raise MappingConflictError(
                        f"MAPPING_CONFLICT:{cleaned_name}: proposed code {cleaned_code} ({source}) "
                        f"conflicts with canonical master code {existing_code} ({existing_name})"
                    )

            # 2. Check if code already points to a completely different company
            if cleaned_code in self.code_to_name:
                existing_name = self.code_to_name[cleaned_code]
                norm_existing = normalize_lookup_key(existing_name)
                # Allow minor variations (e.g. 현대차 vs 현대자동차, 한신기계 vs 한신기계공업)
                is_alias = (
                    norm_existing == norm_key
                    or ALIAS_TO_CANONICAL_NAME.get(norm_existing) == cleaned_name
                    or ALIAS_TO_CANONICAL_NAME.get(norm_key) == existing_name
                    or norm_key in norm_existing
                    or norm_existing in norm_key
                )
                if not is_alias:
                    raise MappingConflictError(
                        f"MAPPING_CONFLICT:{cleaned_code}: code already mapped to '{existing_name}', "
                        f"cannot map to conflicting company '{cleaned_name}' ({source})"
                    )

        self.code_to_name[cleaned_code] = cleaned_name
        self.norm_name_to_code[norm_key] = (cleaned_code, cleaned_name)
        # Also map without any affixes for search resilience
        raw_key = cleaned_name.replace(" ", "").lower()
        if raw_key not in self.norm_name_to_code:
            self.norm_name_to_code[raw_key] = (cleaned_code, cleaned_name)


_CANONICAL_MASTER_CACHE: Optional[CanonicalMaster] = None


def get_canonical_master(*, dart_api_key: Optional[str] = None, reload: bool = False) -> CanonicalMaster:
    """Build or retrieve the singleton CanonicalMaster instance.

    Priority of loading:
    1. Static DART master JSON (dart_stock_master.json, ~3,931 stocks)
    2. Built-in Core verified stocks & ETF master
    3. Portfolio holdings JSON from repo
    4. Live DART CORPCODE API if API key is provided and cache reload is requested
    """
    global _CANONICAL_MASTER_CACHE
    if _CANONICAL_MASTER_CACHE is not None and not reload:
        return _CANONICAL_MASTER_CACHE

    master = CanonicalMaster()

    # 1. Load static dart_stock_master.json if present
    master_json_path = Path(__file__).resolve().parent / "dart_stock_master.json"
    if master_json_path.is_file():
        try:
            data = json.loads(master_json_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for c, n in data.items():
                    master.add_entry(c, n, source="dart_stock_master.json", check_conflict=False)
                logger.debug(f"[stock_resolver] Loaded {len(data)} stocks from dart_stock_master.json")
        except Exception as e:
            logger.warning(f"[stock_resolver] Failed to load dart_stock_master.json: {e}")

    # 2. Merge core verified stocks (strict conflict check)
    for c, n in CORE_VERIFIED_STOCKS.items():
        master.add_entry(c, n, source="CORE_VERIFIED_STOCKS", check_conflict=True)

    # 3. Merge ETF master
    for c, n in ETF_MASTER.items():
        master.add_entry(c, n, source="ETF_MASTER", check_conflict=True)

    # 4. Merge portfolio_holdings.json if present
    repo_root = Path(__file__).resolve().parent.parent
    candidate_holdings = [
        repo_root / "github_discovery_lab" / "portfolio_holdings.json",
        repo_root / "stock_analysis_system" / "config" / "portfolio_holdings.json",
    ]
    for p in candidate_holdings:
        if p.is_file():
            try:
                h_data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(h_data, list):
                    for item in h_data:
                        c = str(item.get("stock_code") or "").strip()
                        n = str(item.get("stock_name") or "").strip()
                        if c and n:
                            master.add_entry(c, n, source=f"holdings:{p.name}", check_conflict=True)
            except Exception as e:
                logger.warning(f"[stock_resolver] Failed to load holdings from {p}: {e}")

    # 5. Live DART CORPCODE API if key is present
    api_key = dart_api_key or os.environ.get("DART_API_KEY") or ""
    if api_key and reload:
        try:
            import requests
            from bs4 import BeautifulSoup

            res = requests.get(f"{DART_BASE}/corpCode.xml", params={"crtfc_key": api_key}, timeout=30)
            if res.status_code == 200:
                with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
                    xml_bytes = zf.read("CORPCODE.xml")
                soup = BeautifulSoup(xml_bytes, "xml")
                for row in soup.find_all("list"):
                    stock = (row.stock_code.text if row.stock_code else "").strip()
                    corp = (row.corp_name.text if row.corp_name else "").strip()
                    if stock and corp and re.fullmatch(r"\d{6}", stock):
                        master.add_entry(stock, corp, source="live_dart_api", check_conflict=False)
        except Exception as e:
            logger.warning(f"[stock_resolver] Live DART refresh failed: {e}")

    _CANONICAL_MASTER_CACHE = master
    return master


def _fallback_krx_web_search(query: str) -> Optional[Tuple[str, str]]:
    """Fallback: KRX web lookup using FinanceDataReader or KRX listing if available."""
    try:
        import FinanceDataReader as fdr
        df = fdr.StockListing("KRX")
        norm_q = normalize_lookup_key(query)

        # 1. Exact match on normalized Name
        matched = df[df["Name"].astype(str).apply(normalize_lookup_key) == norm_q]
        if len(matched) > 0:
            code = str(matched["Code"].iloc[0]).zfill(6)
            name = str(matched["Name"].iloc[0]).strip()
            return code, name

        # 2. Contains match
        contains = df[df["Name"].astype(str).str.contains(query, case=False, na=False)]
        if len(contains) > 0:
            code = str(contains["Code"].iloc[0]).zfill(6)
            name = str(contains["Name"].iloc[0]).strip()
            return code, name
    except Exception as e:
        logger.warning(f"[stock_resolver] KRX web lookup failed/blocked: {e}")

    return None


def resolve_stock_input(
    stock_input: str,
    *,
    dart_api_key: Optional[str] = None,
) -> Tuple[str, str]:
    """Resolve user stock input into a guaranteed 6-digit stock code and canonical name.

    Resolution Priority Order:
    1. Direct 6-digit stock code input -> returns (code, canonical_name_or_empty)
    2. Exact canonical Korean stock name lookup against DART master & verified master
    3. Pure alias normalization layer -> resolves alias to canonical name, then resolves code
    4. KRX web search fallback (graceful on 403 / IP block, cross-checked for conflicts)
    5. Fail-closed: raises ValueError if no valid stock code can be resolved.
       Never returns unparsed text or corrupted strings like '????'.
    """
    raw = (stock_input or "").strip()
    if not raw:
        raise ValueError("종목명 또는 종목코드를 입력해 주세요.")

    # Guard against corrupted strings like '????'
    if re.fullmatch(r"\?+", raw):
        raise ValueError("유효하지 않은 입력값입니다: 인코딩 오류가 감지되었습니다.")

    master = get_canonical_master(dart_api_key=dart_api_key)

    # Priority 1: Direct 6-digit code
    if re.fullmatch(r"\d{6}", raw):
        canonical_name = master.get_name_by_code(raw)
        return raw, canonical_name

    norm_q = normalize_lookup_key(raw)
    if not norm_q:
        raise ValueError("종목명 또는 종목코드를 입력해 주세요.")

    # Priority 2: Exact match in Canonical Master (DART + ETF + Holdings)
    direct_match = master.lookup_by_normalized_name(norm_q)
    if direct_match is not None:
        return direct_match

    # Priority 3: Alias normalization to canonical name
    alias_target = ALIAS_TO_CANONICAL_NAME.get(norm_q)
    if alias_target:
        target_norm = normalize_lookup_key(alias_target)
        alias_match = master.lookup_by_normalized_name(target_norm)
        if alias_match is not None:
            return alias_match
        raw = alias_target

    # Priority 4: KRX web lookup fallback
    try:
        fallback_res = _fallback_krx_web_search(raw)
        if fallback_res is not None:
            code, name = fallback_res
            if re.fullmatch(r"\d{6}", code):
                # Verify no conflict with canonical master
                master.add_entry(code, name, source="krx_fallback", check_conflict=True)
                return code, name
    except MappingConflictError:
        raise
    except Exception as e:
        logger.warning(f"[stock_resolver] KRX fallback search exception: {e}")

    # Priority 5: Fail-closed with clean error
    raise ValueError(f"UNKNOWN_STOCK_INPUT:{raw}")


def audit_internal_mappings(master: Optional[CanonicalMaster] = None) -> List[str]:
    """Audit all internal aliases and hardcoded mappings for conflicts against canonical master."""
    if master is None:
        master = get_canonical_master()

    errors: List[str] = []

    # 1. Audit CORE_VERIFIED_STOCKS
    for code, expected_name in CORE_VERIFIED_STOCKS.items():
        master_name = master.get_name_by_code(code)
        if not master_name:
            errors.append(f"MISSING_CODE:{code}:{expected_name}")
            continue
        norm_expected = normalize_lookup_key(expected_name)
        norm_master = normalize_lookup_key(master_name)
        if norm_expected != norm_master and norm_expected not in norm_master and norm_master not in norm_expected:
            errors.append(f"MISMATCH_CODE:{code}: expected '{expected_name}' vs master '{master_name}'")

    # 2. Audit ALIAS_TO_CANONICAL_NAME targets
    for alias, canonical_target in ALIAS_TO_CANONICAL_NAME.items():
        norm_target = normalize_lookup_key(canonical_target)
        if norm_target not in master.norm_name_to_code:
            errors.append(f"UNRESOLVED_ALIAS_TARGET:{alias} -> {canonical_target}")

    return errors
