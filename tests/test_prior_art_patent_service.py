import services.patent.prior_art_patent_service as prior_art_service
from services.patent.prior_art_patent_service import (
    build_prior_art_patent_context,
    collect_prior_art_candidates,
    prior_art_legal_content_from_markdown,
    resolve_prior_art_candidate,
)


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


def test_prior_art_fulltext_without_parsed_claims_has_separate_status():
    result = prior_art_legal_content_from_markdown(
        "(57)【要約】 統計的な工程監視を行う装置。",
        country_code="JP",
    )

    assert result["comparison_status"] == "fulltext_claims_unparsed"
    assert result["representative_claims"] == []


def test_prior_art_fulltext_exposes_technical_content():
    result = prior_art_legal_content_from_markdown(
        """
## 해결하려는 과제
처리 지연을 줄인다.
## 과제의 해결 수단
입력을 분할하여 병렬 처리한다.
## 발명의 효과
처리 시간이 감소한다.
## 발명을 실시하기 위한 구체적인 내용
프로세서는 분할된 입력을 복수 작업기에 전달한다.
""",
        country_code="KR",
    )

    assert result["technical_content"] == {
        "problem": "처리 지연을 줄인다.",
        "solution": "입력을 분할하여 병렬 처리한다.",
        "effect": "처리 시간이 감소한다.",
        "detailed_description": "프로세서는 분할된 입력을 복수 작업기에 전달한다.",
    }


def test_collect_prior_art_candidates_excludes_kind_code_digits_from_kr_document_number():
    candidates = collect_prior_art_candidates(
        target_metadata={
            "prior_art": [
                "KR102284539 B1",
                "KR102311787 B1",
                "KR1020210131720 A",
            ]
        },
        citation_documents=[],
    )

    by_display = {candidate["display_number"]: candidate for candidate in candidates}

    assert by_display["KR102284539 B1"]["standard_number"] == "102284539"
    assert by_display["KR102284539 B1"]["kind_code"] == "B1"
    assert by_display["KR102311787 B1"]["standard_number"] == "102311787"
    assert by_display["KR102311787 B1"]["kind_code"] == "B1"
    assert by_display["KR1020210131720 A"]["standard_number"] == "1020210131720"
    assert by_display["KR1020210131720 A"]["kind_code"] == "A"


def test_collect_prior_art_candidates_ranks_home_country_first():
    citation_documents = [
        {"display_number": "EP3550568 A1", "country_code": "EP", "kind_code": "A1"},
        {"display_number": "US2006053172 A1", "country_code": "US", "kind_code": "A1"},
        {"display_number": "JP2018123456 A", "country_code": "JP", "kind_code": "A"},
        {"display_number": "US9999999 B2", "country_code": "US", "kind_code": "B2"},
    ]

    candidates = collect_prior_art_candidates(
        target_metadata={},
        citation_documents=citation_documents,
        home_country="US",
    )
    order = [candidate["display_number"] for candidate in candidates]

    # 자국(US) 인용이 앞으로, 그중 등록(B kind)이 공개(A kind)보다 먼저
    assert order[0] == "US9999999 B2"
    assert order[1] == "US2006053172 A1"
    assert order.index("US2006053172 A1") < order.index("EP3550568 A1")
    assert order.index("US2006053172 A1") < order.index("JP2018123456 A")


def test_collect_prior_art_candidates_defaults_home_country_to_kr():
    citation_documents = [
        {"display_number": "US9999999 B2", "country_code": "US", "kind_code": "B2"},
        {"display_number": "KR102284539 B1", "country_code": "KR", "kind_code": "B1"},
    ]

    candidates = collect_prior_art_candidates(
        target_metadata={},
        citation_documents=citation_documents,
    )
    order = [candidate["display_number"] for candidate in candidates]

    assert order[0] == "KR102284539 B1"


