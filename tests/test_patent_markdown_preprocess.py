from pathlib import Path

from services.patent.markdown_preprocess_service import (
    build_preprocessed_patent,
    extract_foreign_frontpage_metadata,
    extract_us_patent_sections,
    preprocess_markdown_file,
)


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


def test_extract_foreign_frontpage_metadata_reads_claim_count_classifications_and_applicant():
    text = """
United States Patent
US 12,032,469 B2
9 Claims
Int. Cl.
G06N 5/045
CPC
G06F 11/3457
G06F 11/302
G06F 11/3086
G06N 5/045
Applicant: SK HOLDINGS CO., LTD.
Inventors: John Doe; Jane Roe
"""

    metadata = extract_foreign_frontpage_metadata(text)

    assert metadata["claim_count"] == 9
    assert metadata["ipc"] == ["G06N 5/045"]
    assert metadata["cpc"] == []
    assert metadata["assignee"] == ["SK HOLDINGS CO., LTD."]
    assert metadata["inventors"] == ["John Doe", "Jane Roe"]


def test_build_preprocessed_patent_extracts_foreign_abstract_and_frontpage_people():
    raw_text = """
United States Patent
US 12,032,469 B2
9 Claims
Int. Cl.
G06N 5/045
CPC
G06F 11/3457
G06F 11/302
Applicant: SK HOLDINGS CO., LTD.
Inventors: Byung Min Lee; Young Hee Kim; Jong Moon Kim; Ki Peum Chun

ABSTRACT
An explainable artificial intelligence platform visualizes workflows, compares simulations in real time, and deploys an optimal model continuously.

What is claimed is:
1. An explainable artificial intelligence (AI) modeling and simulation method comprising designing an AI workflow model.
2. The method of claim 1, wherein the AI workflow model is visualized.
9. An explainable artificial intelligence (AI) modeling and simulation system, comprising a storage unit and a processor.

Description
This description explains embodiments in detail.
"""

    result = build_preprocessed_patent(
        raw_text,
        db_metadata={
            "country": "US",
            "application_number": "17/420,237",
            "registration_number": "12,032,469",
            "title_final": "EXPLAINABLE ARTIFICIAL INTELLIGENCE MODELING AND SIMULATION SYSTEM AND METHOD",
        },
        api_data={
            "metadata": {
                "country": "US",
                "application_number": "17/420,237",
                "registration_number": "12,032,469",
                "title": "EXPLAINABLE ARTIFICIAL INTELLIGENCE MODELING AND SIMULATION SYSTEM AND METHOD",
                "ipc": [],
                "cpc": [],
                "assignee": [],
                "inventors": [],
            },
            "sections": {"abstract": ""},
            "claims": [],
            "claim_stats": {},
        },
    )

    assert result["sections"]["abstract"].startswith("An explainable artificial intelligence platform")
    assert result["metadata"]["cpc"] == []
    assert result["metadata"]["ipc"] == ["G06N 5/045"]
    assert result["metadata"]["assignee"] == ["SK HOLDINGS CO., LTD."]
    assert result["metadata"]["inventors"] == [
        "Byung Min Lee",
        "Young Hee Kim",
        "Jong Moon Kim",
        "Ki Peum Chun",
    ]


