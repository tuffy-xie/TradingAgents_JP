from __future__ import annotations

import asyncio

import requests

from tradingagents.dataflows.japan.edinet_db import (
    EDINETDBProvider,
    EDINETDBStatus,
    IssuerIdentityStatus,
    normalize_security_code,
)


class _Response:
    def __init__(self, status_code: int, payload=None, *, json_error: Exception | None = None):
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._payload


def _fetch(provider: EDINETDBProvider, issuer: str = "E01081"):
    return asyncio.run(provider.fetch_earnings(issuer))


def _resolve(provider: EDINETDBProvider, ticker: object = "5016.T"):
    return asyncio.run(provider.resolve_issuer_identity(ticker))


def _company(
    sec_code: str,
    edinet_code: str,
    *,
    listing_status: str | None = "listed",
    is_delisted: bool | None = False,
):
    record = {
        "sec_code": sec_code,
        "edinet_code": edinet_code,
    }
    if listing_status is not None:
        record["listing_status"] = listing_status
    if is_delisted is not None:
        record["is_delisted"] = is_delisted
    return record


def _company_response(*records: dict):
    return _Response(
        200,
        {
            "data": list(records),
            "meta": {"data_as_of": "2026-08-25"},
        },
    )


def test_fetch_earnings_returns_raw_records_and_source_metadata(monkeypatch):
    captured = {}

    def fake_get(url, *, params, headers, timeout):
        captured.update(url=url, params=params, headers=headers, timeout=timeout)
        return _Response(
            200,
            {
                "data": [
                    {
                        "id": "earnings-1",
                        "pdf_url": "https://example.test/earnings-1.pdf",
                        "revenue": 260604,
                    }
                ],
                "meta": {"total": 1},
            },
        )

    monkeypatch.setenv("EDINETDB_API_KEY", "test-edinetdb-key")
    result = _fetch(EDINETDBProvider(http_get=fake_get))

    assert result.status == EDINETDBStatus.OK
    assert result.records == (
        {
            "id": "earnings-1",
            "pdf_url": "https://example.test/earnings-1.pdf",
            "revenue": 260604,
        },
    )
    assert result.raw_response["meta"] == {"total": 1}
    assert result.metadata["source"] == "EDINET DB"
    assert result.metadata["issuer_identifier"] == "E01081"
    assert result.metadata["fetched_at"]
    assert result.metadata["record_metadata"] == (
        {
            "source_record_id": "earnings-1",
            "source_url": "https://example.test/earnings-1.pdf",
        },
    )
    assert captured["url"] == "https://edinetdb.jp/v1/companies/E01081/earnings"
    assert captured["params"] == {"limit": 8}
    assert captured["headers"]["X-API-Key"] == "test-edinetdb-key"
    assert captured["timeout"] == 10.0


def test_fetch_earnings_without_api_key_requires_auth(monkeypatch):
    monkeypatch.delenv("EDINETDB_API_KEY", raising=False)
    result = _fetch(EDINETDBProvider())
    assert result.status == EDINETDBStatus.AUTH_REQUIRED
    assert result.records == ()
    assert result.detail == "EDINETDB_API_KEY is not configured"


def test_fetch_earnings_maps_401_and_403_to_auth_required():
    for status_code in (401, 403):
        result = _fetch(
            EDINETDBProvider(
                api_key="test-key",
                http_get=lambda _url, response_status=status_code, **_: _Response(response_status),
            )
        )
        assert result.status == EDINETDBStatus.AUTH_REQUIRED
        assert result.detail == f"HTTP {status_code}"


def test_fetch_earnings_maps_429_to_rate_limited():
    result = _fetch(
        EDINETDBProvider(api_key="test-key", http_get=lambda _url, **_: _Response(429))
    )
    assert result.status == EDINETDBStatus.RATE_LIMITED


def test_fetch_earnings_maps_timeout_to_timeout():
    def timeout(_url, **_):
        raise requests.Timeout("slow response")

    result = _fetch(EDINETDBProvider(api_key="test-key", http_get=timeout))
    assert result.status == EDINETDBStatus.TIMEOUT
    assert result.detail == "Timeout"


def test_fetch_earnings_maps_500_to_api_error():
    result = _fetch(
        EDINETDBProvider(api_key="test-key", http_get=lambda _url, **_: _Response(500))
    )
    assert result.status == EDINETDBStatus.API_ERROR
    assert result.detail == "HTTP 500"


def test_fetch_earnings_maps_malformed_response_to_data_unavailable():
    result = _fetch(
        EDINETDBProvider(
            api_key="test-key",
            http_get=lambda _url, **_: _Response(200, {"unexpected": []}),
        )
    )
    assert result.status == EDINETDBStatus.DATA_UNAVAILABLE
    assert result.detail == "earnings response has no list data field"


