from open_api.kipris_client import KiprisClient
from services.patent.kipris_patent_service import (
    fetch_kipris_bibliography,
    normalize_kipris_citations,
    normalize_kipris_citing_documents,
    resolve_citation_evidence,
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


def test_citing_info_uses_access_key_auth_param():
    client = KiprisClient(service_key="test-key")
    client.session = Session()

    client.citing_info("1020060089973")

    call = client.session.calls[0]
    assert call["url"].endswith("/openapi/rest/CitingService/citingInfo")
    assert call["params"]["accessKey"] == "test-key"
    assert "ServiceKey" not in call["params"]
    assert call["params"]["standardCitationApplicationNumber"] == "1020060089973"


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

    result = fetch_kipris_bibliography("10-2022-0150081")

    assert result["citation_documents"][0]["display_number"] == "JP29047511 A"
    assert result["metadata"]["prior_art"] == ["JP29047511 A"]
    assert result["citation_stats"]["standardized_count"] == 1
    assert result["citing_documents"][0]["citing_application_number"] == "1020117007865"
    assert result["citing_stats"]["standardized_count"] == 1


def test_resolve_citation_evidence_enriches_kr_citation_and_citing_documents():
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
    )

    assert client.advanced_calls == [
        {"registerNumber": "1003093140000", "patent": True, "utility": False, "pageNo": 1, "numOfRows": 1},
        {"openNumber": "1020220029099", "patent": True, "utility": False, "pageNo": 1, "numOfRows": 1},
    ]
    assert client.detail_calls == ["1019990001111", "1020200012345", "1020117007865"]
    assert [item["application_number"] for item in result["kr_citation_documents"]] == [
        "1019990001111",
        "1020200012345",
    ]
    assert result["kr_citation_documents"][0]["representative_claims"][0]["text"] == "독립항 내용"
    assert result["kr_citing_documents"][0]["application_number"] == "1020117007865"
    assert result["foreign_claim_lookup_candidates"] == [
        {
            "direction": "cited_by_target",
            "country_code": "JP",
            "document_number": "29047511",
            "kind_code": "A",
            "display_number": "JP29047511 A",
            "lookup_source": "bigquery_claims",
        }
    ]


def test_resolve_citation_evidence_prioritizes_search_report_citing_documents():
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

    assert client.detail_calls == ["1020210146956", "1020210152036", "1020210140457"]
    assert [item["application_number"] for item in result["kr_citing_documents"]] == [
        "1020210146956",
        "1020210152036",
        "1020210140457",
    ]
