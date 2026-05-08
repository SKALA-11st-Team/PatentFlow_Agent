from unittest.mock import Mock

import pytest
import requests
from fastapi import HTTPException

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
