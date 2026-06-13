from pathlib import Path

import pytest

from open_api.kipris_client import KiprisClient
from services.patent.kipris_patent_service import (
    apply_foreign_pdf_ocr_fallback,
    decode_google_patents_html_response,
    download_and_parse_foreign_patent_pdf,
    fetch_foreign_patent_rights_data,
    fetch_kipris_bibliography,
    extract_foreign_claims_from_text,
    foreign_fulltext_parse_is_usable,
    foreign_patent_metadata_from_db,
    foreign_target_literature_candidates,
    google_patents_pdf_url,
    google_patents_publication_id,
    google_patents_html_to_markdown,
    normalize_kipris_citations,
    normalize_kipris_citing_documents,
    normalize_foreign_reference_documents,
    parse_single_patent_pdf,
    foreign_reference_candidate_from_text,
    resolve_foreign_prior_art_evidence,
    resolve_citation_evidence,
    _fetch_foreign_claims,
    has_meaningful_pdf_text,
    parse_single_patent_pdf,
    should_exclude_pdf_page_text,
    should_run_ocr_fallback,
    trim_foreign_front_matter,
    find_cached_foreign_patent_pdf,
    _fetch_foreign_claims_from_kipris,
    _foreign_literature_number_candidates,
    _google_patents_figure_urls,
    _google_patents_backward_references,
    _google_patents_forward_references,
    _select_fulltext_pdf,
    fulltext_application_number_candidates,
)


class DocumentPath:
    def __init__(self, *, path=None, doc_name=None, raw=None):
        self.path = path
        self.doc_name = doc_name
        self.raw = raw or {"response": {"header": {"resultCode": "00"}, "body": {"item": ""}}}


class FulltextClient:
    def __init__(self):
        self.calls = []

    def announcement_fulltext_pdf_path(self, application_number):
        self.calls.append(("announcement", application_number))
        return DocumentPath()

    def publication_fulltext_pdf_path(self, application_number):
        self.calls.append(("publication", application_number))
        if application_number == "18/020,829":
            return DocumentPath(path="https://example.com/us.pdf", doc_name="us.pdf")
        return DocumentPath()


class Response:
    text = "<response><body /></response>"
    headers = {}
    content = b""

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=1):
        yield self.content[:chunk_size]

    def close(self):
        return None


class Session:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        return Response()


class ForeignClient:
    def __init__(self):
        self.claim_calls = []
        self.bibliography_calls = []
        self.session = Session()
        self.timeout = 30.0

    def overseas_bibliographic_info(self, literature_number, country_code):
        self.bibliography_calls.append((literature_number, country_code))
        if literature_number == "000012417849B2":
            return {
                "response": {
                    "body": {
                        "item": {
                            "applicationNumber": "18/020,829",
                            "registerNumber": "12,417,849",
                            "inventionTitle": "Method for Identifying Association between Disease-related Factors",
                            "applicationDate": "20210401",
                            "registerDate": "20250610",
                            "claimCount": "1",
                            "astrtCont": "An association identification method using document data.",
                            "ipcNumber": ["G16H 50/20", "G06F 16/2457"],
                            "applicantName": "SK HOLDINGS CO., LTD.",
                            "inventorName": "Hong Gil Dong",
                        }
                    }
                }
            }
        return {"response": {"body": {"items": {}}}}

    def overseas_demand_paragraph(self, literature_number, country_code):
        self.claim_calls.append((literature_number, country_code))
        if literature_number == "000012417849B2":
            return {
                "response": {
                    "body": {
                        "items": {
                            "demandParagraphInfo": [
                                {"claimText": "A system comprising a processor configured to identify associations."}
                            ]
                        }
                    }
                }
            }
        return {"response": {"body": {"items": {}}}}

    def overseas_registration_fulltext(self, literature_number, country_code):
        return {"response": {"body": {"items": {"item": {}}}}}

    def overseas_open_fulltext(self, literature_number, country_code):
        return {"response": {"body": {"items": {"item": {}}}}}

    def overseas_us_patent_documents(self, literature_number, country_code):
        return {"response": {"body": {"items": {}}}}

    def overseas_foreign_patent_documents(self, literature_number, country_code):
        return {"response": {"body": {"items": {}}}}


class GooglePatentsResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


class GooglePatentsSession:
    def __init__(self, text):
        self.text = text
        self.calls = []

    def get(self, url, timeout=None, **kwargs):
        self.calls.append(url)
        return GooglePatentsResponse(self.text)


def test_citation_info_v3_uses_access_key_auth_param():
    client = KiprisClient(service_key="test-key")
    client.session = Session()

    client.citation_info_v3("1020220150081")

    call = client.session.calls[0]
    assert call["url"].endswith("/openapi/rest/CitationService/citationInfoV3")
    assert call["params"]["accessKey"] == "test-key"
    assert "ServiceKey" not in call["params"]
    assert call["params"]["applicationNumber"] == "1020220150081"


def test_citing_info_uses_access_key_auth_param():
    client = KiprisClient(service_key="test-key")
    client.session = Session()

    client.citing_info("1020060089973")

    call = client.session.calls[0]
    assert call["url"].endswith("/openapi/rest/CitingService/citingInfo")
    assert call["params"]["accessKey"] == "test-key"
    assert "ServiceKey" not in call["params"]
    assert call["params"]["standardCitationApplicationNumber"] == "1020060089973"


def test_overseas_demand_paragraph_uses_foreign_bibliographic_access_key():
    client = KiprisClient(service_key="test-key")
    client.session = Session()

    client.overseas_demand_paragraph("000004002589B2", "JP")

    call = client.session.calls[0]
    assert call["url"].endswith("/openapi/rest/ForeignPatentBibliographicService/demandParagraphInfo")
    assert call["params"]["accessKey"] == "test-key"
    assert "ServiceKey" not in call["params"]
    assert call["params"]["literatureNumber"] == "000004002589B2"
    assert call["params"]["countryCode"] == "JP"


def test_overseas_bibliographic_info_uses_foreign_bibliographic_access_key():
    client = KiprisClient(service_key="test-key")
    client.session = Session()

    client.overseas_bibliographic_info("000004002589B2", "JP")

    call = client.session.calls[0]
    assert call["url"].endswith("/openapi/rest/ForeignPatentBibliographicService/bibliographicInfo")
    assert call["params"]["accessKey"] == "test-key"
    assert "ServiceKey" not in call["params"]
    assert call["params"]["literatureNumber"] == "000004002589B2"
    assert call["params"]["countryCode"] == "JP"


def test_overseas_open_fulltext_uses_foreign_image_fulltext_access_key():
    client = KiprisClient(service_key="test-key")
    client.session = Session()

    client.overseas_open_fulltext("000004002589A", "JP")

    call = client.session.calls[0]
    assert call["url"].endswith("/openapi/rest/ForeignPatentImageAndFullTextService/openFullTextInfo")
    assert call["params"]["accessKey"] == "test-key"
    assert "ServiceKey" not in call["params"]
    assert call["params"]["literatureNumber"] == "000004002589A"
    assert call["params"]["countryCode"] == "JP"


def test_overseas_registration_fulltext_uses_foreign_image_fulltext_access_key():
    client = KiprisClient(service_key="test-key")
    client.session = Session()

    client.overseas_registration_fulltext("000004002589B2", "JP")

    call = client.session.calls[0]
    assert call["url"].endswith("/openapi/rest/ForeignPatentImageAndFullTextService/registrationFullTextInfo")
    assert call["params"]["accessKey"] == "test-key"
    assert "ServiceKey" not in call["params"]
    assert call["params"]["literatureNumber"] == "000004002589B2"
    assert call["params"]["countryCode"] == "JP"


def test_foreign_target_literature_candidates_defaults_us_registration_to_b2():
    candidates = foreign_target_literature_candidates(
        {
            "country": "US",
            "registration_number": "12,417,849",
            "application_number": "18/020,829",
            "registration_date": "2026-01-01",
        }
    )

    assert candidates[0]["country_code"] == "US"
    assert candidates[0]["document_number"] == "12417849"
    assert candidates[0]["kind_code"] == "B2"


def test_foreign_reference_candidate_from_pdf_prior_art():
    assert foreign_reference_candidate_from_text("US 2010241261 A1") == {
        "direction": "cited_by_target",
        "country_code": "US",
        "document_number": "2010241261",
        "kind_code": "A1",
        "original_number": "US 2010241261 A1",
        "display_number": "US 2010241261 A1",
        "lookup_source": "foreign_target_pdf_prior_art",
    }


