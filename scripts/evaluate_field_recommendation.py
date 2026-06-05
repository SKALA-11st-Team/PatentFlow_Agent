"""특허 분야 추천(field_recommendation) 정확도를 실측 평가한다.

로컬 patents.sqlite3 의 라벨링된(business_area / technology_area 가 채워진) 특허를
정답(ground truth)으로 삼아, recommend_fields 가 taxonomy 안에서 얼마나 정확히
분류하는지 측정한다.

핵심 설계:
- 정답 누수 방지: 현재 분류값(business_area/technology_area)을 입력으로 넘기지 않는다
  (넘기면 모델이 그대로 따라할 수 있음). 모델은 제목(+초록)만으로 새로 분류한다.
- 초록 효과 측정: 기본은 applicationNumber 를 넘겨 KIPRIS 초록을 보강하고,
  --no-abstract 면 초록 없이 제목만으로 평가한다. 같은 표본에 두 번 돌려 비교하면
  "초록이 분류 정확도를 얼마나 끌어올리는가"를 정량적으로 볼 수 있다.

실행 예:
    OPENAI_API_KEY=... python scripts/evaluate_field_recommendation.py --limit 30
    OPENAI_API_KEY=... python scripts/evaluate_field_recommendation.py --limit 30 --no-abstract

LLM 키(OPENAI_API_KEY)와 (초록 보강 시) KIPRIS 자격증명이 환경에 있어야 한다.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from agents.field_recommendation import load_taxonomy, recommend_fields  # noqa: E402
from app.config import settings  # noqa: E402

_EXCLUDED = ("", "기타", "etc", "ETC", "기타/미정")


def log_step(message: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="특허 분야 추천 정확도 평가")
    parser.add_argument("--limit", type=int, default=30, help="평가할 특허 표본 수 (기본 30)")
    parser.add_argument("--offset", type=int, default=0, help="표본 시작 오프셋 (기본 0)")
    parser.add_argument(
        "--no-abstract",
        action="store_true",
        help="초록 보강 없이 제목만으로 평가 (KIPRIS 미조회). 초록 효과 대조군용.",
    )
    parser.add_argument("--output", help="결과 JSON 저장 경로. 미지정 시 artifacts/runs/manual 하위.")
    return parser.parse_args()


def load_labeled_patents(limit: int, offset: int) -> list[dict[str, Any]]:
    placeholders = ",".join("?" * len(_EXCLUDED))
    query = f"""
        SELECT management_number, application_number,
               COALESCE(title_final, title_draft, '') AS title,
               business_area, technology_area
        FROM patents
        WHERE business_area   NOT IN ({placeholders})
          AND technology_area NOT IN ({placeholders})
        ORDER BY management_number
        LIMIT ? OFFSET ?
    """
    with sqlite3.connect(settings.patent_db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, (*_EXCLUDED, *_EXCLUDED, limit, offset)).fetchall()
    return [dict(row) for row in rows]


def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    taxonomy = load_taxonomy()
    log_step(
        f"taxonomy 로드: business={len(taxonomy['businessArea'])} "
        f"technology={len(taxonomy['technologyArea'])}"
    )

    patents = load_labeled_patents(args.limit, args.offset)
    log_step(f"평가 표본 {len(patents)}건 (offset={args.offset}, abstract={'OFF' if args.no_abstract else 'ON'})")

    results: list[dict[str, Any]] = []
    biz_hit = tech_hit = both_hit = errors = 0
    confidence_sum = 0.0

    for idx, patent in enumerate(patents, start=1):
        gt_biz = patent["business_area"]
        gt_tech = patent["technology_area"]
        try:
            recommended = recommend_fields(
                title=patent["title"],
                management_number=patent["management_number"],
                # 초록 보강 모드일 때만 applicationNumber 전달 → KIPRIS 초록 조회 유도.
                application_number=None if args.no_abstract else patent["application_number"],
                # 정답 누수 방지: 현재 분류값은 비워서 모델이 새로 분류하게 한다.
                technology_area=None,
                business_area=None,
                taxonomy=taxonomy,
            )
        except Exception as exc:  # LLM/네트워크 실패는 건별로 집계만 하고 계속 진행
            errors += 1
            log_step(f"  [{idx}/{len(patents)}] ERROR {exc.__class__.__name__}: {str(exc)[:120]}")
            results.append({"managementNumber": patent["management_number"], "error": str(exc)[:300]})
            continue

        pred_biz = recommended["businessArea"]
        pred_tech = recommended["technologyArea"]
        biz_ok = pred_biz == gt_biz
        tech_ok = pred_tech == gt_tech
        biz_hit += biz_ok
        tech_hit += tech_ok
        both_hit += biz_ok and tech_ok
        confidence_sum += recommended["confidence"]

        results.append(
            {
                "managementNumber": patent["management_number"],
                "title": patent["title"][:60],
                "businessArea": {"gt": gt_biz, "pred": pred_biz, "hit": biz_ok},
                "technologyArea": {"gt": gt_tech, "pred": pred_tech, "hit": tech_ok},
                "confidence": recommended["confidence"],
            }
        )
        log_step(
            f"  [{idx}/{len(patents)}] biz {'O' if biz_ok else 'X'} ({gt_biz}->{pred_biz}) | "
            f"tech {'O' if tech_ok else 'X'} ({gt_tech}->{pred_tech}) | conf={recommended['confidence']:.2f}"
        )

    scored = len(patents) - errors
    summary = {
        "evaluatedAt": datetime.now(timezone.utc).isoformat(),
        "abstractEnrichment": not args.no_abstract,
        "sampleSize": len(patents),
        "scored": scored,
        "errors": errors,
        "businessAccuracy": round(biz_hit / scored, 4) if scored else None,
        "technologyAccuracy": round(tech_hit / scored, 4) if scored else None,
        "bothAccuracy": round(both_hit / scored, 4) if scored else None,
        "avgConfidence": round(confidence_sum / scored, 4) if scored else None,
        "taxonomySize": {
            "business": len(taxonomy["businessArea"]),
            "technology": len(taxonomy["technologyArea"]),
        },
    }
    return {"summary": summary, "results": results}


def main() -> None:
    args = parse_args()
    report = evaluate(args)
    summary = report["summary"]

    output_path = (
        Path(args.output)
        if args.output
        else PROJECT_ROOT
        / "artifacts"
        / "runs"
        / "manual"
        / f"field_recommendation_eval_{'noabs' if args.no_abstract else 'abs'}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 60)
    print("특허 분야 추천 정확도 (exact match)")
    print("=" * 60)
    print(f"초록 보강     : {'ON' if summary['abstractEnrichment'] else 'OFF (제목만)'}")
    print(f"표본/채점/에러: {summary['sampleSize']} / {summary['scored']} / {summary['errors']}")
    print(f"사업분야 정확도: {summary['businessAccuracy']}  (taxonomy {summary['taxonomySize']['business']}종)")
    print(f"기술분야 정확도: {summary['technologyAccuracy']}  (taxonomy {summary['taxonomySize']['technology']}종)")
    print(f"동시 정답 정확도: {summary['bothAccuracy']}")
    print(f"평균 confidence : {summary['avgConfidence']}")
    print(f"\n결과 저장: {output_path}")


if __name__ == "__main__":
    main()
