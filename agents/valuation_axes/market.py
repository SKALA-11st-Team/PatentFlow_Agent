from __future__ import annotations

from datetime import date, datetime, timedelta
import re
from typing import Any

from agents.valuation_axes.common import grade_for_score, select_by_source_types
from agents.valuation_axes.payload_common import build_base_input_payload
from services.evidence.api_normalizers import extract_kipris_items
from workflow.state import PatentWorkflowState

try:
    from open_api.secret_scrub import scrub_secrets
except ImportError:
    def scrub_secrets(text: Any) -> str:
        return str(text or "")


AXIS = "market"
LABEL = "시장성"
PROMPT_PATH = "valuation/valuation_market.md"
MARKET_GROWTH_MISSING_MESSAGE = "분류 기준 18개월 전 종료 3개 1년 구간 공개 특허 수 확인 필요"
FOREIGN_MARKET_PRIORITY_REPORTS = (
    "mckinsey-technology-trends-outlook-2025.pdf",
    "wef_top_10_emerging_technologies_of_2025.pdf",
)


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
        axis=AXIS,
    )
    result = runtime.run_llm_required(axis=AXIS, prompt=prompt, evidence=evidence)
    return apply_marketability_scores(result, metrics)


def select_evidence(items: list[dict[str, Any]], state: PatentWorkflowState) -> list[dict[str, Any]]:
    selected = select_by_source_types(
        items,
        source_types={"industry_report", "news"},
        limit=None,
    )
    if is_foreign_patent(state):
        return prioritize_foreign_market_reports(selected)
    return selected