def test_resolve_foreign_prior_art_evidence_returns_claim_details(monkeypatch):
    monkeypatch.setattr(
        "services.patent.kipris_patent_service._kipris_client",
        lambda: ForeignClient(),
    )
    monkeypatch.setattr(
        "services.patent.kipris_patent_service._fetch_foreign_claims",
        lambda client, candidates, **kwargs: [
            {
                "display_number": candidates[0]["display_number"],
                "representative_claims": [{"claim_no": 1, "text": "Prior art claim"}],
            }
        ],
    )

    result = resolve_foreign_prior_art_evidence(["US 2010241261 A1"])

    assert result["foreign_citation_documents"][0]["representative_claims"][0]["text"] == "Prior art claim"
    assert result["prior_art_collection"]["comparison_ready_count"] == 1
    assert result["warnings"] == []


def test_fetch_foreign_claims_falls_back_to_google_patents_pdf(monkeypatch, tmp_path):
    candidate = foreign_reference_candidate_from_text("US 2010241261 A1")
    client = ForeignClient()
    client.session = object()
    client.timeout = 20
    monkeypatch.setattr(
        "services.patent.kipris_patent_service._fetch_foreign_claims_from_kipris",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "services.patent.kipris_patent_service._fetch_foreign_claims_from_bigquery",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "services.patent.kipris_patent_service.google_patents_pdf_url",
        lambda *args, **kwargs: "https://example.com/prior-art.pdf",
    )
    monkeypatch.setattr(
        "services.patent.kipris_patent_service._download_pdf_url",
        lambda *args, **kwargs: tmp_path / "prior-art.pdf",
    )
    monkeypatch.setattr(
        "services.patent.kipris_patent_service.parse_single_patent_pdf",
        lambda *args, **kwargs: {"markdown_text": "1. A method comprising a processor and a memory."},
    )

    documents = _fetch_foreign_claims(client, [candidate])

    assert documents[0]["lookup_source"] == "google_patents_pdf"
    assert documents[0]["comparison_status"] == "claim_comparison_ready"
    assert documents[0]["representative_claims"][0]["text"].startswith("A method")


def test_parse_single_patent_pdf_requires_java_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr("services.patent.kipris_patent_service.shutil.which", lambda name: None)

    with pytest.raises(RuntimeError, match="java_runtime_missing"):
        parse_single_patent_pdf(tmp_path / "sample.pdf", output_dir=tmp_path / "out")


def test_parse_single_patent_pdf_rejects_broken_java_stub(monkeypatch, tmp_path):
    class Result:
        returncode = 1
        stdout = ""
        stderr = "Unable to locate a Java Runtime."

    monkeypatch.setattr("services.patent.kipris_patent_service.shutil.which", lambda name: "/usr/bin/java")
    monkeypatch.setattr("services.patent.kipris_patent_service.subprocess.run", lambda *args, **kwargs: Result())

    with pytest.raises(RuntimeError, match="java_runtime_unavailable"):
        parse_single_patent_pdf(tmp_path / "sample.pdf", output_dir=tmp_path / "out")


def test_fetch_foreign_claims_uses_google_patents_html_when_pdf_has_no_claims(monkeypatch):
    class Response:
        text = """
            <html>
              <meta name="DC.title" content="Prior art title">
              <meta name="DC.description" content="Prior art abstract">
              <div class="claim-text">1. A method comprising a processor and a memory.</div>
            </html>
        """

        def raise_for_status(self):
            return None

    class Session:
        def get(self, *args, **kwargs):
            return Response()

    client = ForeignClient()
    client.session = Session()
    client.timeout = 20
    candidate = foreign_reference_candidate_from_text("US 2010241261 A1")
    monkeypatch.setattr(
        "services.patent.kipris_patent_service._fetch_foreign_claims_from_kipris",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "services.patent.kipris_patent_service._fetch_foreign_claims_from_bigquery",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "services.patent.kipris_patent_service.google_patents_pdf_url",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "services.patent.kipris_patent_service._kipris_client",
        lambda: client,
    )

    documents = _fetch_foreign_claims(client, [candidate])

    assert documents[0]["lookup_source"] == "google_patents_html"
    assert documents[0]["title"] == "Prior art title"
    assert documents[0]["abstract"] == "Prior art abstract"
    assert documents[0]["representative_claims"][0]["text"].startswith("A method")


def test_fetch_foreign_claims_marks_html_abstract_without_claims_as_auxiliary(monkeypatch):
    class Response:
        text = """
            <html>
              <meta name="DC.title" content="Prior art title">
              <meta name="DC.description" content="Prior art abstract only">
            </html>
        """

        def raise_for_status(self):
            return None

    class Session:
        def get(self, *args, **kwargs):
            return Response()

    client = ForeignClient()
    client.session = Session()
    client.timeout = 20
    candidate = foreign_reference_candidate_from_text("JP 2000029513 A")
    monkeypatch.setattr(
        "services.patent.kipris_patent_service._fetch_foreign_claims_from_kipris",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "services.patent.kipris_patent_service._fetch_foreign_claims_from_bigquery",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "services.patent.kipris_patent_service.google_patents_pdf_url",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "services.patent.kipris_patent_service._kipris_client",
        lambda: client,
    )

    documents = _fetch_foreign_claims(client, [candidate])

    assert documents[0]["comparison_status"] == "abstract_only"
    collection = resolve_foreign_prior_art_evidence(["JP 2000029513 A"])["prior_art_collection"]
    assert collection["comparison_ready_count"] == 0
    assert collection["abstract_only_count"] == 1


def test_resolve_foreign_prior_art_evidence_marks_identifier_only_documents(monkeypatch):
    monkeypatch.setattr(
        "services.patent.kipris_patent_service._kipris_client",
        lambda: ForeignClient(),
    )
    monkeypatch.setattr(
        "services.patent.kipris_patent_service._fetch_foreign_claims",
        lambda *args, **kwargs: [],
    )

    result = resolve_foreign_prior_art_evidence(["US 2010241261 A1"])

    assert result["foreign_citation_documents"] == []
    assert result["foreign_identifier_only_documents"][0]["display_number"] == "US 2010241261 A1"
    assert result["prior_art_collection"] == {
        "candidate_count": 1,
        "comparison_ready_count": 0,
        "claim_comparison_ready_count": 0,
        "abstract_only_count": 0,
        "fulltext_claims_unparsed_count": 0,
        "identifier_only_count": 1,
        "comparison_status": "unknown",
    }


def test_fetch_foreign_patent_rights_data_uses_overseas_claims_for_us(monkeypatch):
    client = ForeignClient()
    monkeypatch.setattr("services.patent.kipris_patent_service._kipris_client", lambda: client)

    result = fetch_foreign_patent_rights_data(
        {
            "country": "US",
            "application_number": "18/020,829",
            "registration_number": "12,417,849",
            "title_final": "US title",
            "status": "등록",
        },
        collect_pdf=False,
    )

    assert result["metadata"]["country"] == "US"
    assert result["metadata"]["ipc"] == ["G16H 50/20", "G06F 16/2457"]
    assert result["sections"]["abstract"] == "An association identification method using document data."
    assert result["claims"][0]["source"] == "kipris_foreign_bibliographic_claims"
    assert result["foreign_claim_literature_number"] == "000012417849B2"
    assert result["foreign_bibliography_literature_number"] == "000012417849B2"
    assert ("000012417849B2", "US") in client.bibliography_calls
    assert ("000012417849B2", "US") in client.claim_calls


def test_fetch_foreign_patent_rights_data_marks_unsupported_claim_api_without_kr_metadata(monkeypatch):
    monkeypatch.setattr("services.patent.kipris_patent_service._kipris_client", lambda: ForeignClient())

    result = fetch_foreign_patent_rights_data(
        {
            "country": "TW",
            "application_number": "106132082",
            "registration_number": "I669767",
            "title_final": "TW title",
            "status": "등록",
        },
        collect_pdf=False,
    )

    assert result["source_type"] == "kipris_foreign_bibliographic_info"
    assert result["metadata"]["country"] == "TW"
    assert result["claims"] == []
    assert result["warnings"] == ["kipris_foreign_claims_not_supported:TW", "kipris_foreign_claims_not_found"]


def test_fetch_foreign_patent_rights_data_requires_manual_upload_when_all_pdf_sources_fail(monkeypatch):
    monkeypatch.setattr("services.patent.kipris_patent_service._kipris_client", lambda: ForeignClient())
    monkeypatch.setattr(
        "services.patent.kipris_patent_service.google_patents_pdf_url",
        lambda *args, **kwargs: None,
    )

    result = fetch_foreign_patent_rights_data(
        {
            "country": "CN",
            "application_number": "201880038342.9",
            "registration_number": "CN 110770661 B",
            "title_final": "CN title",
            "status": "등록",
        },
        collect_pdf=True,
    )

    assert result["pdf_collection"] == {
        "status": "manual_upload_required",
        "source": None,
        "manual_upload_required": True,
        "missing_reason": "kipris_and_google_patents_pdf_not_found",
    }
    assert result["warnings"][-1].startswith("foreign_pdf_manual_upload_required:")


