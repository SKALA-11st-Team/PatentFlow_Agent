from pathlib import Path

from open_api.kipris_client import KiprisClient
from services.patent.kipris_patent_service import (
    download_and_parse_foreign_patent_pdf,
    fetch_foreign_patent_rights_data,
    fetch_kipris_bibliography,
    extract_foreign_claims_from_text,
    find_cached_foreign_patent_pdf,
    foreign_target_literature_candidates,
    google_patents_pdf_url,
    google_patents_publication_id,
    has_meaningful_pdf_text,
    normalize_kipris_citations,
    normalize_kipris_citing_documents,
    parse_single_patent_pdf,
    normalize_foreign_reference_documents,
    resolve_citation_evidence,
    should_run_ocr_fallback,
    classify_foreign_pdf_failure,
    _fetch_foreign_claims_from_kipris,
    _foreign_literature_number_candidates,
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
        self.session = Session()
        self.timeout = 30.0

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
    assert result["claims"][0]["source"] == "kipris_foreign_bibliographic_claims"
    assert result["foreign_claim_literature_number"] == "000012417849B2"
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

    assert result["source_type"] == "kipris_foreign_patent"
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


def test_classify_foreign_pdf_failure_preserves_non_lookup_errors():
    assert classify_foreign_pdf_failure(RuntimeError("ocr_tools_not_available:tesseract,pdftoppm")) == (
        "ocr_tools_not_available:tesseract,pdftoppm"
    )


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
        "services.patent.kipris_patent_service.extract_pdf_text_with_ocr",
        lambda path: "Abstract\nClaims\n1. A system comprising a processor.",
    )

    result = parse_single_patent_pdf(pdf_path, output_dir=output_dir)

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
        "services.patent.kipris_patent_service.extract_pdf_text_with_ocr",
        lambda path: "",
    )

    import pytest

    with pytest.raises(RuntimeError, match="foreign_pdf_text_extraction_failed_after_ocr"):
        parse_single_patent_pdf(pdf_path, output_dir=output_dir)


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


def test_download_and_parse_foreign_patent_pdf_prefers_cached_local_pdf(monkeypatch, tmp_path):
    cached = tmp_path / "US12032469B2.pdf"
    cached.write_bytes(b"%PDF-1.4 cached")
    parse_output_dir = tmp_path / "parsed"
    captured = {}

    def fake_parse(pdf_path, *, output_dir, output_format="markdown-with-images"):
        captured["pdf_path"] = Path(pdf_path)
        captured["output_dir"] = Path(output_dir)
        return {"markdown_paths": [str(output_dir / "cached.md")], "markdown_text": "Abstract\nClaims\n1. Cached claim."}

    def fail_select(*args, **kwargs):
        raise AssertionError("remote PDF lookup should not run when cached PDF exists")

    monkeypatch.setattr("services.patent.kipris_patent_service.parse_single_patent_pdf", fake_parse)
    monkeypatch.setattr("services.patent.kipris_patent_service.select_foreign_fulltext_pdf_with_fallback", fail_select)
    monkeypatch.setattr("services.patent.kipris_patent_service.settings.patent_pdf_dir", tmp_path)

    result = download_and_parse_foreign_patent_pdf(
        ForeignClient(),
        {"country": "US", "registration_number": "12,032,469", "application_number": "17/420,237"},
        candidates=[],
        output_dir=parse_output_dir,
    )

    assert captured["pdf_path"] == cached
    assert result["selected_type"] == "CACHED_LOCAL_PDF"
    assert result["pdf_path"] == str(cached)
    assert result["source_path"] == str(cached)


def test_google_patents_publication_id_normalizes_cn_registration_number():
    assert (
        google_patents_publication_id({"country": "CN", "registration_number": "CN 110770661 B"})
        == "CN110770661B"
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
