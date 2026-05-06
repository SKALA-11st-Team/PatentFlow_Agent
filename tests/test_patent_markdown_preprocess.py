from pathlib import Path

from services.patent.markdown_preprocess_service import preprocess_markdown_file


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