def test_should_run_ocr_fallback_for_image_only_markdown():
    markdown = "![image 1](<page1.png>)\n\n![image 2](<page2.png>)"

    assert should_run_ocr_fallback(markdown) is True
    assert has_meaningful_pdf_text(markdown) is False


def test_parse_single_patent_pdf_uses_ocr_when_markdown_has_no_text(monkeypatch, tmp_path):
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 test")
    output_dir = tmp_path / "out"

    class FakeOpenDataLoaderPdf:
        @staticmethod
        def convert(*, input_path, output_dir, format):
            md_path = Path(output_dir) / "sample.md"
            md_path.write_text("![image 1](<page1.png>)\n", encoding="utf-8")

    monkeypatch.setitem(__import__("sys").modules, "opendataloader_pdf", FakeOpenDataLoaderPdf)
    monkeypatch.setattr(
        "services.patent.kipris_patent_service.extract_pdf_text_left_then_right",
        lambda path: "",
    )
    monkeypatch.setattr(
        "services.patent.kipris_patent_service.extract_pdf_text_with_ocr",
        lambda path: "Abstract\nClaims\n1. A system comprising a processor.",
    )

    result = parse_single_patent_pdf(pdf_path, output_dir=output_dir, country="US")

    assert result["markdown_paths"]
    assert "Abstract" in result["markdown_text"]
    assert "1. A system comprising a processor." in result["markdown_text"]


def test_parse_single_patent_pdf_raises_when_ocr_fails_to_extract_text(monkeypatch, tmp_path):
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 test")
    output_dir = tmp_path / "out"

    class FakeOpenDataLoaderPdf:
        @staticmethod
        def convert(*, input_path, output_dir, format):
            md_path = Path(output_dir) / "sample.md"
            md_path.write_text("![image 1](<page1.png>)\n", encoding="utf-8")

    monkeypatch.setitem(__import__("sys").modules, "opendataloader_pdf", FakeOpenDataLoaderPdf)
    monkeypatch.setattr(
        "services.patent.kipris_patent_service.extract_pdf_text_left_then_right",
        lambda path: "",
    )
    monkeypatch.setattr(
        "services.patent.kipris_patent_service.extract_pdf_text_with_ocr",
        lambda path: "",
    )

    with pytest.raises(RuntimeError, match="foreign_pdf_text_extraction_failed_after_ocr"):
        parse_single_patent_pdf(pdf_path, output_dir=output_dir, country="US")


def test_find_cached_foreign_patent_pdf_uses_publication_id(tmp_path):
    cached = tmp_path / "US12032469B2.pdf"
    cached.write_bytes(b"%PDF-1.4 cached")

    result = find_cached_foreign_patent_pdf(
        {"country": "US", "registration_number": "12,032,469", "application_number": "17/420,237"},
        pdf_dir=tmp_path,
    )

    assert result == cached


def test_extract_foreign_claims_from_text_ignores_description_and_keeps_real_claims():
    text = """
Explainable AI Modeling and Simulation Method
Embodiments of the present disclosure provide a system and a method.

AI Workflow Model Designing
FIG. 2 is a view provided to explain a process of designing an AI workflow model.

What is claimed is:
1. An explainable artificial intelligence (AI) modeling and simulation method comprising the steps of: designing an AI workflow model.
2. The method of claim 1, wherein the AI workflow model is provided by visualizing a workflow.
9. An explainable artificial intelligence (AI) modeling and simulation system, comprising: a storage unit and a processor.

Description
This description explains embodiments in detail.
"""

    claims = extract_foreign_claims_from_text(text)

    assert [claim["claim_no"] for claim in claims] == [1, 2, 9]
    assert claims[0]["text"].startswith("An explainable artificial intelligence")
    assert claims[-1]["text"].startswith("An explainable artificial intelligence")
    assert all("FIG. 2" not in claim["text"] for claim in claims)


def test_should_exclude_pdf_page_text_for_drawing_sheet_noise():
    text = """
U.S. Patent Jul. 9, 2024 Sheet 8 of 16 US 12,032,469 B2

Workflow Modeling
Simulation
Registration/performance
FIG. 8
Real-time classification result
Real-time distribution/performance
"""

    assert should_exclude_pdf_page_text(text) is True


def test_should_exclude_pdf_page_text_keeps_body_text():
    text = """
The present disclosure relates to explainable artificial intelligence technology.
Embodiments of the present disclosure provide a system and method for modelling workflows.
The system conducts a simulation and compares performance across candidate models.
"""

    assert should_exclude_pdf_page_text(text) is False


def test_trim_foreign_front_matter_starts_at_body_heading():
    text = """
United States Patent
(57) ABSTRACT
Provided is a system and method.

TECHNICAL FIELD
The present disclosure relates to explainable artificial intelligence technology.

BACKGROUND ART
Conventional approaches have limitations.
"""

    trimmed = trim_foreign_front_matter(text)

    assert trimmed.startswith("TECHNICAL FIELD")
    assert "United States Patent" not in trimmed
    assert "ABSTRACT" not in trimmed


def test_google_patents_publication_id_normalizes_cn_registration_number():
    assert (
        google_patents_publication_id({"country": "CN", "registration_number": "CN 110770661 B"})
        == "CN110770661B"
    )


def test_google_patents_publication_id_restores_us_publication_serial_padding():
    assert (
        google_patents_publication_id({"country": "US", "registration_number": "US 2010241261 A1"})
        == "US20100241261A1"
    )


def test_foreign_fulltext_parse_rejects_image_only_markdown():
    assert not foreign_fulltext_parse_is_usable(
        "\n".join(f"![image {index}](<images/image{index}.png>)" for index in range(1, 12))
    )
    assert foreign_fulltext_parse_is_usable(
        "What is claimed is:\n"
        "1. A method comprising calculating an equipment reliability index and controlling measurement."
    )


def test_extract_foreign_claims_accepts_chinese_ocr_numbering():
    claims = extract_foreign_claims_from_text(
        """
        200710112054.7 权利要求书
        1、一种导航装置，包括输入单元、地图数据存储器、图像存储器和控制单元。
        2、如权利要求 1 所述的导航装置，其中，控制单元显示导航信息。
        ## 페이지 3
        ![페이지 3](<images/page3.png>)
        ### OCR 텍스트
        ```text
        200710112054. 7                    权 AR OR TB 第273丰
        3、如权利要求 | 所述的导航装置，其中，控制单元切换背景图像。
        ```
        ## 페이지 4
        说明书
        本发明涉及导航装置。
        """
    )

    assert [claim["claim_no"] for claim in claims] == [1, 2, 3]
    assert claims[0]["is_independent"] is True
    assert claims[1]["dependency"] == 1
    assert claims[2]["dependency"] == 1
    assert claims[2]["is_independent"] is False
    assert "페이지" not in claims[1]["text"]
    assert "OCR 텍스트" not in claims[1]["text"]
    assert "image" not in claims[1]["text"]
    assert "第273丰" not in claims[2]["text"]
    assert "说明书" not in claims[2]["text"]


def test_extract_foreign_claims_accepts_taiwan_bracketed_claim_numbers():
    claims = extract_foreign_claims_from_text(
        """
        【發明申請專利範圍】
        【第1項】 一種用於提供虛擬產品的方法，包括取得製造數據。
        【第2項】 如申請專利範圍第1項所述之方法，其中即時提供虛擬產品。
        """
    )

    assert [claim["claim_no"] for claim in claims] == [1, 2]
    assert claims[0]["is_independent"] is True
    assert claims[1]["dependency"] == 1


def test_foreign_pdf_ocr_fallback_keeps_usable_text(monkeypatch, tmp_path):
    markdown_path = tmp_path / "CN110770661B.md"
    markdown_text = "测量控制方法和系统" * 40
    markdown_path.write_text(markdown_text, encoding="utf-8")
    monkeypatch.setattr(
        "services.patent.kipris_patent_service.shutil.which",
        lambda name: (_ for _ in ()).throw(AssertionError("OCR must not be called")),
    )

    result = apply_foreign_pdf_ocr_fallback(
        {
            "markdown_paths": [str(markdown_path)],
            "markdown_text": markdown_text,
        },
        country="CN",
    )

    assert result["markdown_text"] == markdown_text
    assert "ocr_applied" not in result