def test_build_prior_art_context_stops_after_target_fulltext(monkeypatch):
    # 짝수 번호 후보만 전문 확보 성공한다고 가정 → 목표 3건 채우면 더 시도하지 않아야 한다.
    citation_documents = [
        {"display_number": f"US{index}", "country_code": "US", "kind_code": "B2"}
        for index in range(10)
    ]
    attempts: list[str] = []

    def fake_resolve(candidate, *, output_dir, collect_pdf, text_limit, fulltext_source):
        assert fulltext_source == "remote"
        display = candidate.get("display_number")
        attempts.append(display)
        index = int(display.removeprefix("US"))
        item = {"display_number": display, "_warnings": []}
        if index % 2 == 0:
            item["pdf_text"] = f"본문 {display}"
        return item

    monkeypatch.setattr(prior_art_service, "resolve_prior_art_candidate", fake_resolve)

    context = build_prior_art_patent_context(
        target_metadata={},
        kipris_api_data={"citation_documents": citation_documents},
        collect_pdf=True,
        target_fulltext_count=3,
        home_country="US",
    )

    assert context["fulltext_count"] == 3
    # US0, US2, US4 에서 3건 성공 → US4까지(앞에서부터 5건) 시도하고 멈춤. 10건 전부 시도하지 않음.
    assert attempts == ["US0", "US1", "US2", "US3", "US4"]


def test_build_prior_art_context_attempt_cap_when_fulltext_scarce(monkeypatch):
    # 전문이 전혀 안 잡히면 안전장치(max_resolution_attempts)까지만 시도하고 멈춘다.
    citation_documents = [
        {"display_number": f"EP{index}", "country_code": "EP", "kind_code": "A1"}
        for index in range(20)
    ]
    attempts: list[tuple[str, str]] = []

    def fake_resolve(candidate, *, output_dir, collect_pdf, text_limit, fulltext_source):
        attempts.append((fulltext_source, candidate.get("display_number")))
        return {"display_number": candidate.get("display_number"), "_warnings": []}

    monkeypatch.setattr(prior_art_service, "resolve_prior_art_candidate", fake_resolve)

    context = build_prior_art_patent_context(
        target_metadata={},
        kipris_api_data={"citation_documents": citation_documents},
        collect_pdf=True,
        target_fulltext_count=6,
        max_resolution_attempts=8,
        home_country="EP",
    )

    assert context["fulltext_count"] == 0
    assert [source for source, _display in attempts].count("remote") == 8
    assert [source for source, _display in attempts].count("google") == 8


