from unittest.mock import Mock

import pytest
import requests
from fastapi import HTTPException
from fastapi.testclient import TestClient

from open_api import api_server
from open_api.kipris_client import KiprisError, raise_for_kipris_body_error


def test_gnews_search_preserves_upstream_http_status(monkeypatch):
    monkeypatch.setenv("GNEWS_API_KEY", "test-key")

    response = Mock()
    response.status_code = 403
    response.raise_for_status.side_effect = requests.HTTPError(
        "403 Client Error: Forbidden for url: https://gnews.io/api/v4/search?apikey=test-key",
        response=response,
    )
    monkeypatch.setattr(api_server.requests, "get", Mock(return_value=response))

    with pytest.raises(HTTPException) as exc_info:
        api_server.gnews_search(q="ham")

    assert exc_info.value.status_code == 403
    assert "test-key" not in exc_info.value.detail


def test_unified_api_key_middleware_blocks_missing_key(monkeypatch):
    monkeypatch.setenv("UNIFIED_API_KEY", "gateway-secret")

    response = TestClient(api_server.app).get("/api/news/search", params={"query": "AI"})

    assert response.status_code == 401


def test_unified_api_key_middleware_accepts_matching_key(monkeypatch):
    monkeypatch.setenv("UNIFIED_API_KEY", "gateway-secret")
    monkeypatch.setenv("NAVER_CLIENT_ID", "client")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "secret")
    monkeypatch.setattr(api_server.requests, "get", Mock(return_value=Mock(json=lambda: {"items": []}, raise_for_status=lambda: None)))

    response = TestClient(api_server.app).get(
        "/api/news/search",
        params={"query": "AI"},
        headers={"X-API-Key": "gateway-secret"},
    )

    assert response.status_code == 200


def test_kipris_body_level_error_is_raised():
    with pytest.raises(KiprisError, match="INVALID REQUEST"):
        raise_for_kipris_body_error(
            {
                "response": {
                    "header": {
                        "resultCode": "03",
                        "resultMsg": "INVALID REQUEST",
                    }
                }
            }
        )


def test_citation_info_v3_route_uses_access_key(monkeypatch):
    class Client:
        def __init__(self):
            self.service_key = None

        def citation_info_v3(self, application_number):
            return {
                "service_key": self.service_key,
                "application_number": application_number,
                "citationInfoV3": [],
            }

    client = Client()
    monkeypatch.setattr(api_server, "_client", lambda service_key=None: setattr(client, "service_key", service_key) or client)

    response = TestClient(api_server.app).get(
        "/openapi/rest/CitationService/citationInfoV3",
        params={"accessKey": "rest-key", "applicationNumber": "1020220150081"},
    )

    assert response.status_code == 200
    assert response.json()["service_key"] == "rest-key"
    assert response.json()["application_number"] == "1020220150081"


def test_citing_info_route_uses_access_key(monkeypatch):
    class Client:
        def __init__(self):
            self.service_key = None

        def citing_info(self, standard_citation_application_number):
            return {
                "service_key": self.service_key,
                "standard_citation_application_number": standard_citation_application_number,
                "citingInfo": [],
            }

    client = Client()
    monkeypatch.setattr(api_server, "_client", lambda service_key=None: setattr(client, "service_key", service_key) or client)

    response = TestClient(api_server.app).get(
        "/openapi/rest/CitingService/citingInfo",
        params={"accessKey": "rest-key", "standardCitationApplicationNumber": "1020060089973"},
    )

    assert response.status_code == 200
    assert response.json()["service_key"] == "rest-key"
    assert response.json()["standard_citation_application_number"] == "1020060089973"


def test_overseas_open_fulltext_route_uses_access_key(monkeypatch):
    class Client:
        def __init__(self):
            self.service_key = None

        def overseas_open_fulltext(self, literature_number, country_code):
            return {
                "service_key": self.service_key,
                "literature_number": literature_number,
                "country_code": country_code,
                "openFullTextInfo": {"path": "http://example.com/open.pdf"},
            }

    client = Client()
    monkeypatch.setattr(api_server, "_client", lambda service_key=None: setattr(client, "service_key", service_key) or client)

    response = TestClient(api_server.app).get(
        "/kipris/overseas-patent/documents/open-fulltext",
        params={"accessKey": "rest-key", "literatureNumber": "002017047511A0", "countryCode": "JP"},
    )

    assert response.status_code == 200
    assert response.json()["service_key"] == "rest-key"
    assert response.json()["literature_number"] == "002017047511A0"
    assert response.json()["country_code"] == "JP"


def test_overseas_registration_fulltext_route_uses_access_key(monkeypatch):
    class Client:
        def __init__(self):
            self.service_key = None

        def overseas_registration_fulltext(self, literature_number, country_code):
            return {
                "service_key": self.service_key,
                "literature_number": literature_number,
                "country_code": country_code,
                "registrationFullTextInfo": {"path": "http://example.com/registration.pdf"},
            }

    client = Client()
    monkeypatch.setattr(api_server, "_client", lambda service_key=None: setattr(client, "service_key", service_key) or client)

    response = TestClient(api_server.app).get(
        "/kipris/overseas-patent/documents/registration-fulltext",
        params={"accessKey": "rest-key", "literatureNumber": "000004002589B2", "countryCode": "JP"},
    )

    assert response.status_code == 200
    assert response.json()["service_key"] == "rest-key"
    assert response.json()["literature_number"] == "000004002589B2"
    assert response.json()["country_code"] == "JP"
