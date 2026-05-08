import json
import sqlite3

from services.patent.portfolio_service import analyze_portfolio_siblings, find_sibling_patents


def create_patent_db(path):
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE patents (
                id INTEGER PRIMARY KEY,
                management_number TEXT,
                application_number TEXT,
                registration_number TEXT,
                title_final TEXT,
                business_area TEXT,
                technology_area TEXT,
                related_product TEXT,
                country TEXT,
                joint_application TEXT,
                joint_applicant_name TEXT,
                status TEXT,
                application_date TEXT,
                registration_date TEXT,
                expected_expiration_date TEXT,
                source_file_id INTEGER,
                title_draft TEXT,
                evaluation_status TEXT,
                data_source_status TEXT
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO patents (
                id, management_number, application_number, registration_number, title_final,
                business_area, technology_area, related_product, country, joint_application,
                joint_applicant_name, status, application_date, registration_date,
                expected_expiration_date, source_file_id, title_draft, evaluation_status,
                data_source_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    1,
                    "M1",
                    "10-2024-0000001",
                    "10-3000001",
                    "예측 모델 특허",
                    "금융/전략",
                    "AI/Bigdata",
                    "MarketCaster",
                    "KR",
                    "N",
                    None,
                    "등록",
                    "2024-01-01",
                    "2026-01-01",
                    None,
                    1,
                    "예측 모델 특허",
                    None,
                    None,
                ),
                (
                    2,
                    "M2",
                    "10-2024-0000002",
                    "10-3000002",
                    "이벤트 추출 특허",
                    "금융/전략",
                    "AI/Bigdata",
                    "MarketCaster",
                    "KR",
                    "N",
                    None,
                    "등록",
                    "2024-01-01",
                    "2026-01-01",
                    None,
                    1,
                    "이벤트 추출 특허",
                    None,
                    None,
                ),
                (
                    3,
                    "M3",
                    "10-2024-0000003",
                    "10-3000003",
                    "기타 특허",
                    "금융/전략",
                    "AI/Bigdata",
                    "기타",
                    "KR",
                    "N",
                    None,
                    "등록",
                    "2024-01-01",
                    "2026-01-01",
                    None,
                    1,
                    "기타 특허",
                    None,
                    None,
                ),
            ],
        )


def test_find_sibling_patents_excludes_target_and_misc_products(tmp_path):
    db_path = tmp_path / "patents.sqlite3"
    create_patent_db(db_path)

    siblings = find_sibling_patents(
        {
            "id": 1,
            "related_product": "MarketCaster",
            "application_date": "2024-01-01",
            "business_area": "금융/전략",
            "technology_area": "AI/Bigdata",
        },
        database_path=db_path,
    )
    misc_siblings = find_sibling_patents({"id": 3, "related_product": "기타"}, database_path=db_path)

    assert [sibling["id"] for sibling in siblings] == [2]
    assert misc_siblings == []


