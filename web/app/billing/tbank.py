"""Клиент интернет-эквайринга Т-Банка (Т-Касса).

Документация: https://developer.tbank.ru/eacq/
API base: https://securepay.tinkoff.ru/v2/
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger("dok.tbank")

DEFAULT_API_BASE = "https://securepay.tinkoff.ru/v2"


def token_value(value: Any) -> str:
    """Приведение значения корневого поля к строке для Token (как в доке Т-Банка)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def build_token(params: dict[str, Any], password: str) -> str:
    """SHA-256 Token: корневые поля + Password, сортировка ключей, конкатенация значений.

    Вложенные dict/list и сам Token в подпись не входят.
    Эталон Init из документации:
    Amount=19200, Description=…, OrderId=00000, Password=11111111111111,
    TerminalKey=MerchantTerminalKey
    → 72dd466f8ace0a37a1f740ce5fb78101712bc0665d91a8108c7c8a0ccd426db2
    """
    pairs: dict[str, str] = {}
    for key, value in params.items():
        if key == "Token":
            continue
        if isinstance(value, (dict, list)):
            continue
        if value is None:
            continue
        pairs[key] = token_value(value)
    pairs["Password"] = password
    concatenated = "".join(pairs[k] for k in sorted(pairs))
    return hashlib.sha256(concatenated.encode("utf-8")).hexdigest()


def verify_token(params: dict[str, Any], password: str) -> bool:
    received = params.get("Token")
    if not received or not isinstance(received, str):
        return False
    return build_token(params, password) == received


@dataclass
class TBankConfig:
    terminal_key: str
    password: str
    api_base: str = DEFAULT_API_BASE
    timeout: float = 20.0


class TBankError(Exception):
    def __init__(self, message: str, *, code: str | None = None, payload: dict | None = None):
        super().__init__(message)
        self.code = code
        self.payload = payload or {}


class TBankClient:
    def __init__(self, config: TBankConfig, client: httpx.Client | None = None) -> None:
        self.config = config
        self._client = client
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self.config.timeout)
        return self._client

    def _call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = {
            "TerminalKey": self.config.terminal_key,
            **payload,
        }
        body["Token"] = build_token(body, self.config.password)
        url = f"{self.config.api_base.rstrip('/')}/{method}"
        # Не логируем Token/Password и полный body
        log.info("T-Bank %s OrderId=%s", method, body.get("OrderId") or body.get("PaymentId"))
        resp = self._http().post(url, json=body)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("Success"):
            raise TBankError(
                str(data.get("Message") or data.get("Details") or "Ошибка Т-Кассы"),
                code=str(data.get("ErrorCode") or ""),
                payload=data,
            )
        return data

    def init(
        self,
        *,
        amount_kop: int,
        order_id: str,
        description: str,
        notification_url: str,
        success_url: str,
        fail_url: str,
        customer_key: str | None = None,
        recurrent: bool = False,
        email: str | None = None,
        receipt: dict[str, Any] | None = None,
        data: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "Amount": int(amount_kop),
            "OrderId": order_id,
            "Description": description[:250],
            "NotificationURL": notification_url,
            "SuccessURL": success_url,
            "FailURL": fail_url,
            "PayType": "O",  # одностадийный
        }
        if recurrent and customer_key:
            payload["Recurrent"] = "Y"
            payload["CustomerKey"] = customer_key
        elif customer_key:
            payload["CustomerKey"] = customer_key
        extra = dict(data or {})
        if email:
            extra.setdefault("Email", email)
        if extra:
            payload["DATA"] = extra
        if receipt:
            payload["Receipt"] = receipt
        return self._call("Init", payload)

    def get_state(self, payment_id: str) -> dict[str, Any]:
        return self._call("GetState", {"PaymentId": str(payment_id)})

    def charge(self, *, payment_id: str, rebill_id: str) -> dict[str, Any]:
        return self._call(
            "Charge",
            {"PaymentId": str(payment_id), "RebillId": str(rebill_id)},
        )

    def cancel(self, payment_id: str, amount_kop: int | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"PaymentId": str(payment_id)}
        if amount_kop is not None:
            payload["Amount"] = int(amount_kop)
        return self._call("Cancel", payload)


def build_subscription_receipt(
    *,
    email: str,
    taxation: str,
    amount_kop: int,
    description: str,
    vat: str = "none",
) -> dict[str, Any]:
    """Receipt для 54-ФЗ: одна услуга, полная предоплата."""
    return {
        "Email": email,
        "Taxation": taxation,
        "Items": [
            {
                "Name": description[:128],
                "Price": int(amount_kop),
                "Quantity": 1.0,
                "Amount": int(amount_kop),
                "Tax": vat if vat != "none" else "none",
                "PaymentMethod": "full_prepayment",
                "PaymentObject": "service",
            }
        ],
    }