def test_foreign_pdf_ocr_fallback_rewrites_image_only_cn_markdown(monkeypatch, tmp_path):
    image_dir = tmp_path / "CN101275848_images"
    image_dir.mkdir()
    for index in (1, 2):
        (image_dir / f"imageFile{index}.png").write_bytes(b"fake-png")
    markdown_path = tmp_path / "CN101275848.md"
    markdown_path.write_text(
        "\n\n".join(
            [
                "![image 1](<CN101275848_images/imageFile1.png>)",
                "![image 2](<CN101275848_images/imageFile2.png>)",
            ]
        ),
        encoding="utf-8",
    )
    calls = []
    monkeypatch.setattr(
        "services.patent.kipris_patent_service.shutil.which",
        lambda name: "/opt/homebrew/bin/tesseract",
    )

    def fake_run(command, **kwargs):
        calls.append(command)

        class Result:
            stdout = (
                "1、一种导航装置，包括输入单元、地图数据存储器和控制单元。\n"
                + "本发明提供具有相框功能的导航装置及其操作方法。" * 20
            )

        return Result()

    monkeypatch.setattr(
        "services.patent.kipris_patent_service.subprocess.run",
        fake_run,
    )

    result = apply_foreign_pdf_ocr_fallback(
        {
            "markdown_paths": [str(markdown_path)],
            "markdown_text": markdown_path.read_text(encoding="utf-8"),
        },
        country="CN",
    )

    assert result["ocr_applied"] is True
    assert result["ocr_language"] == "chi_sim+eng"
    assert len(calls) == 2
    assert calls[0][-4:] == ["-l", "chi_sim+eng", "--psm", "6"]
    assert "## 페이지 1" in result["markdown_text"]
    assert "### OCR 텍스트" in result["markdown_text"]
    assert "1、一种导航装置" in markdown_path.read_text(encoding="utf-8")


def test_foreign_pdf_ocr_fallback_uses_japanese_language_pack(monkeypatch, tmp_path):
    image_dir = tmp_path / "JP123_images"
    image_dir.mkdir()
    (image_dir / "imageFile1.png").write_bytes(b"fake-png")
    markdown_path = tmp_path / "JP123.md"
    markdown_path.write_text(
        "![image 1](<JP123_images/imageFile1.png>)",
        encoding="utf-8",
    )
    commands = []
    monkeypatch.setattr(
        "services.patent.kipris_patent_service.shutil.which",
        lambda name: "/opt/homebrew/bin/tesseract",
    )

    def fake_run(command, **kwargs):
        commands.append(command)

        class Result:
            stdout = "請求項１ 装置の計測データの微細変動検知方法及びシステム。" * 20

        return Result()

    monkeypatch.setattr(
        "services.patent.kipris_patent_service.subprocess.run",
        fake_run,
    )

    result = apply_foreign_pdf_ocr_fallback(
        {
            "markdown_paths": [str(markdown_path)],
            "markdown_text": markdown_path.read_text(encoding="utf-8"),
        },
        country="JP",
    )

    assert result["ocr_applied"] is True
    assert result["ocr_language"] == "jpn+eng"
    assert commands[0][-4:] == ["-l", "jpn+eng", "--psm", "6"]


def test_foreign_pdf_ocr_fallback_uses_traditional_chinese_for_tw(monkeypatch, tmp_path):
    image_dir = tmp_path / "TWI123_images"
    image_dir.mkdir()
    (image_dir / "imageFile1.png").write_bytes(b"fake-png")
    markdown_path = tmp_path / "TWI123.md"
    markdown_path.write_text(
        "![image 1](<TWI123_images/imageFile1.png>)",
        encoding="utf-8",
    )
    commands = []
    monkeypatch.setattr(
        "services.patent.kipris_patent_service.shutil.which",
        lambda name: "/opt/homebrew/bin/tesseract",
    )

    def fake_run(command, **kwargs):
        commands.append(command)

        class Result:
            stdout = "申請專利範圍\n1. 一種用於提供虛擬產品的方法，包括取得製造數據。" * 20

        return Result()

    monkeypatch.setattr(
        "services.patent.kipris_patent_service.subprocess.run",
        fake_run,
    )

    result = apply_foreign_pdf_ocr_fallback(
        {
            "markdown_paths": [str(markdown_path)],
            "markdown_text": markdown_path.read_text(encoding="utf-8"),
        },
        country="TW",
    )

    assert result["ocr_applied"] is True
    assert result["ocr_language"] == "chi_tra+eng"
    assert commands[0][-4:] == ["-l", "chi_tra+eng", "--psm", "6"]


def test_foreign_pdf_parse_falls_back_to_google_patents_pdf(monkeypatch, tmp_path):
    class Client:
        session = object()
        timeout = 20

    parse_calls = []
    monkeypatch.setattr(
        "services.patent.kipris_patent_service.select_foreign_fulltext_pdf_with_fallback",
        lambda *args, **kwargs: {
            "literature_number": "000011782432B2",
            "selected_type": "FOREIGN_REGISTRATION_FULLTEXT",
            "doc_name": "kipris.pdf",
            "path": "https://example.com/kipris.pdf",
        },
    )
    monkeypatch.setattr(
        "services.patent.kipris_patent_service.google_patents_fulltext_selection",
        lambda *args, **kwargs: {
            "literature_number": "US11782432B2",
            "selected_type": "GOOGLE_PATENTS_FULLTEXT",
            "doc_name": "US11782432B2.pdf",
            "path": "https://example.com/google.pdf",
        },
    )
    monkeypatch.setattr(
        "services.patent.kipris_patent_service._download_pdf_url",
        lambda url, **kwargs: tmp_path / kwargs["filename"],
    )

    def fake_parse(pdf_path, *, output_dir, country=None):
        parse_calls.append(str(pdf_path))
        if len(parse_calls) == 1:
            return {
                "markdown_paths": [str(output_dir / "kipris.md")],
                "markdown_text": "![image 1](<images/image1.png>)",
            }
        return {
            "markdown_paths": [str(output_dir / "google.md")],
            "markdown_text": (
                "## ABSTRACT\n\nA dynamic lot measurement control system.\n\n"
                "## CLAIMS\n\n1. A method comprising calculating an equipment reliability index."
            ),
        }

    monkeypatch.setattr("services.patent.kipris_patent_service.parse_single_patent_pdf", fake_parse)

    result = download_and_parse_foreign_patent_pdf(
        Client(),
        {"country": "US", "registration_number": "11,782,432"},
        candidates=[],
        output_dir=tmp_path / "parsed",
    )

    assert result["selected_type"] == "GOOGLE_PATENTS_FULLTEXT"
    assert result["fallback_reason"] == "kipris_pdf_parse_unusable"
    assert "equipment reliability index" in result["markdown_text"]


def test_foreign_patent_metadata_uses_final_us_title_as_english_title():
    metadata = foreign_patent_metadata_from_db(
        {
            "country": "US",
            "title_final": "Correct English Patent Title",
            "title_draft": "Unrelated draft title",
        }
    )

    assert metadata["title"] == "Correct English Patent Title"
    assert metadata["title_eng"] == "Correct English Patent Title"


def test_google_patents_html_to_markdown_extracts_fulltext_sections():
    markdown = google_patents_html_to_markdown(
        """
        <meta name="DC.title" content="Dynamic measurement patent">
        <meta name="DC.description" content="Controls measurement using equipment reliability.">
        <section itemprop="description">
          <div>A controller calculates a lot risk score and determines whether to measure the lot.</div>
        </section>
        <div class="claim-text">1. A method comprising calculating an equipment reliability index.</div>
        """
    )

    assert "## ABSTRACT" in markdown
    assert "## DETAILED DESCRIPTION" in markdown
    assert "## CLAIMS" in markdown
    assert "equipment reliability index" in markdown


def test_google_patents_html_preserves_claim_numbers_and_taiwan_dependencies():
    markdown = google_patents_html_to_markdown(
        """
        <div num="1" class="claim">
          <div class="claim-text">一種用於提供虛擬半導體產品的方法，包括取得製造數據。</div>
        </div>
        <div num="2" class="claim">
          <div class="claim-text">如申請專利範圍第1項所述之方法，其中即時提供虛擬產品。</div>
        </div>
        """
    )

    claims = extract_foreign_claims_from_text(markdown)

    assert [claim["claim_no"] for claim in claims] == [1, 2]
    assert claims[0]["is_independent"] is True
    assert claims[1]["dependency"] == 1


