from services.patent.prior_art_patent_service import resolve_prior_art_candidate


class Response:
    content = b"%PDF-foreign"

    def raise_for_status(self):
        return None


class Session:
    def __init__(self):
        self.calls = []

    def get(self, url, timeout=None):
        self.calls.append({"url": url, "timeout": timeout})
        return Response()


def test_resolve_foreign_prior_art_collects_registration_fulltext(monkeypatch, tmp_path):
    class Client:
        def __init__(self):
            self.session = Session()
            self.timeout = 30.0
            self.registration_calls = []
            self.open_calls = []

        def overseas_registration_fulltext(self, literature_number, country_code):
            self.registration_calls.append((literature_number, country_code))
            if literature_number == "000004002589B2":
                return {
                    "response": {
                        "body": {
                            "items": {
                                "registrationFullTextInfo": {
                                    "docName": "jp_registration.pdf",
                                    "path": "https://example.com/jp_registration.pdf",
                                }
                            }
                        }
                    }
                }
            return {"response": {"body": {"items": {}}}}

        def overseas_open_fulltext(self, literature_number, country_code):
            self.open_calls.append((literature_number, country_code))
            return {"response": {"body": {"items": {}}}}

    client = Client()
    monkeypatch.setattr("services.patent.prior_art_patent_service.KiprisClient", lambda: client)
    monkeypatch.setattr(
        "services.patent.prior_art_patent_service.parse_single_patent_pdf",
        lambda pdf_path, output_dir: {
            "markdown_paths": [str(output_dir / "jp_registration.md")],
            "markdown_text": "JP registration full text with claims and detailed description",
        },
        raising=False,
    )

    result = resolve_prior_art_candidate(
        {
            "display_number": "JP4002589 B2",
            "country_code": "JP",
            "standard_number": "04002589",
            "kind_code": "B2",
            "original_number": "JP4002589 B2",
            "publication_date": "2007-11-07",
        },
        output_dir=tmp_path,
        collect_pdf=True,
        text_limit=None,
    )

    assert result["country_code"] == "JP"
    assert result["literature_number"] == "000004002589B2"
    assert result["foreign_fulltext_type"] == "registration"
    assert result["pdf_collected"] is True
    assert result["pdf_path"].endswith("jp_registration.pdf")
    assert result["pdf_text"] == "JP registration full text with claims and detailed description"
    assert client.registration_calls[0] == ("000004002589B2", "JP")
    assert client.open_calls == []
    assert client.session.calls[0]["url"] == "https://example.com/jp_registration.pdf"
