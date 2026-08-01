"""Клиент открытого API publication.pravo.gov.ru (W-17)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import urlencode

from app.services.sources.http_client import ThrottledClient

DEFAULT_BASE = os.environ.get(
    "PRAVO_PUBLICATION_BASE", "http://publication.pravo.gov.ru"
).rstrip("/")

PAGE_SIZES = (10, 30, 100, 200)
FEDERAL_LAW_TYPE_ID = "82a8bf1c-3bc7-47ed-827f-7affd43a7f27"


@dataclass
class PublicationDocument:
    eo_number: str
    name: str
    number: str
    complex_name: str
    document_date: str | None
    publish_date: str | None
    view_date: str | None
    pdf_file_length: int | None
    document_type_id: str | None
    raw: dict[str, Any]

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> PublicationDocument:
        return cls(
            eo_number=str(item.get("eoNumber") or ""),
            name=str(item.get("name") or ""),
            number=str(item.get("number") or ""),
            complex_name=str(item.get("complexName") or ""),
            document_date=item.get("documentDate"),
            publish_date=item.get("publishDateShort"),
            view_date=item.get("viewDate"),
            pdf_file_length=item.get("pdfFileLength"),
            document_type_id=item.get("documentTypeId"),
            raw=item,
        )


class PublicationClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        http: ThrottledClient | None = None,
    ) -> None:
        self.base_url = (base_url or DEFAULT_BASE).rstrip("/")
        self.http = http or ThrottledClient()
        self._owns_http = http is None

    def close(self) -> None:
        if self._owns_http:
            self.http.close()

    def __enter__(self) -> PublicationClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _url(self, path: str, params: dict[str, Any] | None = None) -> str:
        url = f"{self.base_url}{path}"
        if params:
            clean = {k: v for k, v in params.items() if v is not None and v != ""}
            if clean:
                url = f"{url}?{urlencode(clean, doseq=True)}"
        return url

    def document_types(self) -> list[dict]:
        r = self.http.get(self._url("/api/DocumentTypes"))
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []

    def search_documents(
        self,
        *,
        name: str | None = None,
        number: str | None = None,
        number_search_type: int = 3,
        eo_number: str | None = None,
        period_type: str | None = None,
        publish_date_from: date | None = None,
        publish_date_to: date | None = None,
        document_type_id: str | None = None,
        page_size: int = 10,
        index: int = 1,
        sorted_by: int = 4,
        sort_destination: str = "desc",
    ) -> dict[str, Any]:
        if page_size not in PAGE_SIZES:
            raise ValueError(f"PageSize должен быть одним из {PAGE_SIZES}")
        params: dict[str, Any] = {
            "PageSize": page_size,
            "Index": index,
            "SortedBy": sorted_by,
            "SortDestination": sort_destination,
        }
        if name:
            params["Name"] = name
        if number:
            params["Number"] = number
            params["NumberSearchType"] = number_search_type
        if eo_number:
            params["EoNumber"] = eo_number
        if period_type:
            params["PeriodType"] = period_type
        if publish_date_from:
            params["PublishDateFrom"] = publish_date_from.isoformat()
        if publish_date_to:
            params["PublishDateTo"] = publish_date_to.isoformat()
        if document_type_id:
            params["DocumentTypeId"] = document_type_id
        r = self.http.get(self._url("/api/Documents", params))
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            raise RuntimeError("Неожиданный ответ /api/Documents")
        return data

    def list_documents(self, **kwargs: Any) -> list[PublicationDocument]:
        data = self.search_documents(**kwargs)
        items = data.get("items") or []
        return [PublicationDocument.from_item(i) for i in items if isinstance(i, dict)]

    def get_document(self, eo_number: str) -> dict[str, Any]:
        r = self.http.get(self._url("/api/Document", {"eoNumber": eo_number}))
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            raise RuntimeError("Неожиданный ответ /api/Document")
        return data

    def pdf_url(self, eo_number: str) -> str:
        return self._url("/file/pdf", {"eoNumber": eo_number})

    def download_pdf(self, eo_number: str) -> bytes:
        r = self.http.get(self.pdf_url(eo_number), use_cache=False)
        r.raise_for_status()
        if not r.content.startswith(b"%PDF"):
            raise RuntimeError("Ответ не похож на PDF")
        return r.content

    def find_changes_mentioning(
        self,
        *,
        watch_name: str,
        since: date | None = None,
        page_size: int = 10,
    ) -> list[PublicationDocument]:
        """Документы, в названии которых фигурирует отслеживаемый акт."""
        docs = self.list_documents(
            name=watch_name,
            publish_date_from=since,
            page_size=page_size,
            sorted_by=4,
            sort_destination="desc",
        )
        needle = watch_name.casefold()
        return [
            d
            for d in docs
            if needle in (d.name or "").casefold()
            or needle in (d.complex_name or "").casefold()
        ]