def test_build_preprocessed_patent_keeps_frontpage_metadata_out_of_foreign_abstract():
    raw_text = """
United States Patent
US 12,032,469 B2
(54) EXPLAINABLE ARTIFICIAL INTELLIGENCE MODELING AND SIMULATION SYSTEM AND METHOD
(71) Applicant: SK HOLDINGS CO., LTD., Seoul (KR)
(72) Inventors: Byung Min Lee, Cheonan-si (KR); Young Hee Kim, Seoul (KR); Jong Moon Kim, Cheonan-si (KR); Ki Peum Chun, Yongin-si (KR)
(58) Field of Classification Search
Int. Cl.
G06N 5/045
CPC
G06F 11/3457
G06F 11/302
G06F 11/3086
9 Claims

ABSTRACT
Provided is a system and method that generates an artificial intelligence workflow model in which image conversion, measurement, and image searches can be performed, and that conducts a simulation of the generated model.

(56) References Cited
U.S. PATENT DOCUMENTS

U.S. Patent Jul. 9, 2024 Sheet 2 of 16 US 12,032,469 B2
TECHNICAL FIELD
The present disclosure relates to explainable artificial intelligence (AI) technology.

What is claimed is:
1. An explainable artificial intelligence (AI) modeling and simulation method comprising designing an AI workflow model.
9. An explainable artificial intelligence (AI) modeling and simulation system, comprising a storage unit and a processor.
"""

    result = build_preprocessed_patent(
        raw_text,
        db_metadata={
            "country": "US",
            "application_number": "17/420,237",
            "registration_number": "12,032,469",
            "title_final": "EXPLAINABLE ARTIFICIAL INTELLIGENCE MODELING AND SIMULATION SYSTEM AND METHOD",
        },
        api_data={
            "metadata": {
                "country": "US",
                "application_number": "17/420,237",
                "registration_number": "12,032,469",
                "title": "EXPLAINABLE ARTIFICIAL INTELLIGENCE MODELING AND SIMULATION SYSTEM AND METHOD",
                "ipc": [],
                "cpc": [],
                "assignee": [],
                "inventors": [],
            },
            "sections": {"abstract": ""},
            "claims": [],
            "claim_stats": {},
        },
    )

    assert result["metadata"]["ipc"] == ["G06N 5/045"]
    assert result["metadata"]["cpc"] == []
    assert result["metadata"]["assignee"] == ["SK HOLDINGS CO., LTD."]
    assert result["metadata"]["inventors"] == [
        "Byung Min Lee",
        "Young Hee Kim",
        "Jong Moon Kim",
        "Ki Peum Chun",
    ]
    assert result["sections"]["abstract"].startswith("Provided is a system and method")
    assert "Int. Cl." not in result["sections"]["abstract"]
    assert "CPC" not in result["sections"]["abstract"]
    assert "References Cited" not in result["sections"]["abstract"]


def test_extract_foreign_frontpage_metadata_stops_before_body_even_without_second_page_marker():
    text = """
United States Patent
US 12,032,469 B2
9 Claims
Int. Cl.
G06N 5/045
CPC
G06F 11/3457
Applicant: SK HOLDINGS CO., LTD.
Inventors: John Doe; Jane Roe

ABSTRACT
An explainable artificial intelligence platform for workflow simulation.

TECHNICAL FIELD
The present disclosure relates to explainable artificial intelligence technology.
"""

    metadata = extract_foreign_frontpage_metadata(text)
    result = build_preprocessed_patent(
        text,
        db_metadata={
            "country": "US",
            "application_number": "17/420,237",
            "registration_number": "12,032,469",
            "title_final": "EXPLAINABLE ARTIFICIAL INTELLIGENCE MODELING AND SIMULATION SYSTEM AND METHOD",
        },
        api_data={
            "metadata": {
                "country": "US",
                "application_number": "17/420,237",
                "registration_number": "12,032,469",
                "title": "EXPLAINABLE ARTIFICIAL INTELLIGENCE MODELING AND SIMULATION SYSTEM AND METHOD",
                "ipc": [],
                "cpc": [],
                "assignee": [],
                "inventors": [],
            },
            "sections": {"abstract": ""},
            "claims": [],
            "claim_stats": {},
        },
    )

    assert metadata["ipc"] == ["G06N 5/045"]
    assert metadata["cpc"] == []
    assert metadata["assignee"] == ["SK HOLDINGS CO., LTD."]
    assert metadata["inventors"] == ["John Doe", "Jane Roe"]
    assert result["sections"]["abstract"] == "An explainable artificial intelligence platform for workflow simulation."


