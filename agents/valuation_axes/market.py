from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from agents.valuation_axes.common import select_by_types_or_axes
from agents.valuation_axes.payload_common import build_base_input_payload
from services.evidence.api_normalizers import extract_kipris_items
from workflow.state import PatentWorkflowState


AXIS = "market"
LABEL = "시장성"
PROMPT_PATH = "valuation/valuation_market.md"
MARKET_GROWTH_MISSING_MESSAGE = "CPC 기준 18개월 전 종료 3개 1년 구간 공개 특허 수 확인 필요"
def run(state: PatentWorkflowState, runtime: Any) -> dict[str, Any]:
    evidence = select_evidence(state.evidence_bundle or [], state)
    payload = build_input_payload(state=state, evidence=evidence)
    metrics = build_marketability_metrics(state, evidence=evidence)
    payload["marketability_metrics"] = metrics
    payload["market_evidence_groups"] = build_market_evidence_groups(evidence)
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
        source_types={"industry_report", "news"},
        axes={AXIS},
        limit=None,
    )


def build_input_payload(*, state: PatentWorkflowState, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    return build_base_input_payload(state=state, evidence=evidence)


def build_marketability_metrics(state: PatentWorkflowState, *, evidence: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    representative_cpc = extract_representative_cpc(state)
    growth = build_market_growth_metrics(representative_cpc)
    global_business = build_global_business_metrics(evidence or [])
    return {
        "representative_cpc": representative_cpc,
        **growth,
        **global_business,
    }


def build_market_evidence_groups(evidence: list[dict[str, Any]]) -> dict[str, list[str]]:
    groups = {
        "industry_report_evidence_ids": [],
        "naver_news_evidence_ids": [],
        "gnews_evidence_ids": [],
    }
    for item in evidence:
        evidence_id = normalize_text(item.get("evidence_id"))
        if not evidence_id:
            continue
        source = normalize_text(item.get("source")).lower()
        source_type = normalize_text(item.get("source_type")).lower()
        if source_type == "industry_report":
            groups["industry_report_evidence_ids"].append(evidence_id)
        elif source == "naver_news":
            groups["naver_news_evidence_ids"].append(evidence_id)
        elif source == "gnews":
            groups["gnews_evidence_ids"].append(evidence_id)
    return groups


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

    windows = market_growth_windows()
    try:
        counts = collect_cpc_window_application_counts(representative_cpc, windows=windows)
    except Exception as exc:
        return missing_market_growth(f"kipris_search_failed:{exc.__class__.__name__}", windows)

    if len(counts) < 3 or any(item.get("count") is None for item in counts):
        return missing_market_growth("window_counts_incomplete", windows)

    first_count = int(counts[0]["count"] or 0)
    last_count = int(counts[-1]["count"] or 0)
    if first_count <= 0:
        return missing_market_growth("cagr_start_count_zero", windows)

    cagr = (last_count / first_count) ** (1 / (len(counts) - 1)) - 1
    cagr_score = score_cagr(cagr)
    trend_status, trend_score = score_recent_trend([int(item["count"]) for item in counts])
    reference_date = windows[-1]["end_date"] if windows else None
    return {
        "cpc_application_counts": counts,
        "market_growth_reference_date": reference_date.isoformat() if isinstance(reference_date, date) else None,
        "market_growth_available": True,
        "cagr": round(cagr, 6),
        "cagr_score": cagr_score,
        "trend_status": trend_status,
        "trend_score": trend_score,
        "market_growth_score": cagr_score + trend_score,
        "missing_reason": None,
    }


def missing_market_growth(reason: str, windows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = [
        {
            "label": item["label"],
            "start_date": item["start_date"].isoformat(),
            "end_date": item["end_date"].isoformat(),
            "count": None,
        }
        for item in windows
    ]
    reference_date = windows[-1]["end_date"] if windows else None
    return {
        "cpc_application_counts": counts,
        "market_growth_reference_date": reference_date.isoformat() if isinstance(reference_date, date) else None,
        "market_growth_available": False,
        "cagr": None,
        "cagr_score": None,
        "trend_status": None,
        "trend_score": None,
        "market_growth_score": None,
        "missing_reason": reason,
    }


def collect_cpc_window_application_counts(
    representative_cpc: str,
    *,
    windows: list[dict[str, Any]] | None = None,
    page_size: int = 500,
    max_pages: int = 5,
) -> list[dict[str, Any]]:
    from open_api.kipris_client import KiprisClient

    target_windows = windows or market_growth_windows()
    if not target_windows:
        return []
    earliest_start = min(item["start_date"] for item in target_windows)
    seen: dict[str, dict[str, Any]] = {}
    client = KiprisClient()
    for page_no in range(1, max_pages + 1):
        docs_start = ((page_no - 1) * page_size) + 1
        raw = client.search_by_cpc(
            representative_cpc,
            patent=True,
            utility=False,
            docsCount=page_size,
            docsStart=docs_start,
            descSort=True,
            sortSpec="OPD",
            lastvalue="",
        )
        items = extract_kipris_items(raw)
        if not items:
            break
        should_stop = False
        for item in items:
            opening_date = cpc_item_opening_date(item)
            if opening_date is None:
                continue
            if opening_date < earliest_start:
                should_stop = True
                continue
            window = market_growth_window_for_date(opening_date, target_windows)
            if window is None:
                continue
            key = patent_item_key(item)
            if key not in seen:
                seen[key] = item
                seen[key]["_market_growth_window_label"] = window["label"]
        if len(items) < page_size or should_stop:
            break

    counts_by_window = {item["label"]: 0 for item in target_windows}
    for item in seen.values():
        label = item.get("_market_growth_window_label")
        if label in counts_by_window:
            counts_by_window[label] += 1
    return [
        {
            "label": item["label"],
            "start_date": item["start_date"].isoformat(),
            "end_date": item["end_date"].isoformat(),
            "count": counts_by_window[item["label"]],
        }
        for item in target_windows
    ]


def cpc_item_opening_date(item: dict[str, Any]) -> date | None:
    return parse_kipris_date(first_present(item, "OpeningDate", "openDate", "openingDate"))


def market_growth_reference_date(now: datetime | None = None) -> date:
    current_date = (now or datetime.now()).date()
    return shift_months(current_date, -18)


def market_growth_windows(reference_date: date | None = None) -> list[dict[str, Any]]:
    end_date = reference_date or market_growth_reference_date()
    windows: list[dict[str, Any]] = []
    for offset in (2, 1, 0):
        window_end = shift_months(end_date, -(12 * offset))
        previous_end = shift_months(window_end, -12)
        window_start = previous_end + timedelta(days=1)
        windows.append(
            {
                "label": f"{window_start.isoformat()}~{window_end.isoformat()}",
                "start_date": window_start,
                "end_date": window_end,
            }
        )
    return windows


def market_growth_window_for_date(opening_date: date, windows: list[dict[str, Any]]) -> dict[str, Any] | None:
    for item in windows:
        if item["start_date"] <= opening_date <= item["end_date"]:
            return item
    return None


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
    return "flat_or_mixed", 0


def build_global_business_metrics(evidence: list[dict[str, Any]]) -> dict[str, Any]:
    gnews_items = [item for item in evidence if normalize_text(item.get("source")).lower() == "gnews"]
    quality_items = [item for item in gnews_items if has_global_market_signal_content(item)]
    quality_count = len(quality_items)
    if quality_count >= 3:
        score = 20
        status = "strong_gnews_global_signal"
    elif quality_count >= 1:
        score = 10
        status = "limited_gnews_global_signal"
    else:
        score = 0
        status = "no_gnews_global_signal"
    return {
        "gnews_evidence_count": len(gnews_items),
        "gnews_quality_evidence_count": quality_count,
        "gnews_evidence_ids": [item.get("evidence_id") for item in quality_items if item.get("evidence_id")],
        "global_business_status": status,
        "global_business_score": score,
    }


def has_global_market_signal_content(item: dict[str, Any]) -> bool:
    texts = [
        normalize_text(item.get("title")),
        normalize_text(item.get("compressed_summary")),
        normalize_text(item.get("summary")),
    ]
    key_facts = item.get("key_facts")
    if isinstance(key_facts, list):
        texts.extend(normalize_text(fact) for fact in key_facts)
    combined = " ".join(text for text in texts if text).lower()
    if not combined:
        return False
    signal_keywords = (
        "도입",
        "출시",
        "상용",
        "제품",
        "서비스",
        "적용",
        "확산",
        "고객",
        "수요",
        "규제",
        "시장",
        "adoption",
        "launch",
        "commercial",
        "deployment",
        "product",
        "service",
        "demand",
        "customer",
        "regulation",
        "market",
    )
    return any(keyword in combined for keyword in signal_keywords)


def apply_marketability_scores(result: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    industry_breakdown = normalize_industry_marketability_breakdown(
        result.get("industry_marketability_breakdown")
        or (((result.get("subscores") or {}).get("industry_marketability") or {}).get("details"))
    )
    if industry_breakdown:
        industry_score = sum(industry_breakdown.values())
    else:
        industry_score = normalize_industry_score(
            result.get("industry_marketability_score")
            or ((result.get("subscores") or {}).get("industry_marketability") or {}).get("score")
        )
    market_growth_score = metrics.get("market_growth_score")
    global_business_score = int(metrics.get("global_business_score") or 0)
    score = industry_score + global_business_score
    if market_growth_score is not None:
        score += int(market_growth_score)
    metrics = dict(metrics)
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
        }
    }
    return {
        **sanitized_result,
        "score": max(0, min(100, score)),
        "grade": grade_for_score(score),
        "subscores": build_market_subscores(
            result,
            metrics=metrics,
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
    metrics: dict[str, Any],
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
            **({"details": normalize_industry_marketability_breakdown(industry.get("details"))} if isinstance(industry.get("details"), dict) else {}),
            "rationale": normalize_text(industry.get("rationale")),
        },
        "market_growth": {
            "label": "시장 성장성",
            "score": market_growth_score,
            "max_score": 40,
            "details": {
                "cagr_score": nullable_int(metrics.get("cagr_score")),
                "trend_score": nullable_int(metrics.get("trend_score")),
            },
            "rationale": normalize_text(market_growth.get("rationale"))
            or "대표 CPC 기준 18개월 전 종료 3개 1년 구간 공개 특허 수 증가율 및 추세로 산정된 코드 계산값입니다.",
        },
        "global_business": {
            "label": "글로벌 사업성",
            "score": global_business_score,
            "max_score": 20,
            "details": {
                "gnews_evidence_count": clamp_int(metrics.get("gnews_evidence_count"), default=0, max_value=9999),
                "gnews_quality_evidence_count": clamp_int(metrics.get("gnews_quality_evidence_count"), default=0, max_value=9999),
                "global_business_status": normalize_text(metrics.get("global_business_status")),
            },
            "rationale": normalize_text(global_business.get("rationale"))
            or "GNews 해외 뉴스 근거로 산정된 코드 계산값입니다.",
        },
    }


def nullable_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_industry_marketability_breakdown(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        "industry_growth_evidence": nearest_score(
            first_present(value, "industry_growth_evidence_score", "industry_growth_evidence"),
            (0, 15),
        ),
        "corporate_investment_entry": nearest_score(
            first_present(value, "corporate_investment_entry_score", "corporate_investment_entry"),
            (0, 10),
        ),
        "news_market_diffusion": nearest_score(
            first_present(value, "news_market_diffusion_score", "news_market_diffusion"),
            (0, 10),
        ),
        "source_reliability": nearest_score(
            first_present(value, "source_reliability_score", "source_reliability"),
            (0, 5),
        ),
    }


def normalize_industry_score(value: Any) -> int:
    return clamp_int(value, default=0, max_value=40)


def nearest_score(value: Any, candidates: tuple[int, ...]) -> int:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0
    return min(candidates, key=lambda candidate: (abs(candidate - score), -candidate))


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


def parse_kipris_date(value: Any) -> date | None:
    digits = "".join(ch for ch in normalize_text(value) if ch.isdigit())
    if len(digits) < 8:
        return None
    try:
        return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
    except ValueError:
        return None


def shift_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, days_in_month(year, month))
    return date(year, month, day)


def days_in_month(year: int, month: int) -> int:
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    return (next_month - date(year, month, 1)).days


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