def test_google_patents_html_fallback_keeps_downloaded_pdf_path(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "services.patent.kipris_patent_service.select_foreign_fulltext_pdf_with_fallback",
        lambda *args, **kwargs: {
            "literature_number": "TWI123456",
            "selected_type": "GOOGLE_PATENTS_FULLTEXT",
            "doc_name": "TWI123456.pdf",
            "path": "https://example.com/TWI123456.pdf",
        },
    )
    monkeypatch.setattr(
        "services.patent.kipris_patent_service._download_and_parse_foreign_selection",
        lambda *args, **kwargs: {
            "pdf_path": "/tmp/TWI123456.pdf",
            "source_path": "https://example.com/TWI123456.pdf",
            "markdown_text": "![page](<page.png>)",
        },
    )
    monkeypatch.setattr(
        "services.patent.kipris_patent_service.download_google_patents_html_fulltext",
        lambda *args, **kwargs: {
            "selected_type": "GOOGLE_PATENTS_HTML_FULLTEXT",
            "source_path": "https://patents.google.com/patent/TWI123456/zh",
            "markdown_text": "## CLAIMS\n\n1. A method comprising enough usable claim text for parsing.",
        },
    )

    result = download_and_parse_foreign_patent_pdf(
        object(),
        {"country": "TW", "registration_number": "I123456"},
        candidates=[],
        output_dir=tmp_path,
    )

    assert result["selected_type"] == "GOOGLE_PATENTS_HTML_FULLTEXT"
    assert result["pdf_path"] == "/tmp/TWI123456.pdf"
    assert result["pdf_source_path"] == "https://example.com/TWI123456.pdf"


def test_google_patents_html_fulltext_prefers_language_with_parseable_claims(tmp_path):
    from services.patent.kipris_patent_service import download_google_patents_html_fulltext

    class Response:
        def __init__(self, text):
            self.text = text
            self.content = text.encode("utf-8")

        def raise_for_status(self):
            return None

    class Session:
        def get(self, url, **kwargs):
            del kwargs
            if url.endswith("/en"):
                return Response(
                    '<meta name="DC.title" content="English title">'
                    '<section itemprop="description"><div>' + ("Long description. " * 30) + "</div></section>"
                )
            return Response(
                '<meta name="DC.title" content="中文標題">'
                '<div class="claim" num="1"><div class="claim-text">'
                "一種用於提供虛擬產品的方法，包括取得製造數據。"
                "</div></div>"
            )

    class Client:
        session = Session()
        timeout = 1

    result = download_google_patents_html_fulltext(
        Client(),
        {"country": "TW", "registration_number": "I123456"},
        output_dir=tmp_path,
    )

    assert result["source_path"].endswith("/zh")
    assert len(extract_foreign_claims_from_text(result["markdown_text"])) == 1


def test_google_patents_html_response_decodes_utf8_content_instead_of_mojibake_text():
    chinese_html = (
        '<meta name="DC.title" content="具有相框功能的导航装置及其操作方法">'
        '<section itemprop="description"><div>本发明涉及导航装置。</div></section>'
    )

    class Response:
        content = chinese_html.encode("utf-8")
        text = content.decode("latin-1")

    decoded = decode_google_patents_html_response(Response())
    markdown = google_patents_html_to_markdown(decoded)

    assert "具有相框功能的导航装置及其操作方法" in markdown
    assert "本发明涉及导航装置" in markdown
    assert "å" not in markdown


def test_google_patents_html_to_markdown_keeps_full_description_section():
    markdown = google_patents_html_to_markdown(
        """
        <meta name="DC.title" content="Dynamic measurement patent">
        <section itemprop="description">
          <div>First paragraph describes the technical field.</div>
          <div>Second paragraph explains background problems.</div>
          <div>Third paragraph describes the detailed embodiment.</div>
        </section>
        <div class="claim-text">1. A method comprising calculating an equipment reliability index.</div>
        """
    )

    assert "First paragraph describes the technical field." in markdown
    assert "Second paragraph explains background problems." in markdown
    assert "Third paragraph describes the detailed embodiment." in markdown


def test_google_patents_figure_urls_extracts_thumbnail_images():
    urls = _google_patents_figure_urls(
        """
        <img itemprop="thumbnail" src="https://example.com/D00000.png">
        <img itemprop="thumbnail" src="https://example.com/D00001.png">
        <img src="https://example.com/unrelated.png">
        """
    )

    assert urls == [
        "https://example.com/D00000.png",
        "https://example.com/D00001.png",
    ]


def test_google_patents_backward_references_extracts_supported_patent_numbers():
    references = _google_patents_backward_references(
        """
        <tr itemprop="backwardReferencesOrig">
          <td><span itemprop="publicationNumber">US20090306803A1</span></td>
        </tr>
        <tr itemprop="backwardReferences">
          <td><span itemprop="publicationNumber">JP2010118562A</span></td>
        </tr>
        """
    )

    assert references == ["US 20090306803 A1", "JP 2010118562 A"]


def test_google_patents_forward_references_extracts_citing_documents():
    references = _google_patents_forward_references(
        """
        <tr itemprop="forwardReferencesFamily" itemscope repeat>
          <td>
            <span itemprop="publicationNumber">JP6816175B2</span>
            <span itemprop="examinerCited">*</span>
          </td>
          <td itemprop="priorityDate">2019-01-10</td>
          <td itemprop="publicationDate">2021-01-20</td>
          <td><span itemprop="assigneeOriginal">本田技研工業株式会社</span></td>
          <td itemprop="title">製品測定結果表示システム</td>
        </tr>
        <tr itemprop="forwardReferences">
          <td><span itemprop="publicationNumber">US12217409B2</span></td>
          <td itemprop="priorityDate">2019-04-23</td>
          <td itemprop="publicationDate">2025-02-04</td>
          <td><span itemprop="assigneeOriginal">Sony Group Corporation</span></td>
          <td itemprop="title">Information processing device</td>
        </tr>
        """
    )

    assert [item["display_number"] for item in references] == [
        "JP 6816175 B2",
        "US 12217409 B2",
    ]
    assert references[0]["examiner_cited"] is True
    assert references[0]["assignee"] == "本田技研工業株式会社"
    assert references[1]["publication_date"] == "2025-02-04"


def test_foreign_rights_data_uses_google_patents_forward_references(monkeypatch):
    class Response:
        content = b"""
        <tr itemprop="forwardReferencesFamily">
          <td><span itemprop="publicationNumber">JP6816175B2</span></td>
          <td itemprop="priorityDate">2019-01-10</td>
          <td itemprop="publicationDate">2021-01-20</td>
          <td><span itemprop="assigneeOriginal">Honda</span></td>
          <td itemprop="title">Measurement system</td>
        </tr>
        """

        def raise_for_status(self):
            return None

    class Session:
        def get(self, *args, **kwargs):
            return Response()

    client = ForeignClient()
    client.session = Session()
    monkeypatch.setattr("services.patent.kipris_patent_service._kipris_client", lambda: client)

    result = fetch_foreign_patent_rights_data(
        {
            "country": "TW",
            "application_number": "106132082",
            "registration_number": "I669767",
            "status": "등록",
        },
        collect_pdf=False,
    )

    assert result["citing_stats"]["available"] is True
    assert result["citing_stats"]["total_count"] == 1
    assert result["citing_documents"][0]["display_number"] == "JP 6816175 B2"
    assert result["foreign_api_collection"]["target_citing_references"]["source"] == (
        "google_patents_html_forward_references"
    )


def test_google_patents_pdf_url_reads_citation_pdf_meta():
    session = GooglePatentsSession(
        '<html><meta name="citation_pdf_url" content="https://patentimages.storage.googleapis.com/x/CN110770661B.pdf"></html>'
    )

    result = google_patents_pdf_url(
        {"country": "CN", "registration_number": "CN 110770661 B"},
        session=session,
    )

    assert result == "https://patentimages.storage.googleapis.com/x/CN110770661B.pdf"
    assert session.calls[0] == "https://patents.google.com/patent/CN110770661B/en"


def test_extract_foreign_claims_from_text_supports_chinese_numbered_claims():
    claims = extract_foreign_claims_from_text(
        "1.一种测量控制方法，包括：计算设备可靠性指数并确定测量时机。\n"
        "- 2.根据权利要求1所述的方法，其中，计算设备可靠性指数包括计算设备稳定性。"
    )

    assert len(claims) == 2
    assert claims[0]["claim_no"] == 1
    assert claims[0]["is_independent"] is True
    assert claims[1]["dependency"] == 1