def test_find_sibling_patents_includes_same_management_batch(tmp_path):
    db_path = tmp_path / "patents.sqlite3"
    create_patent_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO patents (
                id, management_number, application_number, registration_number, title_final,
                business_area, technology_area, related_product, country, joint_application,
                joint_applicant_name, status, application_date, registration_date,
                expected_expiration_date, source_file_id, title_draft, evaluation_status,
                data_source_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    106,
                    "P201210016-KR0",
                    "10-2012-0135192",
                    "10-1450116",
                    "브로드캐스팅을 이용한 시분할 방식의 배터리 관리 시스템 및 방법",
                    "기존사업",
                    "Green IT （ESS）",
                    "ESS",
                    "KR",
                    "N",
                    None,
                    "등록",
                    "2012-11-27",
                    "2014-10-06",
                    "2032-11-27",
                    1,
                    None,
                    None,
                    None,
                ),
                (
                    107,
                    "P201210015-KR0",
                    "10-2012-0136202",
                    "10-1430134",
                    "배터리 관리 방법 및 시스템",
                    "기존사업",
                    "Green IT （ESS）",
                    "ESS（Flow Battery）",
                    "KR",
                    "N",
                    None,
                    "등록",
                    "2012-11-28",
                    "2014-08-07",
                    "2032-11-28",
                    1,
                    None,
                    None,
                    None,
                ),
                (
                    108,
                    "P201210014-KR0",
                    "10-2012-0135182",
                    "10-1416798",
                    "계층적 구조를 가지는 배터리 관리 시스템 및 방법",
                    "기존사업",
                    "Green IT （ESS）",
                    "ESS",
                    "KR",
                    "N",
                    None,
                    "등록",
                    "2012-11-27",
                    "2014-07-02",
                    "2032-11-27",
                    1,
                    None,
                    None,
                    None,
                ),
                (
                    109,
                    "P201210013-KR0",
                    "10-2012-0135175",
                    "10-1478791",
                    "전력 관리 방법 및 시스템",
                    "기존사업",
                    "Green IT （EMS）",
                    "EMS",
                    "KR",
                    "N",
                    None,
                    "등록",
                    "2012-11-27",
                    "2014-12-26",
                    "2032-11-27",
                    1,
                    None,
                    None,
                    None,
                ),
                (
                    110,
                    "P201210012-KR0",
                    "10-2012-0136211",
                    "10-1416816",
                    "절연 저항 측정 장치 및 그 방법",
                    "기존사업",
                    "Green IT （BMS）",
                    "BMS",
                    "KR",
                    "N",
                    None,
                    "등록",
                    "2012-11-28",
                    "2014-07-02",
                    "2032-11-28",
                    1,
                    None,
                    None,
                    None,
                ),
            ],
        )

    siblings = find_sibling_patents(
        {
            "id": 106,
            "management_number": "P201210016-KR0",
            "related_product": "ESS",
            "application_date": "2012-11-27",
        },
        database_path=db_path,
    )

    assert [sibling["id"] for sibling in siblings] == [108, 107, 109, 110]
    assert siblings[0]["sibling_match_reasons"] == [
        "same_related_product",
        "same_management_batch",
    ]
    assert siblings[-1]["sibling_match_reasons"] == ["same_management_batch"]


def test_analyze_portfolio_siblings_builds_portfolio_context(tmp_path):
    db_path = tmp_path / "patents.sqlite3"
    create_patent_db(db_path)
    captured_prompts = []

    def fake_kipris_fetcher(application_number):
        return {
            "metadata": {
                "title": "이벤트 추출 특허",
                "assignee": ["에스케이 주식회사"],
                "ipc": ["G06Q 40/06"],
                "cpc": ["G06Q 40/06"],
            },
            "sections": {"abstract": "금융시장 이벤트를 추출한다."},
            "claims": [{"text": "뉴스 이벤트를 추출하는 방법", "is_independent": True}],
            "claim_stats": {"active_claim_count": 1},
        }

    def fake_llm(prompt):
        captured_prompts.append(prompt)
        return json.dumps(
            {
                "compressed_summary": "MarketCaster 특허군은 예측과 이벤트 추출 기능을 나눠 보호한다.",
                "key_facts": ["동일 제품군 sibling 특허가 존재한다."],
                "sibling_patents": [
                    {
                        "patent_id": 2,
                        "application_number": "10-2024-0000002",
                        "title": "이벤트 추출 특허",
                        "portfolio_role": "예측 설명을 위한 이벤트 추출 기능 보호",
                        "covered_capability": "event extraction",
                        "relation_to_target": "대상 특허의 예측 기능을 보완",
                    }
                ],
            },
            ensure_ascii=False,
        )

    result = analyze_portfolio_siblings(
        target_patent={
            "id": 1,
            "application_number": "10-2024-0000001",
            "title_final": "예측 모델 특허",
            "related_product": "MarketCaster",
            "application_date": "2024-01-01",
            "business_area": "금융/전략",
            "technology_area": "AI/Bigdata",
        },
        database_path=db_path,
        llm=fake_llm,
        kipris_fetcher=fake_kipris_fetcher,
    )

    item = result["items"][0]
    assert item["source_type"] == "portfolio_context"
    assert item["group_size"] == 2
    assert "target" not in result
    assert "siblings" not in result
    assert item["compressed_summary"] == "MarketCaster 특허군은 예측과 이벤트 추출 기능을 나눠 보호한다."
    assert item["sibling_patents"][0]["portfolio_role"] == "예측 설명을 위한 이벤트 추출 기능 보호"
    assert "sibling_match_reasons" not in captured_prompts[0]
    assert "금융시장 이벤트를 추출한다." in captured_prompts[0]
    assert "뉴스 이벤트를 추출하는 방법" in captured_prompts[0]
