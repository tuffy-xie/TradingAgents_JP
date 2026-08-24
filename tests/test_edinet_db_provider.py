from __future__ import annotations

import asyncio

import requests

from tradingagents.dataflows.japan.edinet_db import EDINETDBProvider, EDINETDBStatus


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