def test_extract_foreign_claims_from_text_detects_japanese_dependency_variants():
    claims = extract_foreign_claims_from_text(
        """
CLAIMS
Claim 1: 基準データと比較データを用いて計測値の微細な変動を検知する方法。
Claim 2: 請求項１記載の方法であって、追加の検定を行う方法。
Claim 3: 請求項１又は２に記載の方法であって、結果を表示する方法。
Claim 4: 請求項１ないし３のいずれか１項に記載の方法であって、警告を出力する方法。
"""
    )

    assert claims[0]["is_independent"] is True
    assert [claim["dependency"] for claim in claims[1:]] == [1, 1, 1]


def test_extract_foreign_claims_removes_ocr_markdown_wrappers_for_japanese_patent():
    claims = extract_foreign_claims_from_text(
        """
CLAIMS
Claim 1: 基準データを用いて計測値の変動を検知する装置。
## 페이지 2
![페이지 2](<images/page2.png>)
### OCR 텍스트
```text
特許第1234567号 請求の範囲 第2頁
Claim 2: 請求項１に記載の装置であって、警告を出力する装置。
```
"""
    )

    assert [claim["claim_no"] for claim in claims] == [1, 2]
    assert claims[1]["dependency"] == 1
    assert all("페이지" not in claim["text"] for claim in claims)
    assert all("OCR 텍스트" not in claim["text"] for claim in claims)
    assert all("![" not in claim["text"] for claim in claims)


def test_extract_foreign_claims_from_text_ignores_numbered_description_before_claims():
    claims = extract_foreign_claims_from_text(
        "1. FIELD OF THE INVENTION numbered description that is not a claim.\n"
        "2. BACKGROUND OF THE INVENTION numbered description that is not a claim.\n"
        "What is claimed is:\n"
        "1. A method comprising calculating an equipment reliability index and controlling measurement.\n"
        "2. The method according to claim 1, further comprising calculating a lot risk score."
    )

    assert len(claims) == 2
    assert claims[0]["text"].startswith("A method comprising")
    assert "FIELD OF THE INVENTION" not in claims[0]["text"]
    assert claims[1]["dependency"] == 1


def test_extract_foreign_claims_from_text_supports_we_claim_marker():
    claims = extract_foreign_claims_from_text(
        "1. Numbered background paragraph that is not a claim.\n"
        "We claim:\n"
        "- 1. A system comprising a processor configured to calculate a risk score.\n"
        "- 2. The system of claim 1, further comprising a measurement controller."
    )

    assert [claim["claim_no"] for claim in claims] == [1, 2]
    assert claims[0]["text"].startswith("A system comprising")
    assert claims[1]["dependency"] == 1


def test_extract_foreign_claims_from_text_sorts_claims_and_handles_joined_dependency():
    claims = extract_foreign_claims_from_text(
        "We claim:\n"
        "12. A method ofclaim 10, further comprising an alarm analysis.\n"
        "1. A method comprising collecting process data.\n"
        "2. The method of claim 1, further comprising calculating risk."
    )

    assert [claim["claim_no"] for claim in claims] == [1, 2, 12]
    assert claims[2]["dependency"] == 10


def test_normalize_foreign_reference_documents_extracts_patent_documents():
    raw = {
        "response": {
            "body": {
                "items": {
                    "foreignPatentDocumentsInfo": [
                        {
                            "countryCode": "US",
                            "literatureNumber": "1234567",
                            "kindCode": "B2",
                            "inventionTitle": "Prior patent",
                            "publicationDate": "20200131",
                        }
                    ]
                }
            }
        }
    }

    result = normalize_foreign_reference_documents(
        raw,
        source="kipris_foreign_foreign_citation_documents",
        direction="cited_by_target",
    )

    assert result[0]["country_code"] == "US"
    assert result[0]["document_number"] == "1234567"
    assert result[0]["display_number"] == "US1234567 B2"
    assert result[0]["publication_date"] == "2020-01-31"


def test_fulltext_application_number_candidates_include_normalized_and_original():
    assert fulltext_application_number_candidates("18/020,829") == ["18020829", "18/020,829"]


def test_select_fulltext_pdf_tries_original_application_number_after_normalized_empty():
    client = FulltextClient()

    selected = _select_fulltext_pdf(
        client,
        fulltext_application_number_candidates("18/020,829"),
        prefer_announcement=True,
    )

    assert selected["application_number"] == "18/020,829"
    assert selected["path"] == "https://example.com/us.pdf"
    assert ("publication", "18020829") in client.calls
    assert ("publication", "18/020,829") in client.calls


def test_family_patents_normalizes_country_code_and_registration_number(monkeypatch):
    client = KiprisClient(service_key="test-key")
    monkeypatch.setattr(
        client,
        "family_info",
        lambda application_number: {
            "response": {
                "body": {
                    "items": {
                        "item": [
                            {"publicationCountryCode": "US", "publicationKindCode": "A1", "publicationNumber": "2024000001"},
                            {"publicationCountryCode": "CN", "publicationKindCode": "B", "publicationNumber": "1234567"},
                            {"publicationCountryCode": "JP", "publicationKindCode": "B2", "publicationNumber": "7654321"},
                        ]
                    }
                }
            }
        },
    )

    result = client.family_patents("1020050050026")

    assert [(item.country_code, item.registration_number) for item in result] == [
        ("CN", "1234567"),
        ("JP", "7654321"),
    ]


def test_normalize_kipris_citations_prefers_standardized_duplicate():
    raw = {
        "response": {
            "body": {
                "items": {
                    "citationInfoV3": [
                        {
                            "ApplicationNumber": "1020220150081",
                            "OriginalcitationLiteraturenumber": "CN113039310 A",
                            "StandardCitationLiteraturenumber": "113039310",
                            "StandardCitationLiteratureCountryCode": "CN",
                            "StandardCitationIdentificationCode": "A",
                            "StandardCitationLiteraturePublicationDate": "20210625",
                            "StandardStatusCode": "20001",
                            "StandardStatusCodeName": "표준화",
                            "CitationLiteratureTypeCode": "E0802",
                            "CitationLiteratureTypeCodeName": "선행기술조사보고서",
                        },
                        {
                            "ApplicationNumber": "1020220150081",
                            "OriginalcitationLiteraturenumber": "CN113039310 A",
                            "StandardCitationLiteraturenumber": " ",
                            "StandardCitationLiteratureCountryCode": "CN",
                            "StandardCitationIdentificationCode": " ",
                            "StandardCitationLiteraturePublicationDate": " ",
                            "StandardStatusCode": "99999",
                            "StandardStatusCodeName": "비표준화",
                            "CitationLiteratureTypeCode": "E0805",
                            "CitationLiteratureTypeCodeName": "선행기술조사문헌",
                        },
                        {
                            "ApplicationNumber": "1020220150081",
                            "OriginalcitationLiteraturenumber": "The Gerontologist, Vol.33, 1993. 10.",
                            "StandardCitationLiteraturenumber": "",
                            "StandardCitationLiteratureCountryCode": "",
                            "StandardCitationIdentificationCode": "",
                            "StandardCitationLiteraturePublicationDate": "",
                            "StandardStatusCode": "20002",
                            "StandardStatusCodeName": "비표준화",
                            "CitationLiteratureTypeCode": "E0805",
                            "CitationLiteratureTypeCodeName": "선행기술조사문헌",
                        },
                    ]
                }
            }
        }
    }

    result = normalize_kipris_citations(raw)

    assert [item["display_number"] for item in result] == [
        "CN113039310 A",
        "The Gerontologist, Vol.33, 1993. 10.",
    ]
    assert result[0]["is_standardized"] is True
    assert result[0]["publication_date"] == "2021-06-25"
    assert result[0]["citation_type_names"] == ["선행기술조사보고서", "선행기술조사문헌"]
    assert result[1]["is_standardized"] is False


def test_normalize_kipris_citing_documents_merges_duplicate_application_numbers():
    raw = {
        "response": {
            "body": {
                "items": {
                    "citingInfo": [
                        {
                            "StandardCitationApplicationNumber": "1020060089973",
                            "ApplicationNumber": "1020117007865",
                            "StandardStatusCode": "20001",
                            "StandardStatusCodeName": "표준화",
                            "CitationLiteratureTypeCode": "E0801",
                            "CitationLiteratureTypeCodeName": "발송문서",
                        },
                        {
                            "StandardCitationApplicationNumber": "1020060089973",
                            "ApplicationNumber": "1020117007865",
                            "StandardStatusCode": "20001",
                            "StandardStatusCodeName": "표준화",
                            "CitationLiteratureTypeCode": "E0805",
                            "CitationLiteratureTypeCodeName": "선행기술조사문헌",
                        },
                    ]
                }
            }
        }
    }

    result = normalize_kipris_citing_documents(raw)

    assert len(result) == 1
    assert result[0]["standard_citation_application_number"] == "1020060089973"
    assert result[0]["citing_application_number"] == "1020117007865"
    assert result[0]["is_standardized"] is True
    assert result[0]["citation_type_codes"] == ["E0801", "E0805"]
    assert result[0]["citation_type_names"] == ["발송문서", "선행기술조사문헌"]