def test_uspto_front_page_parser_extracts_common_fields_and_metadata_sources():
    raw_text = """
United States Patent
Patent No.: US 12,032,469 B2
Date of Patent: Jul. 9, 2024
(54) EXPLAINABLE ARTIFICIAL INTELLIGENCE MODELING AND SIMULATION SYSTEM AND METHOD
(71) Applicant: SK HOLDINGS CO., LTD.
(72) Inventors: Byung Min Lee; Young Hee Kim; Jong Moon Kim; Ki Peum Chun
Appl. No.: 17/420,237
Filed: Jul. 1, 2021
(51) Int. Cl.
G06N 5/045 (2023.01)
(52) U.S. Cl.
CPC G06F 11/3457 ; G06F 11/302 ; G06F 11/3086
(57) ABSTRACT
An explainable artificial intelligence platform visualizes workflows and compares simulations in real time.

The invention claimed is:
1. An explainable artificial intelligence (AI) modeling and simulation method comprising designing an AI workflow model.
2. The method of claim 1, wherein the AI workflow model is visualized.
9. An explainable artificial intelligence (AI) modeling and simulation system, comprising a storage unit and a processor.

TECHNICAL FIELD
The present disclosure relates to explainable artificial intelligence technology.
"""

    result = build_preprocessed_patent(
        raw_text,
        db_metadata={
            "country": "US",
            "application_number": "17/420,237",
            "registration_number": "12,032,469",
            "title_final": "EXPLAINABLE ARTIFICIAL INTELLIGENCE MODELING AND SIMULATION SYSTEM AND METHOD",
        },
        api_data={
            "metadata": {
                "country": "US",
                "application_number": "17/420,237",
                "registration_number": "12,032,469",
                "title": "",
                "ipc": [],
                "cpc": [],
                "assignee": [],
                "inventors": [],
                "registration_date": "",
            },
            "sections": {"abstract": ""},
            "claims": [],
            "claim_stats": {},
        },
    )

    metadata = result["metadata"]
    assert metadata["title"] == "EXPLAINABLE ARTIFICIAL INTELLIGENCE MODELING AND SIMULATION SYSTEM AND METHOD"
    assert metadata["patent_number"] == "US 12,032,469 B2"
    assert metadata["registration_date"] == "Jul. 9, 2024"
    assert metadata["ipc"] == ["G06N 5/045"]
    assert metadata["cpc"] == []
    assert metadata["assignee"] == ["SK HOLDINGS CO., LTD."]
    assert metadata["inventors"] == [
        "Byung Min Lee",
        "Young Hee Kim",
        "Jong Moon Kim",
        "Ki Peum Chun",
    ]
    assert metadata["metadata_source"]["ipc"] == "ocr_front_page"
    assert metadata["metadata_source"]["cpc"] == ""
    assert metadata["metadata_source"]["assignee"] == "ocr_front_page"
    assert result["sections"]["abstract"].startswith("An explainable artificial intelligence platform")
    assert result["sections"]["claims_text"].startswith("1. An explainable artificial intelligence")
    assert [claim["claim_no"] for claim in result["claims"]] == [1, 2, 9]
    assert result["claim_stats"]["independent_claim_numbers"] == [1, 9]
    assert "TECHNICAL FIELD" not in result["sections"]["claims_text"]
    assert result["sections"]["technical_field"].startswith("The present disclosure relates")


def test_extract_us_patent_sections_keeps_pre_claim_text_out_of_claims():
    raw_text = """
United States Patent
(57) ABSTRACT
An explainable AI system.

BACKGROUND ART
Background text that should not enter claims.

The invention claimed is:
1. An explainable artificial intelligence (AI) modeling and simulation method comprising designing an AI workflow model.
2. The method of claim 1, wherein the AI workflow model is visualized.

DESCRIPTION
Detailed description text.
"""

    sections = extract_us_patent_sections(raw_text, cleaned_text=raw_text)

    assert sections["abstract"] == "An explainable AI system."
    assert sections["claims_text"].startswith("1. An explainable artificial intelligence")
    assert "Background text" not in sections["claims_text"]
    assert "ABSTRACT" not in sections["claims_text"]


def test_uspto_abstract_continued_reads_next_page():
    raw_text = """
United States Patent
(57) ABSTRACT
An explainable artificial intelligence platform supports workflow simulation
(Continued)
U.S. Patent Jul. 9, 2024 Sheet 2 of 16 US 12,032,469 B2
and continuous optimization across multiple models.

TECHNICAL FIELD
The present disclosure relates to explainable artificial intelligence technology.
"""

    sections = extract_us_patent_sections(raw_text, cleaned_text=raw_text)

    assert "supports workflow simulation" in sections["abstract"]
    assert "continuous optimization across multiple models." in sections["abstract"]
    assert "TECHNICAL FIELD" not in sections["abstract"]


def test_uspto_abstract_filters_frontpage_noise_lines():
    raw_text = """
United States Patent
(67) ABSTRACT
Provided is a system and method that generates an artificial
US 2022/0066905 Al Mar. 3, 2022
(30) Foreign Application Priority Data
Jan. 4, 2019 (KR) 10-2019-0000998
(51) Int Ch
G06F 11/34 (2006.01)
and conducts a simulation of the generated model.
9 Claims, 16 Drawing Sheets
"""

    sections = extract_us_patent_sections(raw_text, cleaned_text=raw_text)

    assert sections["abstract"].startswith("Provided is a system and method")
    assert "Foreign Application Priority Data" not in sections["abstract"]
    assert "G06F 11/34" not in sections["abstract"]
    assert "9 Claims" not in sections["abstract"]