def build_input_payload(*, state: PatentWorkflowState, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    payload = build_base_input_payload(state=state, evidence=evidence)
    payload["invention_market_linkage_context"] = build_invention_market_linkage_context(state)
    return payload


def build_invention_market_linkage_context(state: PatentWorkflowState) -> dict[str, Any]:
    patent = state.patent_structured or {}
    target_structure = state.target_structure or {}
    preprocessed = state.preprocessed_patent or {}
    summary = state.summary_result or {}
    key_elements = target_structure.get("key_elements") if isinstance(target_structure.get("key_elements"), list) else []
    expected_effects = patent.get("expected_effects")
    if not isinstance(expected_effects, list):
        expected_effects = [expected_effects] if expected_effects else []
    expected_effects = unique_nonempty_texts(
        [
            *expected_effects,
            *extract_effect_texts_from_key_elements(key_elements),
            *(summary.get("key_points") if isinstance(summary.get("key_points"), list) else []),
        ]
    )
    core_application_functions = unique_nonempty_texts(
        [
            *(patent.get("core_application_functions") if isinstance(patent.get("core_application_functions"), list) else [patent.get("core_application_functions")]),
            *extract_core_functions_from_key_elements(key_elements),
        ]
    )
    problem_to_solve = normalize_text(
        patent.get("problem_to_solve")
        or patent.get("problem")
        or preprocessed.get("problem_to_solve")
        or extract_problem_to_solve_from_key_elements(key_elements)
    )
    technology_field = normalize_text(
        patent.get("technology_field")
        or patent.get("field")
        or patent.get("field_label")
        or preprocessed.get("technology_field")
        or patent.get("technology_area")
        or patent.get("related_product")
        or patent.get("title_final")
    )
    background_art = normalize_text(
        patent.get("background_art")
        or patent.get("background")
        or preprocessed.get("background_art")
        or summary.get("plain_summary")
    )
    application_keywords = unique_nonempty_texts(
        [
            patent.get("related_product"),
            patent.get("business_area"),
            patent.get("technology_area"),
            patent.get("field_label"),
            patent.get("application_domain"),
            patent.get("market_keyword"),
            patent.get("problem_domain"),
        ]
    )
    return {
        "technology_field": technology_field,
        "background_art": background_art,
        "problem_to_solve": problem_to_solve,
        "core_application_functions": core_application_functions,
        "key_elements": key_elements,
        "expected_effects": unique_nonempty_texts(expected_effects),
        "application_domain_keywords": application_keywords,
        "direct_connection_hints": build_direct_connection_hints(
            key_elements,
            core_application_functions=core_application_functions,
            expected_effects=expected_effects,
        ),
    }


def build_marketability_metrics(state: PatentWorkflowState, *, evidence: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    foreign_patent = is_foreign_patent(state)
    representative_cpc = extract_representative_cpc(state)
    representative_ipc = extract_representative_ipc(state)
    growth = build_market_growth_metrics(
        representative_code=representative_ipc if foreign_patent else representative_cpc,
        use_ipc=foreign_patent,
        country_code=extract_patent_country(state) if foreign_patent else None,
    )
    return {
        "representative_cpc": representative_cpc,
        "representative_ipc": representative_ipc,
        "market_growth_code_type": "ipc" if foreign_patent else "cpc",
        **growth,
    }


def build_market_evidence_groups(evidence: list[dict[str, Any]]) -> dict[str, list[str]]:
    groups = {
        "industry_report_evidence_ids": [],
        "naver_news_evidence_ids": [],
        "global_news_evidence_ids": [],
        "competition_evidence_ids": [],
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
            groups["competition_evidence_ids"].append(evidence_id)
        elif source == "global_news":
            groups["global_news_evidence_ids"].append(evidence_id)
            groups["competition_evidence_ids"].append(evidence_id)
    return groups


def prioritize_foreign_market_reports(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def sort_key(item: dict[str, Any]) -> tuple[int, float]:
        source_type = normalize_text(item.get("source_type")).lower()
        if source_type != "industry_report":
            return (2, 0.0)
        source_name = industry_report_source_name(item)
        if source_name in FOREIGN_MARKET_PRIORITY_REPORTS:
            return (0, -float(item.get("score") or item.get("retrieval_score") or 0.0))
        return (1, -float(item.get("score") or item.get("retrieval_score") or 0.0))

    return sorted(evidence, key=sort_key)


def industry_report_source_name(item: dict[str, Any]) -> str:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    source_name = normalize_text(metadata.get("source_name")) or normalize_text(item.get("source"))
    return source_name.lower()


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


def extract_representative_ipc(state: PatentWorkflowState) -> str | None:
    sources = [
        ((state.preprocessed_patent or {}).get("metadata") or {}).get("ipc"),
        ((state.kipris_api_data or {}).get("metadata") or {}).get("ipc"),
        (state.patent_structured or {}).get("ipc"),
    ]
    for value in sources:
        values = value if isinstance(value, list) else [value]
        for item in values:
            text = normalize_text(item)
            if text:
                return text
    return None


def build_market_growth_metrics(
    representative_code: str | None,
    *,
    use_ipc: bool = False,
    country_code: str | None = None,
) -> dict[str, Any]:
    if not representative_code:
        return missing_market_growth("representative_ipc_not_found" if use_ipc else "representative_cpc_not_found", [])

    windows = market_growth_windows()
    try:
        counts = collect_classification_window_application_counts(
            representative_code,
            windows=windows,
            use_ipc=use_ipc,
            country_code=country_code,
        )
    except Exception as exc:
        detail = scrub_secrets(normalize_text(exc))
        reason = f"kipris_search_failed:{exc.__class__.__name__}"
        if detail:
            reason = f"{reason}:{detail}"
        return missing_market_growth(reason, windows)

    if len(counts) < 3 or any(item.get("count") is None for item in counts):
        return missing_market_growth("window_counts_incomplete", windows)

    first_count = int(counts[0]["count"] or 0)
    last_count = int(counts[-1]["count"] or 0)
    if first_count <= 0:
        # CAGR needs a non-zero base year. A classification reclassified into a new code
        # (e.g. G06F 17/50 -> G06F 30/xx) goes empty in recent windows, so the
        # base window is 0. Keep the real counts and flag the likely cause.
        total = sum(int(item.get("count") or 0) for item in counts)
        reason = ("ipc_inactive_or_reclassified" if use_ipc else "cpc_inactive_or_reclassified") if total == 0 else "cagr_start_count_zero"
        return unavailable_market_growth(reason, counts)

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
        "market_growth_country_code": country_code,
        "missing_reason": None,
    }


def unavailable_market_growth(reason: str, counts: list[dict[str, Any]]) -> dict[str, Any]:
    """CAGR could not be computed, but keep the real per-window counts so the
    gap stays diagnosable (e.g. a reclassified/inactive CPC whose recent windows
    are empty) instead of masking every window count as null."""
    reference_date = counts[-1]["end_date"] if counts else None
    return {
        "cpc_application_counts": counts,
        "market_growth_reference_date": reference_date,
        "market_growth_available": False,
        "cagr": None,
        "cagr_score": None,
        "trend_status": None,
        "trend_score": None,
        "market_growth_score": None,
        "missing_reason": reason,
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


def collect_classification_window_application_counts(
    representative_code: str,
    *,
    windows: list[dict[str, Any]] | None = None,
    use_ipc: bool = False,
    country_code: str | None = None,
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
        search_fn = client.search_by_ipc if use_ipc else client.search_by_cpc
        raw = search_fn(
            representative_code,
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
            if country_code and normalize_text(first_present(item, "publicationCountryCode", "countryCode", "applicationCountryCode", "country")).upper() != country_code:
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


def collect_cpc_window_application_counts(
    representative_cpc: str,
    *,
    windows: list[dict[str, Any]] | None = None,
    page_size: int = 500,
    max_pages: int = 5,
) -> list[dict[str, Any]]:
    return collect_classification_window_application_counts(
        representative_cpc,
        windows=windows,
        use_ipc=False,
        country_code=None,
        page_size=page_size,
        max_pages=max_pages,
    )


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
        return 10
    if cagr >= 0.08:
        return 8
    if cagr >= 0.03:
        return 6
    if cagr >= 0:
        return 4
    return 0


def score_recent_trend(counts: list[int]) -> tuple[str, int]:
    if len(counts) < 3:
        return "insufficient_data", 0
    if counts[0] < counts[1] < counts[2]:
        return "continuous_increase", 10
    if counts[0] > counts[1] > counts[2]:
        return "continuous_decrease", 0
    if counts[0] < counts[1] or counts[1] < counts[2]:
        return "partial_increase", 5
    return "flat_or_mixed", 0


FOREIGN_COUNTRY_HINTS = {
    "US": ("united states", "u.s.", "u.s.a.", "usa", "american", "america"),
    "JP": ("japan", "japanese", "tokyo"),
    "CN": ("china", "chinese", "beijing", "shanghai"),
    "EP": ("europe", "european", "eu", "e.u."),
    "KR": ("korea", "korean", "seoul"),
}


def build_global_business_metrics(evidence: list[dict[str, Any]], *, patent_country: str | None = None) -> dict[str, Any]:
    gnews_items = [item for item in evidence if normalize_text(item.get("source")).lower() == "global_news"]
    if patent_country and patent_country != "KR":
        quality_items = [item for item in gnews_items if has_foreign_global_business_signal(item, patent_country=patent_country)]
    else:
        quality_items = [item for item in gnews_items if has_global_market_signal_content(item)]
    return {
        "gnews_evidence_count": len(gnews_items),
        "gnews_quality_evidence_count": len(quality_items),
        "gnews_evidence_ids": [item.get("evidence_id") for item in quality_items if item.get("evidence_id")],
        "global_business_excluded_country": patent_country if patent_country and patent_country != "KR" else None,
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


def has_foreign_global_business_signal(item: dict[str, Any], patent_country: str) -> bool:
    if not has_global_market_signal_content(item):
        return False
    if not matches_patent_country_signal(item, patent_country):
        return True
    return has_other_country_or_global_signal(item, patent_country)


def matches_patent_country_signal(item: dict[str, Any], patent_country: str) -> bool:
    country = normalize_text(patent_country).upper()
    if not country:
        return False
    hints = FOREIGN_COUNTRY_HINTS.get(country, ())
    if not hints:
        return False
    texts = [
        normalize_text(item.get("title")),
        normalize_text(item.get("url")),
        normalize_text(item.get("content")),
        normalize_text(item.get("compressed_summary")),
        normalize_text(((item.get("metadata") or {}) if isinstance(item.get("metadata"), dict) else {}).get("publisher")),
    ]
    combined = " ".join(texts).lower()
    return any(hint in combined for hint in hints)


def has_other_country_or_global_signal(item: dict[str, Any], patent_country: str) -> bool:
    excluded = normalize_text(patent_country).upper()
    texts = [
        normalize_text(item.get("title")),
        normalize_text(item.get("url")),
        normalize_text(item.get("content")),
        normalize_text(item.get("compressed_summary")),
        normalize_text(((item.get("metadata") or {}) if isinstance(item.get("metadata"), dict) else {}).get("publisher")),
    ]
    combined = " ".join(texts).lower()
    global_hints = (
        "global",
        "international",
        "worldwide",
        "across europe",
        "across asia",
        "across markets",
        "overseas",
        "cross-border",
        "multi-country",
        "multinational",
        "유럽",
        "아시아",
        "글로벌",
        "국가",
        "해외",
    )
    if any(hint in combined for hint in global_hints):
        return True
    for country_code, hints in FOREIGN_COUNTRY_HINTS.items():
        if country_code == excluded:
            continue
        if any(hint in combined for hint in hints):
            return True
    return False


def apply_marketability_scores(result: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    subscores = result.get("subscores") if isinstance(result.get("subscores"), dict) else {}
    industry = subscores.get("industry_marketability") if isinstance(subscores.get("industry_marketability"), dict) else {}
    global_business = subscores.get("global_business") if isinstance(subscores.get("global_business"), dict) else {}
    competitiveness = subscores.get("competitiveness") if isinstance(subscores.get("competitiveness"), dict) else {}
    invention_market_linkage = (
        subscores.get("invention_market_linkage")
        if isinstance(subscores.get("invention_market_linkage"), dict)
        else {}
    )
    industry_score = clamp_market_subscore(industry.get("score"), max_value=20)
    market_growth_score = metrics.get("market_growth_score")
    global_business_score = clamp_market_subscore(global_business.get("score"), max_value=20)
    competitiveness_score = clamp_market_subscore(competitiveness.get("score"), max_value=20)
    invention_market_linkage_score = clamp_market_subscore(invention_market_linkage.get("score"), max_value=20)
    score = industry_score + global_business_score + competitiveness_score + invention_market_linkage_score
    if market_growth_score is not None:
        score += int(market_growth_score)
    metrics = dict(metrics)
    missing_information = list(result.get("missing_information") or [])
    missing_message = market_growth_missing_message(metrics)
    if market_growth_score is None and missing_message not in missing_information:
        missing_information.append(missing_message)
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
            competitiveness_score=competitiveness_score,
            invention_market_linkage_score=invention_market_linkage_score,
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
    competitiveness_score: int,
    invention_market_linkage_score: int,
) -> dict[str, dict[str, Any]]:
    subscores = result.get("subscores") if isinstance(result.get("subscores"), dict) else {}
    industry = subscores.get("industry_marketability") if isinstance(subscores.get("industry_marketability"), dict) else {}
    market_growth = subscores.get("market_growth") if isinstance(subscores.get("market_growth"), dict) else {}
    global_business = subscores.get("global_business") if isinstance(subscores.get("global_business"), dict) else {}
    competitiveness = subscores.get("competitiveness") if isinstance(subscores.get("competitiveness"), dict) else {}
    invention_market_linkage = (
        subscores.get("invention_market_linkage")
        if isinstance(subscores.get("invention_market_linkage"), dict)
        else {}
    )
    return {
        "industry_marketability": {
            "label": "산업 시장성",
            "score": industry_score,
            "max_score": 20,
            "rationale": normalize_text(industry.get("rationale")),
        },
        "market_growth": {
            "label": "시장 성장성",
            "score": market_growth_score,
            "max_score": 20,
            "details": {
                "cagr_score": nullable_int(metrics.get("cagr_score")),
                "trend_score": nullable_int(metrics.get("trend_score")),
            },
            "rationale": normalize_text(market_growth.get("rationale"))
            or market_growth_rationale(metrics),
        },
        "global_business": {
            "label": "글로벌 사업성",
            "score": global_business_score,
            "max_score": 20,
            "rationale": normalize_text(global_business.get("rationale")),
        },
        "competitiveness": {
            "label": "경쟁성",
            "score": competitiveness_score,
            "max_score": 20,
            "rationale": normalize_text(competitiveness.get("rationale")),
        },
        "invention_market_linkage": {
            "label": "발명-시장 연결성",
            "score": invention_market_linkage_score,
            "max_score": 20,
            "rationale": normalize_text(invention_market_linkage.get("rationale")),
        },
    }


def nullable_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def clamp_market_subscore(value: Any, *, max_value: int) -> int:
    return clamp_int(value, default=0, max_value=max_value)


def clamp_int(value: Any, default: int, max_value: int, min_value: int = 0) -> int:
    try:
        score = int(value)
    except (TypeError, ValueError):
        return default
    return max(min_value, min(max_value, score))


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


def extract_patent_country(state: PatentWorkflowState) -> str | None:
    country = normalize_text((state.patent_structured or {}).get("country")).upper()
    return country or None


def is_foreign_patent(state: PatentWorkflowState) -> bool:
    country = extract_patent_country(state)
    return bool(country and country != "KR")


def market_growth_code_label(metrics: dict[str, Any]) -> str:
    return "대표 IPC 기준 해당 국가 공개 특허 수" if metrics.get("market_growth_code_type") == "ipc" else "대표 CPC 기준 공개 특허 수"


def market_growth_rationale(metrics: dict[str, Any]) -> str:
    label = market_growth_code_label(metrics)
    if metrics.get("market_growth_code_type") == "ipc" and metrics.get("market_growth_country_code"):
        return f"{label} 증가율 및 추세로 산정된 코드 계산값입니다."
    return f"{label} 증가율 및 추세로 산정된 코드 계산값입니다."


def market_growth_missing_message(metrics: dict[str, Any]) -> str:
    if metrics.get("market_growth_code_type") == "ipc" and metrics.get("market_growth_country_code"):
        return "IPC 기준 해당 국가 18개월 전 종료 3개 1년 구간 공개 특허 수 확인 필요"
    return "CPC 기준 18개월 전 종료 3개 1년 구간 공개 특허 수 확인 필요"


def first_present(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return value
    return None


def unique_nonempty_texts(values: list[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = normalize_text(value)
        if text and text not in result:
            result.append(text)
    return result


def extract_core_functions_from_key_elements(key_elements: list[dict[str, Any]]) -> list[str]:
    functions: list[str] = []
    prioritized = prioritized_key_elements(key_elements)
    for item in prioritized[:5]:
        if not isinstance(item, dict):
            continue
        concise = concise_market_linkage_phrase(item)
        functions.extend(
            unique_nonempty_texts(
                [
                    concise,
                    *extract_spec_support_texts(item, sections={"과제해결수단"}),
                ]
            )
        )
    return functions[:6]


def extract_problem_to_solve_from_key_elements(key_elements: list[dict[str, Any]]) -> str:
    for item in prioritized_key_elements(key_elements):
        if not isinstance(item, dict):
            continue
        text = concise_market_linkage_phrase(item) or normalize_text(item.get("why_essential"))
        if text:
            return text
    return ""


def extract_effect_texts_from_key_elements(key_elements: list[dict[str, Any]]) -> list[str]:
    effects: list[str] = []
    for item in key_elements:
        if not isinstance(item, dict):
            continue
        effects.extend(extract_spec_support_texts(item, sections={"효과", "발명의효과"}))
    return unique_nonempty_texts(effects)[:6]


def extract_spec_support_texts(item: dict[str, Any], *, sections: set[str]) -> list[str]:
    texts: list[str] = []
    supports = item.get("spec_support")
    if not isinstance(supports, list):
        return texts
    for support in supports:
        if not isinstance(support, dict):
            continue
        section = normalize_text(support.get("section")).replace(" ", "")
        if section in sections:
            texts.append(normalize_text(support.get("mapped_spec_content")))
    return unique_nonempty_texts(texts)


def build_direct_connection_hints(
    key_elements: list[dict[str, Any]],
    *,
    core_application_functions: list[str],
    expected_effects: list[str],
) -> list[str]:
    hints: list[str] = []
    for item in prioritized_key_elements(key_elements)[:4]:
        if not isinstance(item, dict):
            continue
        name = normalize_text(item.get("key_element_name"))
        essential = concise_market_linkage_phrase(item) or normalize_text(item.get("why_essential"))
        if name and essential:
            hints.append(f"{name}: {essential}")
    for text in core_application_functions[:3]:
        if text:
            hints.append(f"핵심 기능: {text}")
    for text in expected_effects[:3]:
        if text:
            hints.append(f"기대 효과: {text}")
    return unique_nonempty_texts(hints)[:8]


def prioritized_key_elements(key_elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    generic_names = {
        "통신부",
        "프로세서",
        "저장부",
        "저장부 (스토리지)",
        "메모리 영역",
        "메모리 영역 (memory pool)",
        "시스템",
        "장치",
        "서버",
    }
    action_keywords = (
        "스킵",
        "판단",
        "비교",
        "조정",
        "결합",
        "예측",
        "최적화",
        "반영",
        "산출",
        "검증",
        "리밸런싱",
        "배분",
        "동일",
        "해시",
        "복원",
    )

    def score(item: dict[str, Any]) -> tuple[int, int]:
        name = normalize_text(item.get("key_element_name"))
        text = " ".join(
            [
                name,
                normalize_text(item.get("why_essential")),
                *extract_spec_support_texts(item, sections={"과제해결수단", "효과", "발명의효과"}),
            ]
        )
        keyword_score = sum(1 for keyword in action_keywords if keyword in text)
        generic_penalty = 1 if name in generic_names else 0
        return (keyword_score, -generic_penalty)

    return sorted(
        [item for item in key_elements if isinstance(item, dict)],
        key=score,
        reverse=True,
    )


def concise_market_linkage_phrase(item: dict[str, Any]) -> str:
    texts = [
        normalize_text(item.get("why_essential")),
        *extract_spec_support_texts(item, sections={"과제해결수단", "효과", "발명의효과"}),
    ]
    for text in texts:
        if has_market_linkage_action(text):
            return shorten_market_linkage_text(text)
    return ""


def has_market_linkage_action(text: str) -> bool:
    keywords = (
        "스킵",
        "판단",
        "비교",
        "조정",
        "결합",
        "예측",
        "최적화",
        "반영",
        "산출",
        "검증",
        "리밸런싱",
        "배분",
        "동일",
        "해시",
        "복원",
    )
    return any(keyword in text for keyword in keywords)


def shorten_market_linkage_text(text: str) -> str:
    compact = re.sub(r"\s+", " ", normalize_text(text))
    compact = re.sub(r"^.*?(", r"\1", compact, count=1) if False else compact
    if len(compact) <= 140:
        return compact
    for separator in ("그러므로", "따라서", "으로써", "함으로써", ". "):
        if separator in compact:
            compact = compact.split(separator, 1)[0]
            break
    return compact[:140].rstrip(" ,.;") + ("..." if len(compact) > 140 else "")