def test_fetch_kipris_bibliography_adds_citation_documents(monkeypatch):
    class Client:
        def bibliography_detail(self, application_number):
            return {
                "response": {
                    "body": {
                        "item": {
                            "biblioSummaryInfoArray": {
                                "biblioSummaryInfo": {
                                    "applicationNumber": application_number,
                                    "registerStatus": "등록",
                                    "claimCount": "1",
                                }
                            },
                            "claimInfoArray": {"claimInfo": [{"claim": "1. 청구항 내용"}]},
                        }
                    }
                }
            }

        def family_patents(self, application_number):
            return []

        def citation_info_v3(self, application_number):
            return {
                "response": {
                    "body": {
                        "items": {
                            "citationInfoV3": {
                                "ApplicationNumber": application_number,
                                "OriginalcitationLiteraturenumber": "JP2017047511 A",
                                "StandardCitationLiteraturenumber": "29047511",
                                "StandardCitationLiteratureCountryCode": "JP",
                                "StandardCitationIdentificationCode": "A",
                                "StandardCitationLiteraturePublicationDate": "20170309",
                                "StandardStatusCode": "20001",
                                "StandardStatusCodeName": "표준화",
                                "CitationLiteratureTypeCode": "E0802",
                                "CitationLiteratureTypeCodeName": "선행기술조사보고서",
                            }
                        }
                    }
                }
            }

        def citing_info(self, standard_citation_application_number):
            return {
                "response": {
                    "body": {
                        "items": {
                            "citingInfo": {
                                "StandardCitationApplicationNumber": standard_citation_application_number,
                                "ApplicationNumber": "1020117007865",
                                "StandardStatusCode": "20001",
                                "StandardStatusCodeName": "표준화",
                                "CitationLiteratureTypeCode": "E0805",
                                "CitationLiteratureTypeCodeName": "선행기술조사문헌",
                            }
                        }
                    }
                }
            }

    monkeypatch.setattr("services.patent.kipris_patent_service._kipris_client", lambda: Client())
    monkeypatch.setattr(
        "services.patent.kipris_patent_service._fetch_foreign_claims_from_bigquery",
        lambda candidates, **kwargs: [],
    )

    result = fetch_kipris_bibliography("10-2022-0150081")

    assert result["citation_documents"][0]["display_number"] == "JP29047511 A"
    assert result["metadata"]["prior_art"] == ["JP29047511 A"]
    assert result["citation_stats"]["standardized_count"] == 1
    assert result["citing_documents"][0]["citing_application_number"] == "1020117007865"
    assert result["citing_stats"]["standardized_count"] == 1


def test_normalize_kipris_claims_detects_je_dependency_phrase():
    from services.patent.kipris_patent_service import _normalize_kipris_claims

    claims = _normalize_kipris_claims(
        [
            {"claim": "1. 독립항 내용"},
            {"claim": "2. 제1항에 있어서 종속항 내용"},
            {"claim": "3. 제1항 내지 제2항 중 어느 한 항에 따른 시스템"},
        ]
    )

    assert claims[0]["is_independent"] is True
    assert claims[1]["is_independent"] is False
    assert claims[1]["dependency"] == 1
    assert claims[2]["is_independent"] is False
    assert claims[2]["dependency"] == 1


def test_resolve_citation_evidence_enriches_kr_citation_documents_without_citing_details():
    class Client:
        def __init__(self):
            self.advanced_calls = []
            self.detail_calls = []

        def advanced_search(self, **params):
            self.advanced_calls.append(params)
            if params.get("openNumber") == "1020220029099":
                return {
                    "response": {
                        "body": {
                            "items": {
                                "item": {
                                    "applicationNumber": "1020200012345",
                                    "inventionTitle": "인용 공개 특허",
                                    "astrtCont": "인용 공개 초록",
                                    "registerStatus": "공개",
                                }
                            }
                        }
                    }
                }
            if params.get("registerNumber") == "1003093140000":
                return {
                    "response": {
                        "body": {
                            "items": {
                                "item": {
                                    "applicationNumber": "1019990001111",
                                    "inventionTitle": "인용 등록 특허",
                                    "astrtCont": "인용 등록 초록",
                                    "registerStatus": "등록",
                                }
                            }
                        }
                    }
                }
            return {"response": {"body": {"items": {}}}}

        def bibliography_detail(self, application_number):
            self.detail_calls.append(application_number)
            return {
                "response": {
                    "body": {
                        "item": {
                            "biblioSummaryInfoArray": {
                                "biblioSummaryInfo": {
                                    "applicationNumber": application_number,
                                    "inventionTitle": f"{application_number} 상세 제목",
                                    "registerStatus": "등록",
                                    "claimCount": "2",
                                }
                            },
                            "abstractInfoArray": {
                                "abstractInfo": {"astrtCont": f"{application_number} 상세 초록"}
                            },
                            "claimInfoArray": {
                                "claimInfo": [
                                    {"claim": "1. 독립항 내용"},
                                    {"claim": "2. 청구항 1에 있어서 종속항 내용"},
                                ]
                            },
                        }
                    }
                }
            }

    client = Client()
    citation_documents = [
        {
            "country_code": "KR",
            "standard_number": "1020220029099",
            "kind_code": "A",
            "display_number": "KR1020220029099 A",
            "is_standardized": True,
        },
        {
            "country_code": "KR",
            "standard_number": "1003093140000",
            "kind_code": "B1",
            "display_number": "KR100309314 B1",
            "is_standardized": True,
        },
        {
            "country_code": "JP",
            "standard_number": "29047511",
            "kind_code": "A",
            "display_number": "JP29047511 A",
            "is_standardized": True,
        },
    ]
    citing_documents = [
        {
            "citing_application_number": "1020117007865",
            "standard_status_name": "표준화",
            "is_standardized": True,
        }
    ]

    result = resolve_citation_evidence(
        client,
        citation_documents=citation_documents,
        citing_documents=citing_documents,
        foreign_claims_fetcher=lambda candidates, **kwargs: [],
    )

    assert client.advanced_calls == [
        {"registerNumber": "1003093140000", "patent": True, "utility": False, "pageNo": 1, "numOfRows": 1},
        {"openNumber": "1020220029099", "patent": True, "utility": False, "pageNo": 1, "numOfRows": 1},
    ]
    assert client.detail_calls == ["1019990001111", "1020200012345"]
    assert [item["application_number"] for item in result["kr_citation_documents"]] == [
        "1019990001111",
        "1020200012345",
    ]
    assert result["kr_citation_documents"][0]["representative_claims"][0]["text"] == "독립항 내용"
    assert "kr_citing_documents" not in result
    assert result["foreign_claim_lookup_candidates"] == [
        {
            "direction": "cited_by_target",
            "country_code": "JP",
            "document_number": "29047511",
            "kind_code": "A",
            "original_number": None,
            "display_number": "JP29047511 A",
            "lookup_source": "bigquery_claims",
        }
    ]


def test_resolve_citation_evidence_keeps_up_to_six_kr_independent_claims():
    class Client:
        def advanced_search(self, **params):
            return {
                "response": {
                    "body": {
                        "items": {
                            "item": {
                                "applicationNumber": "1020200012345",
                                "registerStatus": "등록",
                            }
                        }
                    }
                }
            }

        def bibliography_detail(self, application_number):
            return {
                "response": {
                    "body": {
                        "item": {
                            "biblioSummaryInfoArray": {
                                "biblioSummaryInfo": {
                                    "applicationNumber": application_number,
                                    "inventionTitle": "독립항 다수 특허",
                                    "registerStatus": "등록",
                                    "claimCount": "7",
                                }
                            },
                            "claimInfoArray": {
                                "claimInfo": [
                                    {"claim": f"{index}. 독립항 {index} 내용"}
                                    for index in range(1, 8)
                                ]
                            },
                        }
                    }
                }
            }

    result = resolve_citation_evidence(
        Client(),
        citation_documents=[
            {
                "country_code": "KR",
                "standard_number": "1020220029099",
                "kind_code": "A",
                "is_standardized": True,
            }
        ],
        citing_documents=[],
        foreign_claims_fetcher=lambda candidates, **kwargs: [],
    )

    claims = result["kr_citation_documents"][0]["representative_claims"]
    assert [claim["claim_no"] for claim in claims] == [1, 2, 3, 4, 5, 6]


