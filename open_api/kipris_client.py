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
import xml.etree.ElementTree as ET

import requests


DEFAULT_BASE_URL = "http://plus.kipris.or.kr"
PAT_UTI_SERVICE_PATH = "/kipo-api/kipi/patUtiModInfoSearchSevice"
PAT_UTI_REST_SERVICE_PATH = "/openapi/rest/patUtiModInfoSearchSevice"
PAT_UTI_TRANSFER_HIST_PATH = "/kipo-api/kipi/patUtiModTransferHistInfoSearchSevice"
OVERSEAS_PATENT_SERVICE_PATH = "/openapi/rest/ForeignPatentBibliographicService"
PAT_FAMILY_SERVICE_PATH = "/kipo-api/kipi/patFamInfoSearchService"
CITATION_SERVICE_PATH = "/openapi/rest/CitationService"
CITING_SERVICE_PATH = "/openapi/rest/CitingService"


class KiprisError(RuntimeError):
    """KIPRISPlus 호출 또는 응답 파싱 오류."""


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
        self.service_key = (
            service_key
            or os.getenv("KIPRIS_SERVICE_KEY")
            or os.getenv("KIPRIS_API_KEY")
            or os.getenv("SERVICE_KEY")
        )
        if not self.service_key:
            raise ValueError("service_key 또는 환경변수 KIPRIS_SERVICE_KEY가 필요합니다.")
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

        query = {auth_param: self.service_key}
        if params:
            for k, v in params.items():
                if v is None:
                    continue
                if isinstance(v, bool):
                    query[k] = str(v).lower()
                else:
                    query[k] = v

        url = self._url(service_path, operation_name)
        response = self.session.get(url, params=query, timeout=self.timeout)
        response.raise_for_status()

        text = response.text.strip()
        if not parse_xml:
            return text
        return parse_xml_response(text)

    # -------------------------
    # 특허·실용 공개·등록공보
    # -------------------------
    def advanced_search(self, **params: Any) -> dict[str, Any]:
        """전체검색 - getAdvancedSearch"""
        return self.request("getAdvancedSearch", params)

    def search_by_application_number(self, application_number: str, **params: Any) -> dict[str, Any]:
        """출원번호 검색 - applicationNumberSearchInfo"""
        params.update({"applicationNumber": application_number})
        return self.request("applicationNumberSearchInfo", params)

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
        """IPC 검색 - ipcSearchInfo"""
        params.update({"ipcNumber": ipc_number})
        return self.request("ipcSearchInfo", params)

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
