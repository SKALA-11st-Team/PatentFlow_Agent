import pytest
import requests

from open_api.secret_scrub import scrub_secrets
from open_api.kipris_client import KiprisClient, KiprisError


def test_scrub_secrets_masks_query_auth_keys():
    assert scrub_secrets("http://x/api?ServiceKey=ABC123secret&q=1") == "http://x/api?ServiceKey=***&q=1"
    masked = scrub_secrets("accessKey=foo crtfc_key=bar serviceKey=baz")
    assert "foo" not in masked and "bar" not in masked and "baz" not in masked
    assert scrub_secrets("") == ""


def test_scrub_secrets_masks_env_secret(monkeypatch):
    monkeypatch.setenv("KIPRIS_SERVICE_KEY", "ENVKEY999")
    assert "ENVKEY999" not in scrub_secrets("call failed with ENVKEY999 in url")


def test_kipris_error_masks_service_key(monkeypatch):
    # EXT-10: 모든 키 실패 시 raise되는 KiprisError 메시지에 ServiceKey 평문이 남지 않아야 한다.
    client = KiprisClient(service_key="REALSECRET123")

    def boom(url, params=None, timeout=None):
        service_key = (params or {}).get("ServiceKey", "REALSECRET123")
        raise requests.RequestException(f"500 Server Error for url: {url}?ServiceKey={service_key}")

    monkeypatch.setattr(client.session, "get", boom)

    with pytest.raises(KiprisError) as exc_info:
        client.request("getAdvancedSearch", {"q": "x"})

    message = str(exc_info.value)
    assert "REALSECRET123" not in message
    assert "ServiceKey=***" in message
