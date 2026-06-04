from __future__ import annotations

import logging
from typing import Any

from app.config import settings


log = logging.getLogger(__name__)


def get_patent_identifiers(patent_id: str) -> dict[str, Any] | None:
    try:
        import psycopg2
        import psycopg2.extras
    except ImportError:
        log.warning("psycopg2가 설치되어 있지 않아 공유 DB 조회를 건너뜁니다.")
        return None

    db_url = settings.pgvector_database_url
    if not db_url:
        log.warning("pgvector_database_url이 설정되지 않아 공유 DB 조회를 건너뜁니다.")
        return None

    try:
        with psycopg2.connect(db_url) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
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