def test_resolve_foreign_prior_art_collects_remote_fulltext_without_api(monkeypatch, tmp_path):
    # JP/US 인용 전문은 KIPRIS 전문 API(쿼터) 없이 remoteFile.do 공개 다운로드로 직접 받는다.
    class Client:
        def __init__(self):
            self.session = Session()  # 모든 GET에 %PDF 응답
            self.timeout = 30.0

    client = Client()
    monkeypatch.setattr("services.patent.prior_art_patent_service.KiprisClient", lambda: client)
    monkeypatch.setattr("services.evidence.news_article_extraction_service.validate_article_url", lambda _url: None)
    monkeypatch.setattr(
        "services.patent.prior_art_patent_service.parse_single_patent_pdf",
        lambda pdf_path, output_dir, country=None: {
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
    assert result["literature_number"] == "JP000004002589B2"
    assert result["foreign_fulltext_type"] == "kipris_remote_fulltext"
    assert result["pdf_collected"] is True
    assert result["pdf_text"] == "JP registration full text with claims and detailed description"
    # 쿼터 미사용 remoteFile.do 공개 URL로 받았는지 확인(전문 API 호출 없음).
    first_url = client.session.calls[0]["url"]
    assert "remoteFile.do" in first_url
    assert "publ_key=JP000004002589B2" in first_url


def test_build_prior_art_context_fills_remote_shortfall_with_google(monkeypatch):
    # 먼저 remoteFile.do만 전체 후보에 시도하고, 목표 건수에 부족할 때만 실패 후보를 Google로 보충한다.
    citation_documents = [
        {"display_number": f"US{index}", "country_code": "US", "kind_code": "A1"}
        for index in range(5)
    ]
    attempts: list[tuple[str, str]] = []

    def fake_resolve(candidate, *, output_dir, collect_pdf, text_limit, fulltext_source):
        display = candidate.get("display_number")
        attempts.append((fulltext_source, display))
        item = {"display_number": display, "_warnings": []}
        index = int(display.removeprefix("US"))
        if fulltext_source == "remote" and index == 1:
            item["pdf_text"] = f"remote 본문 {display}"
        if fulltext_source == "google" and index in {0, 2}:
            item["pdf_text"] = f"google 본문 {display}"
        return item

    monkeypatch.setattr(prior_art_service, "resolve_prior_art_candidate", fake_resolve)

    context = build_prior_art_patent_context(
        target_metadata={},
        kipris_api_data={"citation_documents": citation_documents},
        collect_pdf=True,
        target_fulltext_count=3,
        max_resolution_attempts=5,
        home_country="US",
    )

    assert context["fulltext_count"] == 3
    assert attempts == [
        ("remote", "US0"),
        ("remote", "US1"),
        ("remote", "US2"),
        ("remote", "US3"),
        ("remote", "US4"),
        ("google", "US0"),
        ("google", "US2"),
    ]
    assert [item["display_number"] for item in context["prior_art_patents"] if item.get("pdf_text")] == ["US0", "US1", "US2"]


def test_resolve_foreign_prior_art_remote_mode_does_not_fall_back_to_google(monkeypatch, tmp_path):
    # remoteFile.do가 PDF가 아닌 응답(구형 CN의 ZIP·HTML 등)을 주면 이 후보는 실패 처리하고,
    # 바깥 수집 루프가 다음 후보로 넘어가게 한다. Google은 2차 패스에서만 사용한다.
    class RoutedResponse:
        def __init__(self, content):
            self.content = content

        def raise_for_status(self):
            return None

    class RoutedSession:
        def __init__(self):
            self.calls = []

        def get(self, url, timeout=None):
            self.calls.append({"url": url, "timeout": timeout})
            # remoteFile.do(KIPRIS 공개 전문)는 PDF가 아닌 응답 → 폴백 유도. 그 외(Google)는 PDF.
            content = b"<html>not pdf</html>" if "remoteFile.do" in url else b"%PDF-google"
            return RoutedResponse(content)

    class Client:
        def __init__(self):
            self.session = RoutedSession()
            self.timeout = 30.0

    monkeypatch.setattr("services.patent.prior_art_patent_service.KiprisClient", Client)
    monkeypatch.setattr("services.evidence.news_article_extraction_service.validate_article_url", lambda _url: None)
    monkeypatch.setattr(
        "services.patent.prior_art_patent_service.google_patents_pdf_url",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("google fallback must not run in remote mode")),
    )
    monkeypatch.setattr(
        "services.patent.prior_art_patent_service.parse_single_patent_pdf",
        lambda pdf_path, output_dir, country=None: {
            "markdown_paths": [str(output_dir / "us_publication.md")],
            "markdown_text": "What is claimed is:\n1. A method comprising a processor and a memory.",
        },
    )

    result = resolve_prior_art_candidate(
        {
            "display_number": "US 2010241261 A1",
            "country_code": "US",
            "standard_number": "2010241261",
            "kind_code": "A1",
            "original_number": "US 2010241261 A1",
        },
        output_dir=tmp_path,
        collect_pdf=True,
        text_limit=None,
        fulltext_source="remote",
    )

    assert result["pdf_collected"] is False
    assert "foreign_fulltext_type" not in result
    assert any("not_pdf" in warning for warning in result["_warnings"])


def test_resolve_cn_prior_art_skips_kipris_fulltext_and_uses_google(monkeypatch, tmp_path):
    # 2차 Google 패스에서는 KIPRIS 전문 API(쿼터)를 쓰지 않고 Google Patents PDF만 시도한다.
    class Client:
        def __init__(self):
            self.session = Session()
            self.timeout = 30.0
            self.open_calls = []
            self.registration_calls = []

        def overseas_open_fulltext(self, literature_number, country_code):
            self.open_calls.append((literature_number, country_code))
            return {"response": {"body": {"items": {}}}}

        def overseas_registration_fulltext(self, literature_number, country_code):
            self.registration_calls.append((literature_number, country_code))
            return {"response": {"body": {"items": {}}}}

    client = Client()
    monkeypatch.setattr("services.patent.prior_art_patent_service.KiprisClient", lambda: client)
    monkeypatch.setattr("services.evidence.news_article_extraction_service.validate_article_url", lambda _url: None)
    monkeypatch.setattr(
        "services.patent.prior_art_patent_service.google_patents_pdf_url",
        lambda *args, **kwargs: "https://example.com/cn-publication.pdf",
    )
    monkeypatch.setattr(
        "services.patent.prior_art_patent_service.parse_single_patent_pdf",
        lambda pdf_path, output_dir, country=None: {
            "markdown_paths": [str(output_dir / "cn.md")],
            "markdown_text": "权利要求\n1. 一种半导体工艺监测方法。",
        },
    )

    result = resolve_prior_art_candidate(
        {
            "display_number": "CN 1894652 A",
            "country_code": "CN",
            "document_number": "1894652",
            "standard_number": "1894652",
            "kind_code": "A",
            "original_number": "CN 1894652 A",
        },
        output_dir=tmp_path,
        collect_pdf=True,
        text_limit=None,
        fulltext_source="google",
    )

    # KIPRIS 전문 메서드는 한 번도 호출되지 않아야 한다(쿼터 0).
    assert client.open_calls == []
    assert client.registration_calls == []
    assert result["foreign_fulltext_type"] == "google_patents"
    assert result["pdf_collected"] is True


def _make_tiff_zip() -> bytes:
    import io as _io
    import zipfile as _zip

    buffer = _io.BytesIO()
    with _zip.ZipFile(buffer, "w") as archive:
        archive.writestr("000001.TIF", b"II*\x00fake-tiff-page-1")
        archive.writestr("000002.TIF", b"II*\x00fake-tiff-page-2")
    return buffer.getvalue()


def test_download_kipris_remote_fulltext_ocrs_zip_of_tiffs(monkeypatch, tmp_path):
    # 구형 특허는 PDF 대신 ZIP(of TIFF)으로 온다 → tesseract OCR로 전문 텍스트를 만든다.
    import services.patent.prior_art_patent_service as svc

    zip_bytes = _make_tiff_zip()

    class Resp:
        content = zip_bytes

        def raise_for_status(self):
            return None

    class Sess:
        def get(self, url, timeout=None):
            return Resp()

    class Client:
        session = Sess()
        timeout = 30.0

    monkeypatch.setattr(svc.shutil, "which", lambda name: "/usr/bin/tesseract")

    class Completed:
        # 페이지당 충분한 길이의 OCR 텍스트(중국어 전문 가정)
        stdout = "본문 텍스트 " * 60

    monkeypatch.setattr(svc.subprocess, "run", lambda *a, **k: Completed())

    result = svc.download_kipris_remote_fulltext(Client(), "CN200480037968A0", "CN", output_dir=tmp_path)
    assert result is not None
    assert result["kind"] == "ocr"
    assert "본문 텍스트" in result["markdown_text"]
    assert result["markdown_path"].exists()


def test_download_kipris_remote_fulltext_returns_pdf(monkeypatch, tmp_path):
    import services.patent.prior_art_patent_service as svc

    class Resp:
        content = b"%PDF-1.7 fake"

        def raise_for_status(self):
            return None

    class Sess:
        def get(self, url, timeout=None):
            return Resp()

    class Client:
        session = Sess()
        timeout = 30.0

    result = svc.download_kipris_remote_fulltext(Client(), "US000012417849B2", "US", output_dir=tmp_path)
    assert result["kind"] == "pdf"
    assert result["pdf_path"].suffix == ".pdf"
