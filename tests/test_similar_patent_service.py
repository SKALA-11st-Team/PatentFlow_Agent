from services.patent import similar_patent_service


def test_collect_similar_patent_pdfs_exposes_claims_and_technical_content(monkeypatch, tmp_path):
    monkeypatch.setattr(
        similar_patent_service,
        "download_and_parse_patent_pdf",
        lambda *args, **kwargs: {
            "pdf_path": str(tmp_path / "similar.pdf"),
            "markdown_paths": [],
            "markdown_text": """
## 청구범위
청구항 1 입력을 분석하는 프로세서를 포함하는 시스템.
청구항 2 청구항 1에 있어서, 분석 결과를 저장하는 시스템.
## 해결하려는 과제
분석 정확도를 높인다.
## 과제의 해결 수단
입력 특징과 관계를 함께 분석한다.
## 발명의 효과
관계 설명력이 향상된다.
## 발명을 실시하기 위한 구체적인 내용
프로세서는 특징 벡터와 관계 행렬을 계산한다.
""",
        },
    )

    enriched, warnings = similar_patent_service.collect_similar_patent_pdfs(
        [{"application_number": "1020200000001", "title": "유사 특허", "status": "등록"}],
        output_dir=tmp_path,
        text_limit=None,
    )

    assert warnings == []
    assert [claim["claim_no"] for claim in enriched[0]["representative_claims"]] == [1, 2]
    assert enriched[0]["technical_content"] == {
        "problem": "분석 정확도를 높인다.",
        "solution": "입력 특징과 관계를 함께 분석한다.",
        "effect": "관계 설명력이 향상된다.",
        "detailed_description": "프로세서는 특징 벡터와 관계 행렬을 계산한다.",
    }
