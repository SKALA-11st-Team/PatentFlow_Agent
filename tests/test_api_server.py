from unittest.mock import Mock

import pytest
import requests
from fastapi import HTTPException
from fastapi.testclient import TestClient

from open_api import api_server
from open_api.kipris_client import KiprisError, raise_for_kipris_body_error


@pytest.fixture(autouse=True)
def _clear_rate_limit_window():
    # SEC-01: 인프로세스 레이트리밋 윈도우는 모듈 전역이므로 테스트 간 누적되지 않도록 초기화한다.
    api_server._rate_limit_windows.clear()
    yield
    api_server._rate_limit_windows.clear()


def test_rate_limit_blocks_after_threshold(monkeypatch):
    monkeypatch.setenv("UNIFIED_API_RATELIMIT_PER_MINUTE", "2")
    monkeypatch.delenv("UNIFIED_API_KEY", raising=False)
    monkeypatch.setenv("NAVER_CLIENT_ID", "client")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "secret")
    monkeypatch.setattr(
        api_server.requests,
        "get",
        Mock(return_value=Mock(json=lambda: {"items": []}, raise_for_status=lambda: None)),
    )
    client = TestClient(api_server.app)

    assert client.get("/api/news/search", params={"query": "AI"}).status_code == 200
    assert client.get("/api/news/search", params={"query": "AI"}).status_code == 200
    # 윈도우(1분) 내 한도(2)를 넘긴 3번째 요청은 429.
    assert client.get("/api/news/search", params={"query": "AI"}).status_code == 429


def test_rate_limit_exempts_trusted_internal_caller(monkeypatch):
    # SEC-01: 유효한 X-API-Key(에이전트)는 단일 egress IP fan-out이 429로 차단되지 않도록 면제한다.
    monkeypatch.setenv("UNIFIED_API_RATELIMIT_PER_MINUTE", "2")
    monkeypatch.setenv("UNIFIED_API_KEY", "gateway-secret")
    monkeypatch.setenv("NAVER_CLIENT_ID", "client")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "secret")
    monkeypatch.setattr(
        api_server.requests,
        "get",
        Mock(return_value=Mock(json=lambda: {"items": []}, raise_for_status=lambda: None)),
    )
    client = TestClient(api_server.app)
    headers = {"X-API-Key": "gateway-secret"}

    # 한도(2)를 넘는 3번째 요청도 인증된 내부 호출자는 200(레이트리밋 미적용).
    for _ in range(3):
        assert client.get("/api/news/search", params={"query": "AI"}, headers=headers).status_code == 200
    # 면제된 호출자는 IP 윈도우에 집계되지 않는다.
    assert api_server._rate_limit_windows == {}


def test_rate_limit_reclaims_stale_ip_window(monkeypatch):
    # SEC-01: 만료 엔트리만 남은 다른 IP의 윈도우는 회수돼 메모리 누수가 없어야 한다.
    monkeypatch.setenv("UNIFIED_API_RATELIMIT_PER_MINUTE", "120")
    monkeypatch.delenv("UNIFIED_API_KEY", raising=False)
    monkeypatch.setenv("NAVER_CLIENT_ID", "client")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "secret")
    monkeypatch.setattr(
        api_server.requests,
        "get",
        Mock(return_value=Mock(json=lambda: {"items": []}, raise_for_status=lambda: None)),
    )
    # 더 이상 요청하지 않는 옛 IP의 만료된 윈도우를 주입한다.
    api_server._rate_limit_windows["10.0.0.99"] = api_server.deque([api_server.time.monotonic() - 120.0])

    assert TestClient(api_server.app).get("/api/news/search", params={"query": "AI"}).status_code == 200

    assert "10.0.0.99" not in api_server._rate_limit_windows


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


def test_naver_search_scrubs_query_secret_in_error(monkeypatch):
    # EXT-10: NAVER 에러 경로도 GNews/KIPRIS와 동일하게 시크릿을 마스킹해야 한다.
    monkeypatch.setenv("NAVER_CLIENT_ID", "client")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "secret")
    monkeypatch.setattr(
        api_server.requests,
        "get",
        Mock(side_effect=requests.RequestException("conn failed for url ...?ServiceKey=LEAKED123")),
    )

    with pytest.raises(HTTPException) as exc_info:
        api_server.naver_news_search(query="AI")

    assert exc_info.value.status_code == 502
    assert "LEAKED123" not in exc_info.value.detail
    assert "ServiceKey=***" in exc_info.value.detail


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
