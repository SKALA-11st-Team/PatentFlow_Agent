from unittest.mock import Mock

import pytest
import requests
from fastapi import HTTPException
from fastapi.testclient import TestClient

from open_api import api_server


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
