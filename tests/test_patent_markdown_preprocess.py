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


def test_preprocess_extracts_chinese_patent_sections():
    raw_text = """
(57)摘要

提供了一种测量控制方法和系统。

技术领域

[0001] 本公开涉及半导体测量相关技术。

背景技术

[0002] 固定周期测量没有考虑设备质量。

发明内容

[0003] 本公开提供基于设备可靠性指数的动态测量方法。

附图说明

[0004] 图1是流程图。

具体实施方式

[0005] 系统实时计算风险分数。
"""

    result = build_preprocessed_patent(
        raw_text,
        db_metadata={
            "country": "CN",
            "application_number": "201880038342.9",
            "registration_number": "CN110770661B",
            "title_final": "测量控制方法和系统",
        },
        api_data={
            "metadata": {"country": "CN"},
            "claims": [
                {
                    "claim_no": 1,
                    "text": "一种测量控制方法。",
                    "is_independent": True,
                    "dependency": None,
                }
            ],
            "claim_stats": {"active_claim_count": 1},
        },
    )

    assert result["sections"]["abstract"] == "提供了一种测量控制方法和系统。"
    assert "半导体测量相关技术" in result["sections"]["technical_field"]
    assert "固定周期测量" in result["sections"]["background_art"]
    assert "设备可靠性指数" in result["sections"]["solution"]
    assert "实时计算风险分数" in result["sections"]["detailed_description"]
    assert "sections.claims_text" not in result["validation"]["missing_fields"]


def test_preprocess_uses_first_chinese_drawing_as_representative():
    raw_text = """
附图说明

图1是控制方法的流程图。

### 图1

![image 28](<CN110770661B_images/imageFile28.png>)

### 图2

![image 31](<CN110770661B_images/imageFile31.png>)
"""

    result = build_preprocessed_patent(
        raw_text,
        source={"markdown_paths": ["/tmp/CN110770661B.md"]},
    )

    representative = result["drawing_context"]["representative_drawing"]
    assert representative == {
        "figure_number": "도1",
        "image_path": "CN110770661B_images/imageFile28.png",
        "image_source": "foreign_drawing_section",
        "markdown_path": "/tmp/CN110770661B.md",
    }


def test_preprocess_extracts_japanese_inline_bracket_sections():
    raw_text = """
(57)【特許請求の範囲】 【請求項１】 計測制御方法。
【発明の詳細な説明】 【技術分野】 【０００１】 本発明は半導体計測に関する。
【背景技術】 【０００２】 固定周期の計測には問題がある。
【発明の概要】 【発明が解決しようとする課題】 【０００３】 動的な計測を提供する。
【課題を解決するための手段】 【０００４】 装備信頼指数を算定する。
【発明の効果】 【０００５】 品質を向上できる。
【発明を実施するための形態】 【０００６】 リスクスコアを計算する。
"""

    result = build_preprocessed_patent(
        raw_text,
        db_metadata={
            "country": "JP",
            "application_number": "2019-565924",
            "registration_number": "6947850",
            "title_final": "計測制御方法及びシステム",
        },
        api_data={
            "metadata": {"country": "JP"},
            "sections": {"abstract": "装備信頼指数に基づく計測制御技術。"},
            "claims": [
                {
                    "claim_no": 1,
                    "text": "計測制御方法。",
                    "is_independent": True,
                    "dependency": None,
                }
            ],
            "claim_stats": {"active_claim_count": 1},
        },
    )

    assert "半導体計測" in result["sections"]["technical_field"]
    assert "固定周期" in result["sections"]["background_art"]
    assert "動的な計測" in result["sections"]["problem"]
    assert "装備信頼指数" in result["sections"]["solution"]
    assert "品質を向上" in result["sections"]["effect"]
    assert "リスクスコア" in result["sections"]["detailed_description"]
    assert result["validation"]["is_valid"] is True


def test_preprocess_extracts_us_patent_sections():
    raw_text = """
(57) ABSTRACT

A system identifies associations from document data.

FIELD OF THE INVENTION

The invention relates to document analysis.

BACKGROUND OF THE INVENTION

Existing systems do not explain associations.

SUMMARY OF THE INVENTION

The system extracts factors and identifies an association.

BRIEF DESCRIPTION OF THE DRAWINGS

FIG. 1 is a system diagram.

DETAILED DESCRIPTION OF THE EMBODIMENTS

The processor analyzes document data.
"""

    result = build_preprocessed_patent(
        raw_text,
        db_metadata={
            "country": "US",
            "application_number": "18/020,829",
            "registration_number": "12,417,849",
            "title_final": "Method for Identifying Association",
        },
        api_data={
            "metadata": {"country": "US"},
            "claims": [
                {
                    "claim_no": 1,
                    "text": "A system comprising a processor.",
                    "is_independent": True,
                    "dependency": None,
                }
            ],
            "claim_stats": {"active_claim_count": 1},
        },
    )

    assert result["sections"]["abstract"] == "A system identifies associations from document data."
    assert "document analysis" in result["sections"]["technical_field"]
    assert "do not explain associations" in result["sections"]["background_art"]
    assert "extracts factors" in result["sections"]["solution"]
    assert "processor analyzes" in result["sections"]["detailed_description"]


import pytest

from services.patent.markdown_preprocess_service import _extract_claim_dependency


@pytest.mark.parametrize(
    "text, expected",
    [
        # 종속 인용 — 인용 종결어미를 동반하므로 종속항으로 인식한다.
        ("제1항에 있어서, 상기 방법은", 1),
        ("제2항에 따른 장치", 2),
        ("제3항에 기재된 시스템", 3),
        ("청구항 1에 있어서", 1),
        ("제1항 또는 제2항에 있어서", 1),
        ("제1항 내지 제3항 중 어느 한 항에 있어서", 1),
        ("제1항의 방법을 수행하는 장치", 1),
        # 독립항 — 인용 종결어미가 없는 구성요소 나열은 종속으로 오판하지 않는다.
        ("제1 또는 제2 위치에 배치되는 부재를 포함하는 장치", None),
        ("제1 단계 및 제2 단계를 포함하는 방법", None),
        ("복수의 항목을 포함하고", None),
    ],
)
def test_extract_claim_dependency_requires_citation_terminator(text, expected):
    assert _extract_claim_dependency(text) == expected
