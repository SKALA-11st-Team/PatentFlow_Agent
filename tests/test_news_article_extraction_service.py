import socket

from services.evidence.news_article_extraction_service import enrich_news_items_with_full_text, fetch_article_text


class FakeResponse:
    status_code = 200
    text = """
    <html>
      <head>
        <meta property="og:title" content="AI 자산관리 시장 경쟁 확대와 투자 서비스 변화">
      </head>
      <body>
        <h1>짧은 제목</h1>
        <p>AI 자산관리 서비스가 금융권에서 확대되고 있다. 로보어드바이저와 자동화 투자 서비스는 퇴직연금과 개인 자산관리 영역에서 도입 사례를 늘리고 있다.</p>
        <p>기업들은 데이터 분석, 포트폴리오 관리, 투자 성향 분석 기능을 결합해 서비스 경쟁력을 높이고 있으며, 시장 참여자들은 관련 기술의 상용화 가능성을 검토하고 있다.</p>
        <p>이러한 흐름은 금융 서비스 시장에서 인공지능 기반 투자 지원 도구가 점차 일반 서비스로 편입되고 있음을 보여준다.</p>
        <p>추가 문장입니다. 추가 문장입니다. 추가 문장입니다. 추가 문장입니다. 추가 문장입니다. 추가 문장입니다. 추가 문장입니다.</p>
      </body>
    </html>
    """

    def raise_for_status(self):
        return None


def _fake_getaddrinfo(ip: str):
    def _resolver(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]

    return _resolver


def _failing_getaddrinfo(*args, **kwargs):
    raise socket.gaierror("name resolution failed")


def _patch_dns(monkeypatch, resolver):
    monkeypatch.setattr(
        "services.evidence.news_article_extraction_service.socket.getaddrinfo", resolver
    )


def test_enrich_news_items_updates_title_from_crawled_article(monkeypatch):
    _patch_dns(monkeypatch, _fake_getaddrinfo("93.184.216.34"))
    monkeypatch.setattr(
        "services.evidence.news_article_extraction_service.requests.get",
        lambda *args, **kwargs: FakeResponse(),
    )
    items = [
        {
            "evidence_id": "news_1",
            "source_type": "news",
            "source": "naver_news",
            "title": "AI 자산관리 시장...",
            "url": "https://example.com/news",
            "content": "검색 결과 요약",
            "metadata": {},
        }
    ]

    enriched = enrich_news_items_with_full_text(items)

    assert enriched[0]["title"] == "AI 자산관리 시장 경쟁 확대와 투자 서비스 변화"
    assert enriched[0]["metadata"]["original_title"] == "AI 자산관리 시장..."
    assert enriched[0]["metadata"]["content_source"] in {"full_text_fallback_parser", "snippet"}


def test_fetch_article_text_blocks_private_ip_url(monkeypatch):
    called = False

    def fake_get(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr("services.evidence.news_article_extraction_service.requests.get", fake_get)

    result = fetch_article_text("http://127.0.0.1/internal")

    assert result["error"] == "article_url_blocked:private_or_link_local_ip"
    assert called is False


def test_fetch_article_text_blocks_private_dns_host(monkeypatch):
    # EVID-05: 도메인이 사설 IP로 해석되면 차단되어야 한다.
    called = False

    def fake_get(*args, **kwargs):
        nonlocal called
        called = True

    _patch_dns(monkeypatch, _fake_getaddrinfo("10.0.0.5"))
    monkeypatch.setattr("services.evidence.news_article_extraction_service.requests.get", fake_get)

    result = fetch_article_text("http://internal.example/article")

    assert result["error"] == "article_url_blocked:private_or_link_local_ip"
    assert called is False


def test_fetch_article_text_blocks_link_local_dns_host(monkeypatch):
    # EVID-05: 클라우드 메타데이터(169.254.169.254)로 해석되는 도메인 차단.
    called = False

    def fake_get(*args, **kwargs):
        nonlocal called
        called = True

    _patch_dns(monkeypatch, _fake_getaddrinfo("169.254.169.254"))
    monkeypatch.setattr("services.evidence.news_article_extraction_service.requests.get", fake_get)

    result = fetch_article_text("http://metadata.attacker.example/latest/meta-data/")

    assert result["error"] == "article_url_blocked:private_or_link_local_ip"
    assert called is False


def test_fetch_article_text_allows_public_dns_host(monkeypatch):
    # 회귀 가드: 글로벌 IP로 해석되는 정상 도메인은 차단되지 않고 실제 요청까지 진행한다.
    called = {"value": False}

    def fake_get(*args, **kwargs):
        called["value"] = True
        return FakeResponse()

    _patch_dns(monkeypatch, _fake_getaddrinfo("8.8.8.8"))
    monkeypatch.setattr("services.evidence.news_article_extraction_service.requests.get", fake_get)

    result = fetch_article_text("https://news.example/article")

    assert called["value"] is True
    assert not str(result["error"] or "").startswith("article_url_blocked")


def test_fetch_article_text_blocks_dns_failure(monkeypatch):
    # EVID-05: DNS 해석 실패 호스트는 차단(해석 불가 = 신뢰 불가).
    called = False

    def fake_get(*args, **kwargs):
        nonlocal called
        called = True

    _patch_dns(monkeypatch, _failing_getaddrinfo)
    monkeypatch.setattr("services.evidence.news_article_extraction_service.requests.get", fake_get)

    result = fetch_article_text("http://does-not-resolve.example/")

    assert result["error"] == "article_url_blocked:dns_resolution_failed"
    assert called is False
