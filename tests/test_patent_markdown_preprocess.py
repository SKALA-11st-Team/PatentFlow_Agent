from pathlib import Path

from services.patent.markdown_preprocess_service import build_preprocessed_patent, preprocess_markdown_file


def test_extracts_split_assignees_and_inventors():
    result = preprocess_markdown_file(
        Path(__file__).parent / "fixtures" / "patent_markdown" / "1020230093778.md"
    )

    assert result["metadata"]["assignee"] == ["에스케이 주식회사", "제닉스로보틱스 주식회사"]
    assert result["metadata"]["assignee_count"] == 2
    assert result["metadata"]["has_co_assignee"] is True
    assert result["metadata"]["inventors"] == ["반재만", "배성관", "도건", "박준호", "박성재"]
    assert result["metadata"]["prior_art"] == [
        "KR100182242 B1",
        "KR1020080024887 A",
        "KR1020070030529 A",
        "JP2003321102 A",
        "US20230116896 A1",
    ]
    assert result["claim_stats"]["active_claim_numbers"] == [1, 4]
    assert result["claim_stats"]["has_deleted_claims_gap"] is True
    assert not any("possible_missing_assignees" in warning for warning in result["validation"]["warnings"])
    assert not any("possible_missing_inventors" in warning for warning in result["validation"]["warnings"])


def test_preprocess_keeps_db_business_and_product_context():
    result = preprocess_markdown_file(
        Path(__file__).parent / "fixtures" / "patent_markdown" / "1020230093778.md",
        db_metadata={
            "id": 1,
            "management_number": "P202405001-KR0",
            "business_area": "AI",
            "technology_area": "AI 자산운용",
            "related_product": "투자서비스",
            "joint_application": 1,
            "joint_applicant_name": "공동출원사",
            "status": "등록",
            "application_date": "2024-08-29",
            "expected_expiration_date": "2044-08-29",
        },
    )

    metadata = result["metadata"]
    assert metadata["management_number"] == "P202405001-KR0"
    assert metadata["business_area"] == "AI"
    assert metadata["technology_area"] == "AI 자산운용"
    assert metadata["related_product"] == "투자서비스"
    assert metadata["joint_application"] == 1
    assert metadata["joint_applicant_name"] == "공동출원사"


def test_preprocess_preserves_representative_drawing_context():
    raw_text = """
등록특허 10-3000001

(뒷면에 계속) 대 표 도 - 도4

![image 4](<1020210131424_images/imageFile4.png>)

# 도면의 간단한 설명

[0019] 도 4는 본 발명의 일 실시예에 따른 처리 흐름을 나타내는 도면이다.

# 발명을 실시하기 위한 구체적인 내용

[0068] 도 3은 비교용 처리 흐름을 나타낸다.

[0069] 도 4를 참조하면, 입력 문장을 토큰화하고(S410), 품사를 판별해 숫자 토큰을 추출한다(S420).

[0070] 추출된 토큰은 사전을 기반으로 숫자 형태로 치환되어 리스트에 저장된다(S430).

[0071] 도 5를 참조하면, 다른 실시예의 후처리를 수행한다.

도면

- 도면4

![image 8](<1020210131424_images/imageFile8.png>)

# 청구범위

- 청구항 1 사용자 단말과 서버를 포함하는 시스템.
"""

    result = build_preprocessed_patent(
        raw_text,
        source={
            "markdown_paths": [
                "/tmp/run/patent_markdown/10-2021-0131424/1020210131424.md",
            ],
        },
    )

    drawing_context = result["drawing_context"]
    representative = drawing_context["representative_drawing"]

    assert representative["figure_number"] == "도4"
    assert representative["image_path"] == "1020210131424_images/imageFile8.png"
    assert representative["image_source"] == "drawing_section"
    assert representative["markdown_path"].endswith("1020210131424.md")
    assert "도 4는 본 발명의 일 실시예에 따른 처리 흐름" in drawing_context["figure_description"]
    assert "입력 문장을 토큰화하고(S410)" in drawing_context["representative_figure_detail"]
    assert "리스트에 저장된다(S430)" in drawing_context["representative_figure_detail"]
    assert "도 3은 비교용" not in drawing_context["representative_figure_detail"]
    assert "도 5를 참조하면" not in drawing_context["representative_figure_detail"]


def test_preprocess_uses_cover_representative_when_numbered_drawing_is_missing():
    raw_text = """
(뒷면에 계속) 대 표 도 - 도4

![image 4](<1020210131424_images/imageFile4.png>)

도면

- 도면1

![image 5](<1020210131424_images/imageFile5.png>)
"""

    result = build_preprocessed_patent(raw_text)
    representative = result["drawing_context"]["representative_drawing"]

    assert representative["figure_number"] == "도4"
    assert representative["image_path"] == "1020210131424_images/imageFile4.png"
    assert representative["image_source"] == "cover_representative"


def test_preprocess_uses_cover_image_when_representative_has_no_figure_number():
    raw_text = """
등록특허 10-2042318

대 표 도

![image 4](<1020170168335_images/imageFile4.png>)

# 도면의 간단한 설명

[0028] 도 1은 본 발명의 일 실시예에 따른 스마트 팩토리 레이아웃 설계 방법의 설명에 제공되는 흐름도이다.

도면

- 도면1

![image 12](<1020170168335_images/imageFile12.png>)
"""

    result = build_preprocessed_patent(raw_text)
    representative = result["drawing_context"]["representative_drawing"]

    assert representative["figure_number"] == "대표도"
    assert representative["image_path"] == "1020170168335_images/imageFile4.png"
    assert representative["image_source"] == "cover_representative"
    assert "representative_figure_detail" not in result["drawing_context"]


def test_preprocess_prefers_ordered_drawing_section_image_over_cover_thumbnail():
    raw_text = """
(뒷면에 계속) 대 표 도 - 도4

![image 4](<1020210131424_images/imageFile4.png>)

도면

- 도면1

![image 5](<1020210131424_images/imageFile5.png>)

- 도면2

![image 6](<1020210131424_images/imageFile6.png>)

![image 7](<1020210131424_images/imageFile7.png>)

![image 8](<1020210131424_images/imageFile8.png>)
"""

    result = build_preprocessed_patent(raw_text)
    representative = result["drawing_context"]["representative_drawing"]

    assert representative["figure_number"] == "도4"
    assert representative["image_path"] == "1020210131424_images/imageFile8.png"
    assert representative["image_source"] == "drawing_section_order"