def test_fetch_earnings_maps_invalid_json_to_data_unavailable():
    result = _fetch(
        EDINETDBProvider(
            api_key="test-key",
            http_get=lambda _url, **_: _Response(200, json_error=ValueError("bad JSON")),
        )
    )
    assert result.status == EDINETDBStatus.DATA_UNAVAILABLE
    assert result.detail == "invalid JSON response"


def test_normalize_security_code_is_deterministic():
    assert normalize_security_code(5016) == "5016"
    assert normalize_security_code("5016") == "5016"
    assert normalize_security_code("5016.T") == "5016"
    assert normalize_security_code(" 5016.T ") == "5016"
    assert normalize_security_code(50160) == "5016"
    assert normalize_security_code("50160") == "5016"
    assert normalize_security_code("607A") == "607A"
    assert normalize_security_code("607a.t") == "607A"
    assert normalize_security_code("607A0") == "607A"
    assert normalize_security_code("NOT-A-CODE") is None


def test_resolve_identity_uses_exact_company_master_query():
    captured = {}

    def fake_get(url, *, params, headers, timeout):
        captured.update(url=url, params=params, headers=headers, timeout=timeout)
        return _company_response(_company("50160", "E01081"))

    result = _resolve(EDINETDBProvider(api_key="test-key", http_get=fake_get))

    assert result.status == IssuerIdentityStatus.OK
    assert result.input_ticker == "5016.T"
    assert result.security_code == "5016"
    assert result.source_security_code == "50160"
    assert result.edinet_code == "E01081"
    assert result.listing_status == "listed"
    assert result.source == "EDINET_DB_COMPANY_MASTER"
    assert result.source_as_of == "2026-08-25"
    assert result.fetched_at.endswith("+00:00")
    assert result.detail == ""
    assert captured["url"] == "https://edinetdb.jp/v1/companies"
    assert captured["params"] == {"sec_code": "5016", "per_page": 2}
    assert captured["headers"]["X-API-Key"] == "test-key"
    assert captured["timeout"] == 10.0
    assert "/earnings" not in captured["url"]


def test_resolve_identity_accepts_five_character_source_code_input():
    provider = EDINETDBProvider(
        api_key="test-key",
        http_get=lambda _url, **_: _company_response(_company("50160", "E01081")),
    )

    result = _resolve(provider, 50160)

    assert result.status == IssuerIdentityStatus.OK
    assert result.security_code == "5016"
    assert result.edinet_code == "E01081"


def test_resolve_identity_for_other_verified_numeric_codes():
    cases = {
        "6324.T": ("63240", "E01712"),
        "6981.T": ("69810", "E01914"),
    }
    for ticker, (source_code, edinet_code) in cases.items():
        provider = EDINETDBProvider(
            api_key="test-key",
            http_get=lambda _url, source=source_code, edinet=edinet_code, **_: (
                _company_response(_company(source, edinet))
            ),
        )
        result = _resolve(provider, ticker)
        assert result.status == IssuerIdentityStatus.OK
        assert result.source_security_code == source_code
        assert result.edinet_code == edinet_code


def test_resolve_identity_accepts_alphanumeric_security_code():
    captured = {}

    def fake_get(url, *, params, **_):
        captured.update(url=url, params=params)
        return _company_response(_company("607A0", "E12345"))

    result = _resolve(
        EDINETDBProvider(api_key="test-key", http_get=fake_get),
        "607A.T",
    )

    assert result.status == IssuerIdentityStatus.OK
    assert result.security_code == "607A"
    assert result.source_security_code == "607A0"
    assert result.edinet_code == "E12345"
    assert captured["params"] == {"sec_code": "607A", "per_page": 2}


