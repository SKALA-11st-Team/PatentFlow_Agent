from __future__ import annotations

from datetime import datetime
from typing import Any

from agents.valuation_axes.common import select_by_types_or_axes
from agents.valuation_axes.payload_common import build_base_input_payload
from services.evidence.api_normalizers import extract_kipris_items
from workflow.state import PatentWorkflowState


AXIS = "market"
LABEL = "시장성"
PROMPT_PATH = "valuation/valuation_market.md"
MARKET_GROWTH_MISSING_MESSAGE = "CPC 기준 최근 3년 연도별 특허 출원 수 확인 필요"
INDUSTRY_MARKETABILITY_BREAKDOWN_LIMITS = {
    "industry_growth_evidence_score": 15,
    "corporate_investment_entry_score": 10,
    "news_market_diffusion_score": 10,
    "source_reliability_score": 5,
}


def run(state: PatentWorkflowState, runtime: Any) -> dict[str, Any]:
    evidence = select_evidence(state.evidence_bundle or [], state)
    payload = build_input_payload(state=state, evidence=evidence)
    metrics = build_marketability_metrics(state)
    payload["marketability_metrics"] = metrics
    prompt = runtime.build_prompt(
        prompt_name=PROMPT_PATH,
        state=state,
        payload=payload,
        artifact_name=f"{AXIS}_input",
    )
    result = runtime.run_llm_required(axis=AXIS, prompt=prompt, evidence=evidence)
    return apply_marketability_scores(result, metrics)


def select_evidence(items: list[dict[str, Any]], state: PatentWorkflowState) -> list[dict[str, Any]]:
    del state
    return select_by_types_or_axes(
        items,
        source_types={"industry_report", "company_disclosure", "news"},
        axes={AXIS},
        limit=None,
    )


