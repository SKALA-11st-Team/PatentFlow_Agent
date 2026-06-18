from __future__ import annotations

import logging
from typing import Any

from app.config import settings


log = logging.getLogger(__name__)


# @author 배세은
# @date 2026-06-04
# @relatedFR FR-001
# @relatedUI UI-005
# @description BE 공유 DB(patentflow.patents)에서 patent_id로 특허 식별자·기본 정보
# (관리번호·출원/등록번호·제목·사업/기술 분야)를 조회한다. psycopg/DB URL 미설정 시
# 경고 후 None 반환(평가 흐름을 막지 않는 선택적 보강 조회).
def get_patent_identifiers(patent_id: str) -> dict[str, Any] | None:
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError:
        log.warning("psycopg가 설치되어 있지 않아 공유 DB 조회를 건너뜁니다.")
        return None

    db_url = settings.pgvector_database_url
    if not db_url:
        log.warning("pgvector_database_url이 설정되지 않아 공유 DB 조회를 건너뜁니다.")
        return None

    try:
        with psycopg.connect(db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        patent_id,
                        management_number,
                        application_number,
                        registration_number,
                        title,
                        business_area,
                        technology_area
                    FROM patentflow.patents
                    WHERE patent_id = %s
                    LIMIT 1
                    """,
                    (patent_id,),
                )
                row = cur.fetchone()
                return dict(row) if row else None
    except Exception as exc:
        log.warning("공유 DB 조회 실패 (patent_id=%s): %s", patent_id, exc)
        return None
