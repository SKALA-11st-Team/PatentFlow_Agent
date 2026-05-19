from services.evidence.skax_business_source_service import collect_skax_business_sources


SEED_URL = "https://www.skax.co.kr/manufacturing"
DETAIL_URL = "https://www.skax.co.kr/manufacturing/resource-allocation-automation"
OTHER_DOMAIN_URL = "https://example.com/manufacturing/external"
OUTSIDE_PREFIX_URL = "https://www.skax.co.kr/about"
FILE_URL = "https://www.skax.co.kr/manufacturing/brochure.pdf"
EXTRA_URL = "https://www.skax.co.kr/manufacturing/extra-solution"


def fake_fetcher(html_by_url):
    def _fetch(url):
        value = html_by_url[url]
        if isinstance(value, Exception):
            raise value
        return value

    return _fetch


def test_collects_seed_and_detail_pages_with_depth_one():
    result = collect_skax_business_sources(
        [SEED_URL],
        fetcher=fake_fetcher(
            {
                SEED_URL: f"""
                <html>
                  <head><title>Manufacturing</title></head>
                  <body>
                    <h1>제조 사업</h1>
                    <p>SK AX 제조 AI 솔루션 소개</p>
                    <a href="/manufacturing/resource-allocation-automation">상세</a>
                    <a href="{OUTSIDE_PREFIX_URL}">회사 소개</a>
                    <a href="{OTHER_DOMAIN_URL}">외부</a>
                    <a href="/manufacturing/brochure.pdf">PDF</a>
                  </body>
                </html>
                """,
                DETAIL_URL: """
                <html>
                  <head><title>Resource Allocation Automation</title></head>
                  <body><p>생산 자원 배분 자동화 솔루션 상세 설명</p></body>
                </html>
                """,
            }
        ),
    )

    urls = [item["url"] for item in result["items"]]
    assert urls == [SEED_URL, DETAIL_URL]
    assert result["stats"]["attempted_url_count"] == 2
    assert result["stats"]["collected_evidence_count"] == 2
    assert result["stats"]["skipped_url_count"] == 3


def test_max_depth_zero_collects_seed_page_only():
    result = collect_skax_business_sources(
        [SEED_URL],
        max_depth=0,
        fetcher=fake_fetcher(
            {
                SEED_URL: f"""
                <html>
                  <head><title>Manufacturing</title></head>
                  <body>
                    <p>제조 대표 페이지</p>
                    <a href="{DETAIL_URL}">상세</a>
                  </body>
                </html>
                """,
                DETAIL_URL: "<html><body><p>수집되면 안 되는 상세 페이지</p></body></html>",
            }
        ),
    )

    assert [item["url"] for item in result["items"]] == [SEED_URL]
    assert result["stats"]["attempted_url_count"] == 1


def test_skips_outside_prefix_external_and_file_links():
    result = collect_skax_business_sources(
        [SEED_URL],
        fetcher=fake_fetcher(
            {
                SEED_URL: f"""
                <html>
                  <head><title>Manufacturing</title></head>
                  <body>
                    <p>제조 대표 페이지</p>
                    <a href="{OUTSIDE_PREFIX_URL}">prefix 밖</a>
                    <a href="{OTHER_DOMAIN_URL}">외부 도메인</a>
                    <a href="{FILE_URL}#download">파일 링크</a>
                  </body>
                </html>
                """,
            }
        ),
    )

    assert [item["url"] for item in result["items"]] == [SEED_URL]
    assert OUTSIDE_PREFIX_URL in result["skipped_urls"]
    assert OTHER_DOMAIN_URL in result["skipped_urls"]
    assert FILE_URL in result["skipped_urls"]


def test_normalized_evidence_shape_contains_business_fit_fields():
    result = collect_skax_business_sources(
        [SEED_URL],
        fetcher=fake_fetcher(
            {
                SEED_URL: """
                <html>
                  <head><title>Manufacturing</title></head>
                  <body><p>SK AX 제조 솔루션 본문</p></body>
                </html>
                """,
            }
        ),
    )

    evidence = result["items"][0]
    assert evidence["evidence_id"].startswith("skax_business_")
    assert evidence["source_type"] == "company_disclosure"
    assert evidence["source"] == "sk_ax_official"
    assert evidence["title"] == "Manufacturing"
    assert evidence["url"] == SEED_URL
    assert evidence["content"] == "SK AX 제조 솔루션 본문"
    assert evidence["collected_at"]
    assert "business_fit" in evidence["related_axes"]
    assert evidence["business_domain"] == "manufacturing"
    assert evidence["crawl_depth"] == 0


def test_fetch_failure_and_empty_html_do_not_fail_collection():
    empty_url = "https://www.skax.co.kr/manufacturing/empty"
    failed_url = "https://www.skax.co.kr/manufacturing/failure"
    result = collect_skax_business_sources(
        [SEED_URL],
        extra_urls=[empty_url, failed_url],
        fetcher=fake_fetcher(
            {
                SEED_URL: """
                <html>
                  <head><title>Manufacturing</title></head>
                  <body><p>정상 페이지</p></body>
                </html>
                """,
                empty_url: "   ",
                failed_url: RuntimeError("boom"),
            }
        ),
    )

    assert [item["url"] for item in result["items"]] == [SEED_URL]
    assert result["stats"]["collected_evidence_count"] == 1
    assert result["stats"]["failed_url_count"] == 1
    assert result["stats"]["skipped_url_count"] == 1
    assert failed_url in result["failed_urls"]
    assert empty_url in result["skipped_urls"]


def test_extra_urls_can_collect_undiscovered_page():
    result = collect_skax_business_sources(
        [SEED_URL],
        extra_urls=[EXTRA_URL],
        max_depth=0,
        fetcher=fake_fetcher(
            {
                SEED_URL: """
                <html>
                  <head><title>Manufacturing</title></head>
                  <body><p>제조 대표 페이지</p></body>
                </html>
                """,
                EXTRA_URL: """
                <html>
                  <head><title>Extra Solution</title></head>
                  <body><p>자동 발견되지 않은 솔루션 페이지</p></body>
                </html>
                """,
            }
        ),
    )

    assert [item["url"] for item in result["items"]] == [SEED_URL, EXTRA_URL]


def test_truncates_long_content_and_reports_stats():
    result = collect_skax_business_sources(
        [SEED_URL],
        max_content_chars=20,
        fetcher=fake_fetcher(
            {
                SEED_URL: f"""
                <html>
                  <head><title>Manufacturing</title></head>
                  <body><p>{'긴본문' * 20}</p></body>
                </html>
                """,
            }
        ),
    )

    assert len(result["items"][0]["content"]) == 20
    assert result["stats"]["truncated_content_count"] == 1
    assert result["stats"]["max_depth"] == 1
    assert result["stats"]["max_pages"] == 20