def test_invalid_local_security_code_does_not_make_http_request():
    calls = []

    def fake_get(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("HTTP must not be called")

    result = _resolve(
        EDINETDBProvider(api_key="test-key", http_get=fake_get),
        "NOT-A-CODE",
    )

    assert result.status == IssuerIdentityStatus.DATA_UNAVAILABLE
    assert result.security_code is None
    assert result.edinet_code is None
    assert result.detail == "INVALID_SECURITY_CODE"
    assert calls == []


def test_empty_exact_company_result_is_data_unavailable():
    result = _resolve(
        EDINETDBProvider(
            api_key="test-key",
            http_get=lambda _url, **_: _company_response(),
        ),
        "0000",
    )

    assert result.status == IssuerIdentityStatus.DATA_UNAVAILABLE
    assert result.security_code == "0000"
    assert result.edinet_code is None
    assert result.detail == "NO_EXACT_MATCH"


def test_multiple_exact_candidates_are_rejected():
    result = _resolve(
        EDINETDBProvider(
            api_key="test-key",
            http_get=lambda _url, **_: _company_response(
                _company("50160", "E01081"),
                _company("50160", "E99999"),
            ),
        )
    )

    assert result.status == IssuerIdentityStatus.DATA_UNAVAILABLE
    assert result.edinet_code is None
    assert result.detail == "AMBIGUOUS_IDENTITY"


def test_nonmatching_response_security_code_is_rejected():
    result = _resolve(
        EDINETDBProvider(
            api_key="test-key",
            http_get=lambda _url, **_: _company_response(_company("50170", "E01081")),
        )
    )

    assert result.status == IssuerIdentityStatus.DATA_UNAVAILABLE
    assert result.edinet_code is None
    assert result.detail == "NO_EXACT_MATCH"


def test_invalid_edinet_code_is_rejected():
    result = _resolve(
        EDINETDBProvider(
            api_key="test-key",
            http_get=lambda _url, **_: _company_response(_company("50160", "5016")),
        )
    )

    assert result.status == IssuerIdentityStatus.DATA_UNAVAILABLE
    assert result.source_security_code == "50160"
    assert result.edinet_code is None
    assert result.detail == "INVALID_EDINET_CODE"


def test_delisted_or_nonlisted_issuer_is_rejected():
    cases = (
        _company("50160", "E01081", is_delisted=True),
        _company("50160", "E01081", listing_status="delisted", is_delisted=False),
    )
    for company in cases:
        result = _resolve(
            EDINETDBProvider(
                api_key="test-key",
                http_get=lambda _url, record=company, **_: _company_response(record),
            )
        )
        assert result.status == IssuerIdentityStatus.DATA_UNAVAILABLE
        assert result.edinet_code is None
        assert result.detail == "ISSUER_NOT_CURRENTLY_LISTED"


def test_missing_listing_metadata_is_rejected():
    cases = (
        _company("50160", "E01081", listing_status=None),
        _company("50160", "E01081", is_delisted=None),
    )
    for company in cases:
        result = _resolve(
            EDINETDBProvider(
                api_key="test-key",
                http_get=lambda _url, record=company, **_: _company_response(record),
            )
        )
        assert result.status == IssuerIdentityStatus.DATA_UNAVAILABLE
        assert result.edinet_code is None
        assert result.detail == "ISSUER_NOT_CURRENTLY_LISTED"


def test_resolve_identity_without_api_key_is_data_unavailable(monkeypatch):
    calls = []
    monkeypatch.delenv("EDINETDB_API_KEY", raising=False)

    result = _resolve(EDINETDBProvider(http_get=lambda *args, **kwargs: calls.append((args, kwargs))))

    assert result.status == IssuerIdentityStatus.DATA_UNAVAILABLE
    assert result.edinet_code is None
    assert result.detail == "AUTH_REQUIRED"
    assert calls == []


def test_resolve_identity_maps_http_failures_to_unavailable():
    cases = {
        400: "DATA_UNAVAILABLE",
        401: "AUTH_REQUIRED",
        403: "AUTH_REQUIRED",
        429: "RATE_LIMITED",
        500: "API_ERROR",
    }
    for status_code, detail in cases.items():
        result = _resolve(
            EDINETDBProvider(
                api_key="test-key",
                http_get=lambda _url, response_status=status_code, **_: _Response(
                    response_status
                ),
            )
        )
        assert result.status == IssuerIdentityStatus.DATA_UNAVAILABLE
        assert result.edinet_code is None
        assert result.detail == detail


def test_resolve_identity_maps_timeout_and_connection_error_to_unavailable():
    errors = (
        (requests.Timeout("slow response"), "TIMEOUT"),
        (requests.ConnectionError("offline"), "API_ERROR"),
    )
    for error, detail in errors:
        def fail(_url, exception=error, **_):
            raise exception

        result = _resolve(EDINETDBProvider(api_key="test-key", http_get=fail))
        assert result.status == IssuerIdentityStatus.DATA_UNAVAILABLE
        assert result.edinet_code is None
        assert result.detail == detail


def test_resolve_identity_rejects_invalid_json_and_payload_shape():
    responses = (
        _Response(200, json_error=ValueError("bad JSON")),
        _Response(200, {"unexpected": []}),
        _Response(200, {"data": ["not-a-company"]}),
    )
    for response in responses:
        result = _resolve(
            EDINETDBProvider(
                api_key="test-key",
                http_get=lambda _url, item=response, **_: item,
            )
        )
        assert result.status == IssuerIdentityStatus.DATA_UNAVAILABLE
        assert result.edinet_code is None
        assert result.detail == "INVALID_RESPONSE"
