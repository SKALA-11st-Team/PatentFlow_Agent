from open_api.kipris_client import KiprisClient
from services.patent.kipris_patent_service import _select_fulltext_pdf, fulltext_application_number_candidates


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