def build_input_payload(*, state: PatentWorkflowState, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    return build_base_input_payload(state=state, evidence=evidence)


def build_marketability_metrics(state: PatentWorkflowState) -> dict[str, Any]:
    representative_cpc = extract_representative_cpc(state)
    growth = build_market_growth_metrics(representative_cpc)
    global_business = build_global_business_metrics(state.kipris_family_patents or [])
    return {
        "representative_cpc": representative_cpc,
        **growth,
        **global_business,
    }


def extract_representative_cpc(state: PatentWorkflowState) -> str | None:
    sources = [
        ((state.preprocessed_patent or {}).get("metadata") or {}).get("cpc"),
        ((state.kipris_api_data or {}).get("metadata") or {}).get("cpc"),
        (state.patent_structured or {}).get("cpc"),
    ]
    for value in sources:
        values = value if isinstance(value, list) else [value]
        for item in values:
            text = normalize_text(item)
            if text:
                return text
    return None


def build_market_growth_metrics(representative_cpc: str | None) -> dict[str, Any]:
    if not representative_cpc:
        return missing_market_growth("representative_cpc_not_found", [])

    try:
        counts = collect_cpc_yearly_application_counts(representative_cpc)
    except Exception as exc:
        return missing_market_growth(f"kipris_search_failed:{exc.__class__.__name__}", [])

    if len(counts) < 3 or any(item.get("count") is None for item in counts):
        return missing_market_growth("yearly_counts_incomplete", counts)

    first_count = int(counts[0]["count"] or 0)
    last_count = int(counts[-1]["count"] or 0)
    if first_count <= 0:
        return missing_market_growth("cagr_start_count_zero", counts)

    cagr = (last_count / first_count) ** (1 / (len(counts) - 1)) - 1
    cagr_score = score_cagr(cagr)
    trend_status, trend_score = score_recent_trend([int(item["count"]) for item in counts])
    return {
        "cpc_application_counts": counts,
        "market_growth_available": True,
        "cagr": round(cagr, 6),
        "cagr_score": cagr_score,
        "trend_status": trend_status,
        "trend_score": trend_score,
        "market_growth_score": cagr_score + trend_score,
        "missing_reason": None,
    }


def missing_market_growth(reason: str, counts: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "cpc_application_counts": counts,
        "market_growth_available": False,
        "cagr": None,
        "cagr_score": None,
        "trend_status": None,
        "trend_score": None,
        "market_growth_score": None,
        "missing_reason": reason,
    }


def collect_cpc_yearly_application_counts(
    representative_cpc: str,
    *,
    years: list[int] | None = None,
    page_size: int = 500,
    max_pages: int = 20,
) -> list[dict[str, Any]]:
    from open_api.kipris_client import KiprisClient

    target_years = years or recent_three_years()
    target_year_set = set(target_years)
    earliest_year = min(target_years)
    seen: dict[str, dict[str, Any]] = {}
    client = KiprisClient()
    for page_no in range(1, max_pages + 1):
        raw = client.search_by_cpc(
            representative_cpc,
            patent=True,
            utility=False,
            docsCount=page_size,
            docsStart=page_no,
            descSort=True,
            sortSpec="OPD",
            lastvalue="",
        )
        items = extract_kipris_items(raw)
        if not items:
            break
        should_stop = False
        for item in items:
            item_year = extract_year(first_present(item, "OpeningDate", "openDate", "openingDate"))
            if item_year is None:
                continue
            if item_year < earliest_year:
                should_stop = True
                continue
            if item_year not in target_year_set:
                continue
            key = patent_item_key(item)
            if key not in seen:
                seen[key] = item
                seen[key]["_market_growth_year"] = item_year
        if len(items) < page_size or should_stop:
            break

    counts_by_year = {year: 0 for year in target_years}
    for item in seen.values():
        year = item.get("_market_growth_year")
        if year in counts_by_year:
            counts_by_year[year] += 1
    return [{"year": year, "count": counts_by_year[year]} for year in target_years]

def recent_three_years(now: datetime | None = None) -> list[int]:
    year = (now or datetime.now()).year
    return [year - 3, year - 2, year - 1]


def score_cagr(cagr: float) -> int:
    if cagr >= 0.15:
        return 25
    if cagr >= 0.08:
        return 20
    if cagr >= 0.03:
        return 15
    if cagr >= 0:
        return 10
    return 0


def score_recent_trend(counts: list[int]) -> tuple[str, int]:
    if len(counts) < 3:
        return "insufficient_data", 0
    if counts[0] < counts[1] < counts[2]:
        return "continuous_increase", 15
    if counts[0] > counts[1] > counts[2]:
        return "continuous_decrease", 0
    if counts[0] < counts[1] or counts[1] < counts[2]:
        return "partial_increase", 8
    return "flat_or_mixed", 8


def build_global_business_metrics(family_patents: list[dict[str, Any]]) -> dict[str, Any]:
    countries = sorted({country for item in family_patents for country in [extract_country_code(item)] if country})
    foreign_countries = [country for country in countries if country != "KR"]
    priority_countries = {"US", "CN", "JP"}
    if set(foreign_countries).intersection(priority_countries):
        score = 20
        status = "priority_country_family"
    elif foreign_countries:
        score = 10
        status = "foreign_family"
    else:
        score = 0
        status = "domestic_only"
    return {
        "family_countries": countries,
        "foreign_family_countries": foreign_countries,
        "global_business_status": status,
        "global_business_score": score,
    }


def extract_country_code(item: dict[str, Any]) -> str | None:
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    for key in ("country_code", "countryCode", "publicationCountryCode", "applicationCountryCode", "country"):
        value = item.get(key) or raw.get(key)
        text = normalize_text(value).upper()
        if text:
            return text
    return None


def apply_marketability_scores(result: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    industry_breakdown = normalize_industry_marketability_breakdown(
        result.get("industry_marketability_breakdown")
        or (result.get("sub_scores") or {}).get("industry_marketability_breakdown")
    )
    if industry_breakdown:
        industry_score = sum(industry_breakdown.values())
    else:
        industry_score = normalize_industry_score(
            result.get("industry_marketability_score")
            or (result.get("sub_scores") or {}).get("industry_marketability_score")
            or ((result.get("subscores") or {}).get("industry_marketability") or {}).get("score")
        )
    market_growth_score = metrics.get("market_growth_score")
    global_business_score = int(metrics.get("global_business_score") or 0)
    score = industry_score + global_business_score
    if market_growth_score is not None:
        score += int(market_growth_score)
    missing_information = list(result.get("missing_information") or [])
    if market_growth_score is None and MARKET_GROWTH_MISSING_MESSAGE not in missing_information:
        missing_information.append(MARKET_GROWTH_MISSING_MESSAGE)
    confidence = float(result.get("confidence") or 0)
    if market_growth_score is None:
        confidence = min(confidence, 0.49)
    sanitized_result = {
        key: value
        for key, value in result.items()
        if key
        not in {
            "industry_marketability_score",
            "industry_marketability_breakdown",
            "sub_scores",
        }
    }
    return {
        **sanitized_result,
        "score": max(0, min(100, score)),
        "grade": grade_for_score(score),
        "subscores": build_market_subscores(
            result,
            industry_score=industry_score,
            market_growth_score=market_growth_score,
            global_business_score=global_business_score,
        ),
        "marketability_metrics": metrics,
        "missing_information": missing_information,
        "confidence": max(0.0, min(1.0, confidence)),
    }


def build_market_subscores(
    result: dict[str, Any],
    *,
    industry_score: int,
    market_growth_score: int | None,
    global_business_score: int,
) -> dict[str, dict[str, Any]]:
    subscores = result.get("subscores") if isinstance(result.get("subscores"), dict) else {}
    industry = subscores.get("industry_marketability") if isinstance(subscores.get("industry_marketability"), dict) else {}
    market_growth = subscores.get("market_growth") if isinstance(subscores.get("market_growth"), dict) else {}
    global_business = subscores.get("global_business") if isinstance(subscores.get("global_business"), dict) else {}
    return {
        "industry_marketability": {
            "label": "산업 시장성",
            "score": industry_score,
            "max_score": 40,
            "rationale": normalize_text(industry.get("rationale")),
        },
        "market_growth": {
            "label": "시장 성장성",
            "score": market_growth_score,
            "max_score": 40,
            "rationale": normalize_text(market_growth.get("rationale"))
            or "대표 CPC 기준 최근 3년 특허 출원 증가율 및 추세로 산정된 코드 계산값입니다.",
        },
        "global_business": {
            "label": "글로벌 사업성",
            "score": global_business_score,
            "max_score": 20,
            "rationale": normalize_text(global_business.get("rationale"))
            or "Patent Family 국가 정보로 산정된 코드 계산값입니다.",
        },
    }


def normalize_industry_marketability_breakdown(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        key: binary_full_score(value.get(key), max_score)
        for key, max_score in INDUSTRY_MARKETABILITY_BREAKDOWN_LIMITS.items()
    }


def normalize_industry_score(value: Any) -> int:
    return binary_full_score(value, 40)


def binary_full_score(value: Any, max_score: int) -> int:
    try:
        score = int(value)
    except (TypeError, ValueError):
        return 0
    return max_score if score > 0 else 0


def clamp_int(value: Any, default: int, max_value: int, min_value: int = 0) -> int:
    try:
        score = int(value)
    except (TypeError, ValueError):
        return default
    return max(min_value, min(max_value, score))


def grade_for_score(score: int) -> str:
    if score >= 80:
        return "A"
    if score >= 60:
        return "B"
    if score >= 40:
        return "C"
    return "D"


def extract_year(value: Any) -> int | None:
    text = normalize_text(value)
    if len(text) < 4 or not text[:4].isdigit():
        return None
    return int(text[:4])


def patent_item_key(item: dict[str, Any]) -> str:
    for keys in (
        ("ApplicationNumber", "applicationNumber"),
        ("RegistrationNumber", "registerNumber", "registrationNumber"),
        ("OpeningNumber", "openNumber"),
        ("PublicNumber", "publicationNumber"),
    ):
        text = normalize_text(first_present(item, *keys))
        if text:
            return f"{keys[0]}:{text}"
    return ":".join(
        part
        for part in [
            normalize_text(first_present(item, "InventionName", "inventionTitle")),
            normalize_text(first_present(item, "ApplicationDate", "applicationDate")),
        ]
        if part
    )


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def first_present(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return value
    return None
