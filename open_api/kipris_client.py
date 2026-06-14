"""KIPRISPlus 직접 호출용 클라이언트.

- Wrapper 서버 없이 KIPRISPlus 원천 API URL을 직접 호출합니다.
- 기본 base_url: http://plus.kipris.or.kr
- 인증 파라미터명: ServiceKey
- XML 응답을 dict로 변환합니다.

주의:
KIPRISPlus는 서비스/오퍼레이션별 실제 경로가 통합설명서 기준으로 확정되어야 합니다.
현재 특허·실용 공개·등록공보 계열은 사용자가 제공한 공개전문PDF 샘플 경로와 동일한
/kipo-api/kipi/patUtiModInfoSearchSevice/{operationName} 패턴으로 구성했습니다.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
import os
import re
import time

from open_api.secret_scrub import scrub_secrets
import xml.etree.ElementTree as ET

import requests


DEFAULT_BASE_URL = "http://plus.kipris.or.kr"
PAT_UTI_SERVICE_PATH = "/kipo-api/kipi/patUtiModInfoSearchSevice"
PAT_UTI_REST_SERVICE_PATH = "/openapi/rest/patUtiModInfoSearchSevice"
PAT_UTI_TRANSFER_HIST_PATH = "/kipo-api/kipi/patUtiModTransferHistInfoSearchSevice"
OVERSEAS_PATENT_SERVICE_PATH = "/openapi/rest/ForeignPatentBibliographicService"
OVERSEAS_IMAGE_FULLTEXT_SERVICE_PATH = "/openapi/rest/ForeignPatentImageAndFullTextService"
OVERSEAS_ADVANCED_SEARCH_SERVICE_PATH = "/openapi/rest/ForeignPatentAdvencedSearchService"
PAT_FAMILY_SERVICE_PATH = "/kipo-api/kipi/patFamInfoSearchService"
CITATION_SERVICE_PATH = "/openapi/rest/CitationService"
CITING_SERVICE_PATH = "/openapi/rest/CitingService"


class KiprisError(RuntimeError):
    """KIPRISPlus 호출 또는 응답 파싱 오류."""


KIPRIS_BODY_ERROR_CODES = {
    "01",
    "02",
    "03",
    "04",
    "05",
    "10",
    "11",
    "12",
    "20",
    "22",
    "30",
    "31",
    "32",
    "99",
}


def _resolve_service_keys(explicit: str | None = None) -> list[str]:
    """KIPRIS 키 목록을 구성한다. 한도(쿼터) 분산을 위해 다중 키를 지원한다.

    우선순위: 명시 인자 > KIPRIS_SERVICE_KEYS(콤마/공백 구분 다중) > KIPRIS_SERVICE_KEY
    > KIPRIS_API_KEY > SERVICE_KEY. 중복은 순서를 유지하며 제거한다.
    """
    raw = (
        explicit
        or os.getenv("KIPRIS_SERVICE_KEYS")
        or os.getenv("KIPRIS_SERVICE_KEY")
        or os.getenv("KIPRIS_API_KEY")
        or os.getenv("SERVICE_KEY")
        or ""
    )
    keys: list[str] = []
    for part in re.split(r"[,\s]+", raw):
        part = part.strip()
        if part and part not in keys:
            keys.append(part)
    return keys


# 인스턴스가 호출마다 새로 생성될 수 있어(_kipris_client), 키 라운드로빈 시작점을
# 모듈 전역 커서로 유지해 호출 간에도 부하가 고르게 분산되도록 한다.
_key_cursor = 0


@dataclass(frozen=True)
class KiprisDocumentPath:
    doc_name: str | None
    path: str | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class KiprisFamilyPatent:
    country_code: str | None
    registration_number: str | None
    raw: dict[str, Any]


def _strip_namespace(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _element_to_obj(elem: ET.Element) -> Any:
    children = list(elem)
    if not children:
        return (elem.text or "").strip()

    result: dict[str, Any] = {}
    for child in children:
        key = _strip_namespace(child.tag)
        value = _element_to_obj(child)
        if key in result:
            if not isinstance(result[key], list):
                result[key] = [result[key]]
            result[key].append(value)
        else:
            result[key] = value
    return result


def parse_xml_response(xml_text: str) -> dict[str, Any]:
    """KIPRIS XML 응답을 Python dict로 변환합니다."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise KiprisError(f"XML 파싱 실패: {exc}") from exc
    return {_strip_namespace(root.tag): _element_to_obj(root)}