def test_resolve_citation_evidence_does_not_enrich_citing_documents():
    class Client:
        def __init__(self):
            self.detail_calls = []

        def bibliography_detail(self, application_number):
            self.detail_calls.append(application_number)
            return {
                "response": {
                    "body": {
                        "item": {
                            "biblioSummaryInfoArray": {
                                "biblioSummaryInfo": {
                                    "applicationNumber": application_number,
                                    "inventionTitle": f"{application_number} 제목",
                                    "registerStatus": "등록",
                                    "claimCount": "1",
                                }
                            },
                            "claimInfoArray": {"claimInfo": [{"claim": "1. 대표 청구항"}]},
                        }
                    }
                }
            }

    client = Client()
    citing_documents = [
        {
            "citing_application_number": "1020210152256",
            "citation_type_codes": ["E0806"],
            "citation_type_names": ["출원서인용문헌이력정보"],
            "is_standardized": True,
        },
        {
            "citing_application_number": "1020210140457",
            "citation_type_codes": ["E0801"],
            "citation_type_names": ["발송문서"],
            "is_standardized": True,
        },
        {
            "citing_application_number": "1020210146956",
            "citation_type_codes": ["E0805"],
            "citation_type_names": ["선행기술조사문헌"],
            "is_standardized": True,
        },
        {
            "citing_application_number": "1020210152036",
            "citation_type_codes": ["E0802"],
            "citation_type_names": ["선행기술조사보고서"],
            "is_standardized": True,
        },
    ]

    result = resolve_citation_evidence(
        client,
        citation_documents=[],
        citing_documents=citing_documents,
        max_kr_citing=3,
    )

    assert client.detail_calls == []
    assert "kr_citing_documents" not in result


def test_resolve_citation_evidence_attaches_bigquery_foreign_claims():
    citation_documents = [
        {
            "country_code": "CN",
            "standard_number": "113039310",
            "kind_code": "A",
            "original_number": "CN113039310 A",
            "display_number": "CN113039310 A",
            "is_standardized": True,
        }
    ]

    def foreign_claims_fetcher(candidates, **kwargs):
        assert kwargs["max_candidates"] == 3
        return [
            {
                "direction": "cited_by_target",
                "country_code": "CN",
                "publication_number": "CN-113039310-A",
                "title": "CN 특허",
                "representative_claims": [{"claim_no": 1, "text": "CN claim"}],
                "lookup_status": "resolved",
                "lookup_source": "bigquery_patents_publications",
            }
        ]

    result = resolve_citation_evidence(
        object(),
        citation_documents=citation_documents,
        citing_documents=[],
        foreign_claims_fetcher=foreign_claims_fetcher,
    )

    assert result["foreign_claim_lookup_candidates"][0]["original_number"] == "CN113039310 A"
    assert result["foreign_citation_documents"][0]["publication_number"] == "CN-113039310-A"
    assert result["foreign_citation_documents"][0]["representative_claims"][0]["text"] == "CN claim"


def test_fetch_foreign_claims_from_kipris_uses_literature_number_candidates():
    class Client:
        def __init__(self):
            self.calls = []

        def overseas_demand_paragraph(self, literature_number, country_code):
            self.calls.append((literature_number, country_code))
            if literature_number == "000004002589B2":
                return {
                    "response": {
                        "body": {
                            "items": {
                                "demandParagraphInfo": {
                                    "claimText": "搬送コンベヤにより搬送中のガス容器を洗浄する装置。"
                                }
                            }
                        }
                    }
                }
            return {"response": {"body": {"items": {}}}}

    candidate = {
        "direction": "cited_by_target",
        "country_code": "JP",
        "document_number": "04002589",
        "kind_code": "B2",
        "original_number": "JP4002589 B2",
        "display_number": "JP04002589 B2",
    }

    result = _fetch_foreign_claims_from_kipris(Client(), [candidate])

    assert result[0]["literature_number"] == "000004002589B2"
    assert result[0]["lookup_source"] == "kipris_foreign_bibliographic_claims"
    assert result[0]["representative_claims"][0]["text"].startswith("搬送コンベヤ")


def test_fetch_foreign_claims_from_kipris_keeps_up_to_five_claims():
    class Client:
        def overseas_demand_paragraph(self, literature_number, country_code):
            return {
                "response": {
                    "body": {
                        "items": {
                            "demandParagraphInfo": [
                                {"claimText": f"foreign claim {index}"}
                                for index in range(1, 7)
                            ]
                        }
                    }
                }
            }

    candidate = {
        "direction": "cited_by_target",
        "country_code": "JP",
        "document_number": "04002589",
        "kind_code": "B2",
        "display_number": "JP04002589 B2",
    }

    result = _fetch_foreign_claims_from_kipris(Client(), [candidate])

    assert [claim["text"] for claim in result[0]["representative_claims"]] == [
        "foreign claim 1",
        "foreign claim 2",
        "foreign claim 3",
        "foreign claim 4",
        "foreign claim 5",
    ]


def test_foreign_literature_number_candidates_try_twelve_digit_kind_first():
    candidate = {
        "country_code": "JP",
        "document_number": "7401073",
        "kind_code": "B2",
        "original_number": "JP7401073 B2",
        "display_number": "JP7401073 B2",
    }

    assert _foreign_literature_number_candidates(candidate)[:2] == [
        "000007401073B2",
        "7401073B2",
    ]


def test_foreign_literature_number_candidates_adds_a0_for_open_publications():
    candidate = {
        "country_code": "CN",
        "document_number": "113039310",
        "kind_code": "A",
        "original_number": "CN113039310 A",
        "display_number": "CN113039310 A",
    }

    candidates = _foreign_literature_number_candidates(candidate)

    assert candidates[:2] == [
        "000113039310A0",
        "113039310A0",
    ]
    assert "000113039310A" in candidates
    assert "113039310A" in candidates


def test_foreign_literature_number_candidates_converts_jp_era_open_number():
    candidate = {
        "country_code": "JP",
        "document_number": "29047511",
        "kind_code": "A",
        "original_number": "JP29047511 A",
        "display_number": "JP29047511 A",
        "publication_date": "2017-03-09",
    }

    candidates = _foreign_literature_number_candidates(candidate)

    assert candidates[:2] == [
        "002017047511A0",
        "2017047511A0",
    ]
    assert "000029047511A0" in candidates


import pytest

from services.patent.kipris_patent_service import (
    _extract_claim_dependency,
    _strip_register_suffix,
)


@pytest.mark.parametrize(
    "text, expected",
    [
        # 종속 인용 — 인용 종결어미를 동반하므로 종속항으로 인식한다.
        ("제1항에 있어서, 상기 장치는", 1),
        ("제2항에 따른 방법", 2),
        ("제3항에 기재된 시스템", 3),
        ("청구항 1에 있어서", 1),
        ("제1항 또는 제2항에 있어서", 1),
        ("제1항 내지 제3항 중 어느 한 항에 있어서", 1),
        ("제10항 내지 제12항 중 어느 하나의 항에 있어서", 10),
        ("제1항의 방법을 수행하는 장치", 1),
        ("제5항에 의한 화합물", 5),
        ("제1항에서, 상기", 1),
        # 독립항 — 단순 구성요소 나열은 인용 종결어미가 없으므로 종속으로 오판하지 않는다.
        ("제1 또는 제2 위치에 배치되는 부재를 포함하는 장치", None),
        ("상기 제1 모드 또는 제2 모드에서 동작하는 장치", None),
        ("제1 단계 및 제2 단계를 포함하는 방법", None),
        ("복수의 항목을 포함하고", None),
    ],
)
def test_extract_claim_dependency_requires_citation_terminator(text, expected):
    assert _extract_claim_dependency(text) == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        # 13자리 압축 등록번호 → 항차 4자리 제거.
        ("1003093140000", "100309314"),
        ("1020000000000", "102000000"),
        ("  1003093140000  ", "100309314"),
        # 하이픈 표기 → 항차 4자리 제거.
        ("10-0309314-0001", "10-0309314"),
        ("10-0309314-0000", "10-0309314"),
        # 해외 번호(13자리 아님) → 원형 보존.
        ("CN1234567", "CN1234567"),
        ("US2024000001", "US2024000001"),
        # 빈 값 → None.
        (None, None),
        ("", None),
    ],
)
def test_strip_register_suffix_handles_compact_and_hyphenated_numbers(value, expected):
    assert _strip_register_suffix(value) == expected
