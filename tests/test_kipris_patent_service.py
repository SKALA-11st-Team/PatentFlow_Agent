from open_api.kipris_client import KiprisClient
from services.patent.kipris_patent_service import (
    fetch_kipris_bibliography,
    normalize_kipris_citations,
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

    def raise_for_status(self):
        return None


class Session:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        return Response()


def test_citation_info_v3_uses_access_key_auth_param():
    client = KiprisClient(service_key="test-key")
    client.session = Session()

    client.citation_info_v3("1020220150081")

    call = client.session.calls[0]
    assert call["url"].endswith("/openapi/rest/CitationService/citationInfoV3")
    assert call["params"]["accessKey"] == "test-key"
    assert "ServiceKey" not in call["params"]
    assert call["params"]["applicationNumber"] == "1020220150081"


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

    monkeypatch.setattr("services.patent.kipris_patent_service._kipris_client", lambda: Client())

    result = fetch_kipris_bibliography("10-2022-0150081")

    assert result["citation_documents"][0]["display_number"] == "JP29047511 A"
    assert result["metadata"]["prior_art"] == ["JP29047511 A"]
    assert result["citation_stats"]["standardized_count"] == 1