def raise_for_kipris_body_error(data: Mapping[str, Any]) -> None:
    """KIPRISPlus가 HTTP 200 본문에 담아 돌려주는 오류를 표면화합니다."""
    result_code = first_nested_value(data, ("resultCode", "returnCode", "errorCode"))
    result_msg = first_nested_value(data, ("resultMsg", "returnMsg", "errorMsg", "message"))
    if result_code is None:
        return

    code = str(result_code).strip()
    message = str(result_msg or "").strip()
    if code in {"", "00", "0", "000", "SUCCESS", "success"}:
        return
    if code in KIPRIS_BODY_ERROR_CODES or message:
        detail = f"KIPRIS 응답 오류(resultCode={code})"
        if message:
            detail = f"{detail}: {message}"
        raise KiprisError(detail)


def first_nested_value(value: Any, keys: tuple[str, ...]) -> Any:
    if isinstance(value, Mapping):
        for key in keys:
            found = value.get(key)
            if found not in (None, ""):
                return found
        for nested in value.values():
            found = first_nested_value(nested, keys)
            if found not in (None, ""):
                return found
    elif isinstance(value, list):
        for item in value:
            found = first_nested_value(item, keys)
            if found not in (None, ""):
                return found
    return None


def _get_nested(data: Mapping[str, Any], *keys: str) -> Any:
    cur: Any = data
    for key in keys:
        if not isinstance(cur, Mapping):
            return None
        cur = cur.get(key)
    return cur


def _first_present(data: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return None


def _iter_mappings(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, Mapping)]
    return []