def test_uspto_abstract_strips_mixed_line_frontpage_noise():
    raw_text = """
United States Patent
(65) Prior Publication Data (67) ABSTRACT Provided is a system and method that generates an artificial US 2022/0066905 Al Mar. 3, 2022 intelligence workflow model.
(30) Foreign Application Priority Data selecting/combining algorithms suitable for a workflow hav-
Jan. 4, 2019 (KR) 10-2019-0000998 as a display manufacturing process.
(51) Int Ch intelligence modelling and simulation method includes
and conducting, when input information is input, a simulation of the artificial intelligence workflow model.
9 Claims, 16 Drawing Sheets
"""

    sections = extract_us_patent_sections(raw_text, cleaned_text=raw_text)

    assert sections["abstract"].startswith("Provided is a system and method")
    assert "US 2022/0066905" not in sections["abstract"]
    assert "Prior Publication Data" not in sections["abstract"]
    assert "Foreign Application Priority Data" not in sections["abstract"]
    assert "10-2019-0000998" not in sections["abstract"]
    assert "Int Ch" not in sections["abstract"]


def test_uspto_ipc_extracts_only_int_cl_block():
    text = """
United States Patent
(51) Int. Cl.
GOOF 11/34 (2006.01)
GO6F 11/30 (2006.01)
GO6N 5/045 (2023.01)
(52) U.S. Cl.
GOOF 11/3457 ; GO6F 11/302 ; GO6F 11/3086 ; A6IB 5/015
"""

    metadata = extract_foreign_frontpage_metadata(text)

    assert metadata["ipc"] == ["G06F 11/34", "G06F 11/30", "G06N 5/045"]
    assert metadata["representative_ipc"] == "G06F 11/34"


def test_uspto_frontpage_classification_normalization_and_fallback():
    text = """
United States Patent
Patent No.: US 12,032,469 B2
(51) Int. Cl.
GO6N 5/045 (2023.01)
(52) U.S. Cl.
GOOF 11/3457 ; A6IB 5/015
(58) Field of Classification Search
"""

    metadata = extract_foreign_frontpage_metadata(text)

    assert metadata["ipc"] == ["G06N 5/045"]
    assert metadata["representative_ipc"] == "G06N 5/045"
    assert metadata["cpc"] == []


def test_build_preprocessed_patent_preserves_representative_ipc_from_frontpage():
    raw_text = """
United States Patent
(51) Int. Cl.
GOOF 11/34 (2006.01)
GO6F 11/30 (2006.01)
GO6N 5/045 (2023.01)
(57) ABSTRACT
An explainable artificial intelligence platform.

The invention claimed is:
1. An explainable artificial intelligence method.
"""

    result = build_preprocessed_patent(
        raw_text,
        db_metadata={
            "country": "US",
            "application_number": "17/420,237",
            "registration_number": "12,032,469",
            "title_final": "EXPLAINABLE ARTIFICIAL INTELLIGENCE MODELING AND SIMULATION SYSTEM AND METHOD",
        },
        api_data={
            "metadata": {
                "country": "US",
                "application_number": "17/420,237",
                "registration_number": "12,032,469",
                "title": "EXPLAINABLE ARTIFICIAL INTELLIGENCE MODELING AND SIMULATION SYSTEM AND METHOD",
                "ipc": [],
                "cpc": [],
                "assignee": [],
                "inventors": [],
            },
            "sections": {"abstract": ""},
            "claims": [],
            "claim_stats": {},
        },
    )

    assert result["metadata"]["representative_ipc"] == "G06F 11/34"
    assert result["metadata"]["metadata_source"]["representative_ipc"] == "ocr_front_page"


def test_validation_warns_on_frontpage_noise_patterns():
    from services.patent.markdown_preprocess_service import validate_preprocessed_patent

    validation = validate_preprocessed_patent(
        {
            "title": "EXPLAINABLE ARTIFICIAL INTELLIGENCE MODELING AND SIMULATION SYSTEM AND METHOD",
            "application_number": "17/420,237",
            "registration_number": "12,032,469",
            "assignee": ["SK HOLDINGS CO., LTD. Notice 705/2 Prokoski"],
            "inventors": ["Byung Min Lee"],
            "ipc": [],
            "cpc": [],
        },
        {
            "abstract": "Foreign Application Priority Data 9 Claims Drawing Sheets",
            "claims_text": "1. An explainable artificial intelligence method.",
            "technical_field": "The present disclosure relates to explainable artificial intelligence technology.",
        },
        [{"claim_no": 1, "text": "An explainable artificial intelligence method.", "is_independent": True, "dependency": None}],
        source_text="Inventors: Byung Min Lee, Seoul (KR); Young Hee Kim, Seoul (KR)",
    )

    assert "assignee_contains_reference_noise" in validation["warnings"]
    assert "abstract_contains_frontpage_noise" in validation["warnings"]
    assert "inventor_multi_value_may_be_truncated" in validation["warnings"]