class KiprisClient:
    """KIPRISPlus 원천 API 직접 호출 클라이언트."""

    def __init__(
        self,
        service_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
        sleep_seconds: float = 0.0,
    ) -> None:
        self.service_keys = _resolve_service_keys(service_key)
        if not self.service_keys:
            raise ValueError("service_key 또는 환경변수 KIPRIS_SERVICE_KEY(S)가 필요합니다.")
        # 단일 키 참조 하위호환
        self.service_key = self.service_keys[0]
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.sleep_seconds = sleep_seconds
        self.session = requests.Session()

    def _url(self, service_path: str, operation_name: str) -> str:
        return f"{self.base_url}{service_path}/{operation_name}"

    def request(
        self,
        operation_name: str,
        params: Mapping[str, Any] | None = None,
        *,
        service_path: str = PAT_UTI_SERVICE_PATH,
        parse_xml: bool = True,
        auth_param: str = "ServiceKey",
    ) -> dict[str, Any] | str:
        """임의의 KIPRIS operationName을 직접 호출합니다."""
        if self.sleep_seconds:
            time.sleep(self.sleep_seconds)

        query: dict[str, Any] = {}
        if params:
            for k, v in params.items():
                if v is None:
                    continue
                if isinstance(v, bool):
                    query[k] = str(v).lower()
                else:
                    query[k] = v

        url = self._url(service_path, operation_name)

        # 한도(쿼터) 회피: 키 목록을 라운드로빈으로 시작점만 바꿔 부하를 분산하고,
        # 호출 실패(429/5xx/네트워크 오류) 시 남은 키로 순차 재시도한다.
        global _key_cursor
        count = len(self.service_keys)
        start = _key_cursor % count
        _key_cursor += 1
        ordered = [self.service_keys[(start + i) % count] for i in range(count)]

        last_exc: Exception | None = None
        for key in ordered:
            attempt = dict(query)
            attempt[auth_param] = key
            try:
                response = self.session.get(url, params=attempt, timeout=self.timeout)
                response.raise_for_status()
            except requests.RequestException as exc:
                last_exc = exc
                continue
            text = response.text.strip()
            if not parse_xml:
                return text
            parsed = parse_xml_response(text)
            raise_for_kipris_body_error(parsed)
            return parsed

        # EXT-10: last_exc(requests 예외)의 str()에는 ServiceKey=... 평문 URL이 포함되므로 마스킹한다.
        # 이 메시지는 워크플로 warnings→API 응답·아티팩트로 전파될 수 있다.
        raise KiprisError(
            f"KIPRIS 호출 실패(키 {count}개 모두 실패): {scrub_secrets(str(last_exc))}"
        ) from last_exc

    # -------------------------
    # 특허·실용 공개·등록공보
    # -------------------------
    def advanced_search(self, **params: Any) -> dict[str, Any]:
        """전체검색 - getAdvancedSearch"""
        return self.request("getAdvancedSearch", params)

    def search_by_application_number(self, application_number: str, **params: Any) -> dict[str, Any]:
        """출원번호 검색 - getAdvancedSearch(applicationNumber).

        `applicationNumberSearchInfo` operation은 이 서비스 경로/키에서 INVALID_REQUEST_PARAMETER로
        실패한다(존재하지 않는 operation). 전체검색 getAdvancedSearch에 applicationNumber를 넘기면
        정상 동작하며(resultCode 00), 응답 shape도 extract_kipris_items가 이미 처리한다.
        """
        params.update({"applicationNumber": application_number})
        return self.request("getAdvancedSearch", params)

    def bibliography_detail(self, application_number: str) -> dict[str, Any]:
        """서지상세정보 - getBibliographyDetailInfoSearch"""
        return self.request(
            "getBibliographyDetailInfoSearch",
            {"applicationNumber": application_number},
        )

    def family_info(self, application_number: str) -> dict[str, Any]:
        """특허패밀리정보조회(국내출원번호) - getAppNoPatFamInfoSearch"""
        return self.request(
            "getAppNoPatFamInfoSearch",
            {"applicationNumber": application_number},
            service_path=PAT_FAMILY_SERVICE_PATH,
        )

    def citation_info_v3(self, application_number: str) -> dict[str, Any]:
        """인용문헌V3 - citationInfoV3.

        CitationService는 다른 KIPRISPlus API와 달리 인증 파라미터명이 accessKey입니다.
        """
        return self.request(
            "citationInfoV3",
            {"applicationNumber": application_number},
            service_path=CITATION_SERVICE_PATH,
            auth_param="accessKey",
        )

    def citing_info(self, standard_citation_application_number: str) -> dict[str, Any]:
        """피인용문헌 - citingInfo.

        CitingService는 대상 특허를 선행기술로 인용한 후행 출원번호를 반환합니다.
        """
        return self.request(
            "citingInfo",
            {"standardCitationApplicationNumber": standard_citation_application_number},
            service_path=CITING_SERVICE_PATH,
            auth_param="accessKey",
        )

    def family_patents(self, application_number: str) -> list[KiprisFamilyPatent]:
        """패밀리정보에서 국가코드와 등록번호만 정규화합니다."""
        raw = self.family_info(application_number)
        family_items = (
            _get_nested(raw, "response", "body", "items", "item")
            or _get_nested(raw, "response", "body", "item")
            or {}
        )
        families = []
        for family in _iter_mappings(family_items):
            publication_kind_code = str(family.get("publicationKindCode") or "")
            if publication_kind_code and not publication_kind_code.startswith("B"):
                continue
            families.append(family)
        return [
            KiprisFamilyPatent(
                country_code=_first_present(
                    family,
                    ("publicationCountryCode", "countryCode", "applicationCountryCode", "country"),
                ),
                registration_number=_first_present(
                    family,
                    ("publicationNumber", "registrationNumber", "registerNumber"),
                ),
                raw=dict(family),
            )
            for family in families
        ]

    def search_by_ipc(self, ipc_number: str, **params: Any) -> dict[str, Any]:
        """IPC 검색 - ipcSearchInfo.

        search_by_cpc와 동일하게 /openapi/rest/ 경로 + accessKey 인증을 써야 한다. 기본 경로
        (/kipo-api/kipi/ + ServiceKey)로는 INVALID_REQUEST_PARAMETER로 실패한다.
        """
        params.update({"ipcNumber": ipc_number})
        return self.request(
            "ipcSearchInfo",
            params,
            service_path=PAT_UTI_REST_SERVICE_PATH,
            auth_param="accessKey",
        )

    def count_foreign_patents_by_ipc_opendate(
        self,
        ipc_number: str,
        country_code: str,
        open_date_start: str,
        open_date_end: str,
    ) -> int:
        """해외특허 IPC + 공개일자(OPD) 범위로 해당 국가 공개 특허 건수를 센다.

        ForeignPatentAdvencedSearchService/advancedSearch는 `free` 쿼리에
        `IPC=[코드] AND OPD=[YYYYMMDD~YYYYMMDD]` 문법으로 IPC와 공개일자 범위를 동시에 걸 수
        있고, totalSearchCount로 구간 건수를 바로 돌려준다(ipcSearch는 날짜 필터가 없어 불가).
        IPC는 공백을 제거해야 한다(`G16H 50/70` → `G16H50/70`).
        """
        ipc = re.sub(r"\s+", "", str(ipc_number or ""))
        free_query = f"IPC=[{ipc}] AND OPD=[{open_date_start}~{open_date_end}]"
        raw = self.request(
            "advancedSearch",
            {"free": free_query, "collectionValues": country_code, "currentPage": 1},
            service_path=OVERSEAS_ADVANCED_SEARCH_SERVICE_PATH,
            auth_param="accessKey",
        )
        items = ((raw.get("response") or {}).get("body") or {}).get("items") or {}
        total = items.get("totalSearchCount") if isinstance(items, dict) else None
        try:
            return int(str(total).strip())
        except (TypeError, ValueError):
            return 0

    def search_by_cpc(self, cpc_number: str, **params: Any) -> dict[str, Any]:
        """CPC 검색 - cpcSearchInfo"""
        params.update({"cpcNumber": cpc_number})
        return self.request(
            "cpcSearchInfo",
            params,
            service_path=PAT_UTI_REST_SERVICE_PATH,
            auth_param="accessKey",
        )

    def search_by_applicant(self, applicant: str, **params: Any) -> dict[str, Any]:
        """출원인정보 검색 - applicantNameSearchInfo"""
        params.update({"applicant": applicant})
        return self.request("applicantNameSearchInfo", params)

    def search_by_right_holder(self, right_holder: str, **params: Any) -> dict[str, Any]:
        """등록권자정보 검색 - rightHolerSearchInfo"""
        params.update({"rightHoler": right_holder})
        return self.request("rightHolerSearchInfo", params)

    # -------------------------
    # 도면/전문
    # -------------------------
    def _fulltext_pdf_path(self, operation_name: str, application_number: str) -> KiprisDocumentPath:
        raw = self.request(
            operation_name,
            {"applicationNumber": application_number},
        )
        item = _get_nested(raw, "response", "body", "item") or {}
        if isinstance(item, list):
            item = item[0] if item else {}
        doc_name = item.get("docName") if isinstance(item, Mapping) else None
        path = item.get("path") if isinstance(item, Mapping) else None
        return KiprisDocumentPath(doc_name=doc_name, path=path, raw=raw)  # type: ignore[arg-type]

    def publication_fulltext_pdf_path(self, application_number: str) -> KiprisDocumentPath:
        """공개전문PDF - getPubFullTextInfoSearch

        응답의 body.item.path가 PDF 다운로드 URL입니다.
        """
        return self._fulltext_pdf_path("getPubFullTextInfoSearch", application_number)

    def announcement_fulltext_pdf_path(self, application_number: str) -> KiprisDocumentPath:
        """공고전문PDF - getAnnFullTextInfoSearch"""
        return self._fulltext_pdf_path("getAnnFullTextInfoSearch", application_number)

    def transfer_list_info(
        self,
        *,
        kind: str,
        search_right: str,
        transfer_date: str,
    ) -> dict[str, Any]:
        """특허·실용 행정처리 이력 / 변동정보 - transferListInfo"""
        return self.request(
            "transferListInfo",
            {
                "kind": kind,
                "searchRight": search_right,
                "transferDate": transfer_date,
            },
            service_path=PAT_UTI_TRANSFER_HIST_PATH,
        )

    def overseas_demand_paragraph(
        self,
        literature_number: str,
        country_code: str,
    ) -> dict[str, Any]:
        """해외특허 / 서지정보 / 청구항 - demandParagraphInfo"""
        return self.request(
            "demandParagraphInfo",
            {
                "literatureNumber": literature_number,
                "countryCode": country_code,
            },
            service_path=OVERSEAS_PATENT_SERVICE_PATH,
            auth_param="accessKey",
        )

    def overseas_bibliographic_info(
        self,
        literature_number: str,
        country_code: str,
    ) -> dict[str, Any]:
        """해외특허 / 서지정보 / 서지상세 - bibliographicInfo"""
        return self.request(
            "bibliographicInfo",
            {
                "literatureNumber": literature_number,
                "countryCode": country_code,
            },
            service_path=OVERSEAS_PATENT_SERVICE_PATH,
            auth_param="accessKey",
        )

    def overseas_us_patent_documents(
        self,
        literature_number: str,
        country_code: str,
    ) -> dict[str, Any]:
        """해외특허 / 인용(자국 문헌) - usPatentDocumentsInfo"""
        return self.request(
            "usPatentDocumentsInfo",
            {
                "literatureNumber": literature_number,
                "countryCode": country_code,
            },
            service_path=OVERSEAS_PATENT_SERVICE_PATH,
            auth_param="accessKey",
        )

    def overseas_foreign_patent_documents(
        self,
        literature_number: str,
        country_code: str,
    ) -> dict[str, Any]:
        """해외특허 / 인용(타국 문헌) - foreignPatentDocumentsInfo"""
        return self.request(
            "foreignPatentDocumentsInfo",
            {
                "literatureNumber": literature_number,
                "countryCode": country_code,
            },
            service_path=OVERSEAS_PATENT_SERVICE_PATH,
            auth_param="accessKey",
        )

    def overseas_open_fulltext(
        self,
        literature_number: str,
        country_code: str,
    ) -> dict[str, Any]:
        """해외특허 / 도면·전문 / 공개전문 - openFullTextInfo"""
        return self.request(
            "openFullTextInfo",
            {
                "literatureNumber": literature_number,
                "countryCode": country_code,
            },
            service_path=OVERSEAS_IMAGE_FULLTEXT_SERVICE_PATH,
            auth_param="accessKey",
        )

    def overseas_registration_fulltext(
        self,
        literature_number: str,
        country_code: str,
    ) -> dict[str, Any]:
        """해외특허 / 도면·전문 / 공고전문 - registrationFullTextInfo"""
        return self.request(
            "registrationFullTextInfo",
            {
                "literatureNumber": literature_number,
                "countryCode": country_code,
            },
            service_path=OVERSEAS_IMAGE_FULLTEXT_SERVICE_PATH,
            auth_param="accessKey",
        )

    def download_publication_fulltext_pdf(
        self,
        application_number: str,
        *,
        output_dir: str | Path = ".",
        filename: str | None = None,
    ) -> Path:
        """공개전문 PDF 경로를 조회한 뒤 실제 PDF 파일을 저장합니다."""
        doc = self.publication_fulltext_pdf_path(application_number)
        if not doc.path:
            raise KiprisError("PDF 다운로드 경로(path)를 찾지 못했습니다.")

        response = self.session.get(doc.path, timeout=self.timeout)
        response.raise_for_status()

        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        safe_name = filename or doc.doc_name or f"{application_number}.pdf"
        safe_name = re.sub(r"[^0-9A-Za-z가-힣_.-]+", "_", safe_name)
        file_path = output / safe_name
        file_path.write_bytes(response.content)
        return file_path


def build_evaluation_snapshot(client: KiprisClient, application_number: str) -> dict[str, Any]:
    """평가 엔진에 넘기기 쉬운 KIPRIS 기반 스냅샷을 만듭니다.

    Wrapper API가 아니라, 로컬 코드에서 여러 KIPRIS 원천 API를 직접 호출해 묶는 함수입니다.
    """
    detail = client.bibliography_detail(application_number)
    family_patents = client.family_patents(application_number)
    pdf = client.publication_fulltext_pdf_path(application_number)
    return {
        "source": "KIPRISPlus",
        "applicationNumber": application_number,
        "bibliographyDetail": detail,
        "familyPatents": [
            {
                "countryCode": family.country_code,
                "registrationNumber": family.registration_number,
            }
            for family in family_patents
        ],
        "documents": {
            "publicationFullTextPdf": {
                "docName": pdf.doc_name,
                "path": pdf.path,
            }
        },
        "externalData": {},
    }
