from __future__ import annotations

import asyncio
import inspect
from dataclasses import replace
from datetime import UTC, date, datetime
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage

from tradingagents.dataflows.japan.cache import JapanDataCache
from tradingagents.dataflows.japan.context import (
    collect_japan_data_bundle,
    render_japan_agent_context,
    render_japan_financial_context,
)
from tradingagents.dataflows.japan.edinet_db import (
    EDINETDBProvider,
    EDINETDBResponse,
    EDINETDBStatus,
    IssuerIdentity,
    IssuerIdentityStatus,
)
from tradingagents.dataflows.japan.edinet_db_bundle import EDINETDBFinancialBundleProvider
from tradingagents.dataflows.japan.edinet_db_guidance_selector import LatestGuidanceSelection
from tradingagents.dataflows.japan.edinet_db_normalizer import normalize_financial_document
from tradingagents.dataflows.japan.edinet_db_selector import LatestEarningsSelection
from tradingagents.dataflows.japan.edinet_db_service import EDINETDBFinancialSnapshot
from tradingagents.dataflows.japan.models import (
    DataStatus,
    InformationLayer,
    MarketInformation,
    ProviderResponse,
    SourceStatus,
)
from tradingagents.dataflows.japan.official import build_official_japan_providers
from tradingagents.dataflows.japan.service import JapanDataService
from tradingagents.dataflows.market import resolve_market_context
from tradingagents.graph.propagation import Propagator


def _identity(status=IssuerIdentityStatus.OK, *, detail="") -> IssuerIdentity:
    return IssuerIdentity(
        input_ticker="5016",
        security_code="5016",
        source_security_code="50160",
        edinet_code="E01081" if status == IssuerIdentityStatus.OK else None,
        listing_status="listed" if status == IssuerIdentityStatus.OK else None,
        source="EDINET_DB_COMPANY_MASTER",
        source_as_of="2026-08-15",
        fetched_at="2026-08-15T00:00:00+00:00",
        status=status,
        detail=detail,
    )


def _financial_document(*, quarter=1, fiscal_year_end="2027-03-31"):
    return normalize_financial_document(
        {
            "record_id": "earnings-5016-q1",
            "title": "2027年3月期 第1四半期決算",
            "disclosure_date": "2026-08-06",
            "pdf_url": "https://example.test/5016-q1.pdf",
            "source_as_of": "2026-08-15",
            "accounting_standard": "IFRS",
            "is_consolidated": True,
            "quarter": quarter,
            "fiscal_year_end": fiscal_year_end,
            "currency": "JPY",
            "unit": "百万円",
            "revenue": 260604,
            "operating_income": 81446,
            "ordinary_income": 79644,
            "net_income": 53070,
            "profit_ifrs": 60418,
            "eps": 56.80,
            "forecast_revenue": 1025000,
            "forecast_operating_income": 232000,
            "forecast_net_income": 141000,
            "forecast_eps": 155.80,
        },
        fetched_at="2026-08-15T00:00:00+00:00",
    )


class _IdentityProvider:
    def __init__(self, identity: IssuerIdentity):
        self.identity = identity
        self.calls: list[str] = []

    async def resolve_issuer_identity(self, security_code):
        self.calls.append(security_code)
        return self.identity


class _FinancialService:
    def __init__(self, result):
        self.result = result
        self.calls: list[tuple[str, date]] = []

    async def fetch_financial_snapshot(self, issuer_id, *, analysis_as_of):
        self.calls.append((issuer_id, analysis_as_of))
        if isinstance(self.result, EDINETDBResponse):
            return self.result
        actual = next(
            (record for record in self.result.records if record.record_type == "ACTUAL"),
            None,
        )
        guidance = next(
            (record for record in self.result.records if record.record_type == "GUIDANCE"),
            None,
        )
        actual_document = replace(self.result, records=(actual,)) if actual else None
        guidance_document = replace(self.result, records=(guidance,)) if guidance else None
        actual_selection = LatestEarningsSelection(
            "OK", {}, issuer_id, self.result.source_record_id,
            actual.target_period_end if actual else None,
            actual.period_type if actual else None,
            str(self.result.disclosure_timestamp)[:10], "DATE",
            "LATEST_FISCAL_PERIOD_AS_OF", "",
        )
        guidance_selection = LatestGuidanceSelection(
            "OK" if guidance else "NOT_PROVIDED", {}, issuer_id,
            self.result.source_record_id, guidance.target_period_end if guidance else None,
            guidance.period_type if guidance else None,
            str(self.result.disclosure_timestamp)[:10], "DATE",
            "LATEST_GUIDANCE_DISCLOSURE_AS_OF", "" if guidance else "GUIDANCE_NOT_PROVIDED",
        )
        return EDINETDBFinancialSnapshot(
            actual_document, guidance_document, actual_selection, guidance_selection,
            "OK" if actual else "DATA_UNAVAILABLE",
            "OK" if guidance else "NOT_PROVIDED", "", "", {},
        )


class _LegacyProvider:
    name = "TDnet"
    category = "news"

    async def fetch(self, context, *, start_date, end_date):
        del start_date, end_date
        item = MarketInformation(
            source="TDnet",
            source_type="earnings_or_quarterly",
            ticker=context.symbol,
            timestamp=datetime(2026, 8, 6, tzinfo=UTC),
            title="Official quarterly disclosure",
            verified=True,
            layer=InformationLayer.VERIFIED_FACT,
        )
        return ProviderResponse(
            SourceStatus(self.name, DataStatus.OK, item_count=1),
            (item,),
        )


class _HTTPResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload


def _adapter(identity=None, result=None):
    identity_provider = _IdentityProvider(identity or _identity())
    financial_service = _FinancialService(result or _financial_document())
    provider = EDINETDBFinancialBundleProvider(
        identity_provider,  # type: ignore[arg-type]
        financial_service,  # type: ignore[arg-type]
    )
    return provider, identity_provider, financial_service


def _state(bundle: dict, *, ticker="5016.T") -> dict:
    return Propagator().create_initial_state(ticker, "2026-08-15", japan_data_bundle=bundle)


def test_jp_trade_date_flows_through_identity_service_and_bundle(monkeypatch, tmp_path):
    adapter, identity_provider, financial_service = _adapter()
    data_service = JapanDataService(
        (_LegacyProvider(), adapter),
        cache=JapanDataCache(tmp_path),
        timeout_seconds=1,
    )
    monkeypatch.setattr(
        "tradingagents.dataflows.japan.context.build_official_japan_providers",
        lambda: (_LegacyProvider(), adapter),
    )
    monkeypatch.setattr(
        "tradingagents.dataflows.japan.context.JapanDataService",
        lambda _providers: data_service,
    )

    bundle = collect_japan_data_bundle(resolve_market_context("5016.T"), "2026-08-15")

    assert identity_provider.calls == ["5016"]
    assert financial_service.calls == [("E01081", date(2026, 8, 15))]
    financial = bundle["provider_metadata"]["EDINET DB Financials"]
    assert financial["analysis_as_of"] == "2026-08-15"
    assert financial["actual_selection"]["selection_basis"] == "LATEST_FISCAL_PERIOD_AS_OF"
    assert financial["financial_document"]["records"][0]["period_type"] == "Q1"
    assert any(item["source"] == "TDnet" for item in bundle["items"])


def test_full_mock_transport_resolver_selector_normalizer_bundle_chain(tmp_path):
    calls = []

    def http_get(url, *, params, headers, timeout):
        del headers, timeout
        calls.append((url, params))
        if url.endswith("/companies"):
            return _HTTPResponse(
                {
                    "data": [
                        {
                            "sec_code": "50160",
                            "edinet_code": "E01081",
                            "listing_status": "listed",
                            "is_delisted": False,
                        }
                    ],
                    "meta": {"data_as_of": "2026-08-15"},
                }
            )
        return _HTTPResponse(
            {
                "data": {
                    "count": 2,
                    "earnings": [
                        {
                            "record_id": "future-h1",
                            "quarter": 2,
                            "fiscal_year_end": "2027-03-31",
                            "disclosure_date": "2026-11-06",
                            "is_correction": False,
                            "accounting_standard": "IFRS",
                            "revenue": 999999,
                        },
                        {
                            "record_id": "eligible-q1",
                            "quarter": 1,
                            "fiscal_year_end": "2027-03-31",
                            "disclosure_date": "2026-08-06",
                            "is_correction": False,
                            "accounting_standard": "IFRS",
                            "unit": "百万円",
                            "revenue": 260604,
                            "operating_income": 81446,
                            "net_income": 53070,
                            "profit_ifrs": 60418,
                            "eps": 56.80,
                            "raw_secret_marker": "MUST_NOT_REACH_CONTEXT",
                        },
                    ],
                },
                "meta": {"data_as_of": "2026-08-15"},
            }
        )

    transport = EDINETDBProvider(api_key="test", http_get=http_get)
    adapter = EDINETDBFinancialBundleProvider(transport)
    bundle = asyncio.run(
        JapanDataService((_LegacyProvider(), adapter), cache=JapanDataCache(tmp_path), timeout_seconds=1).collect(
            resolve_market_context("5016.T"),
            start_date="2026-08-01",
            end_date="2026-08-15",
        )
    ).to_dict()

    document = bundle["provider_metadata"]["EDINET DB Financials"]["financial_document"]
    assert document["source_record_id"] == "eligible-q1"
    assert document["records"][0]["period_type"] == "Q1"
    assert document["records"][0]["metrics"]["revenue"]["value"] == 260604
    assert "MUST_NOT_REACH_CONTEXT" not in render_japan_financial_context(_state(bundle))
    assert calls[0][1] == {"sec_code": "5016", "per_page": 2}
    assert calls[1][1] == {"limit": 30}


def test_fundamentals_renderer_preserves_ifrs_semantics_and_provenance(tmp_path):
    adapter, _, _ = _adapter()
    bundle = asyncio.run(
        JapanDataService((_LegacyProvider(), adapter), cache=JapanDataCache(tmp_path), timeout_seconds=1).collect(
            resolve_market_context("5016.T"),
            start_date="2026-08-01",
            end_date="2026-08-15",
        )
    ).to_dict()

    rendered = render_japan_financial_context(_state(bundle))

    assert "Japan financial authority assessment" in rendered
    assert "Q1" in rendered
    assert "2027-03-31" in rendered
    assert "Revenue: 260604 百万円" in rendered
    assert "Operating profit: 81446 百万円" in rendered
    assert "Ordinary profit: NOT_APPLICABLE" in rendered
    assert "Net income (parent attributable): 53070 百万円" in rendered
    assert "Total period profit: 60418 百万円" in rendered
    assert "EPS: 56.8 円" in rendered
    assert "Current Company Guidance" in rendered
    assert "Revenue: 1025000 百万円" in rendered
    assert "Source: EDINET DB" in rendered
    assert "Source type: STRUCTURED_SOURCE" in rendered
    assert "earnings-5016-q1" in rendered
    assert "CURRENT_STRUCTURED_CONFIRMED" in rendered


def test_normalized_financial_metadata_survives_japan_provider_cache(tmp_path):
    adapter, identity_provider, financial_service = _adapter()
    data_service = JapanDataService(
        (_LegacyProvider(), adapter),
        cache=JapanDataCache(tmp_path),
        timeout_seconds=1,
    )
    context = resolve_market_context("5016.T")

    asyncio.run(
        data_service.collect(context, start_date="2026-08-01", end_date="2026-08-15")
    )
    cached = asyncio.run(
        data_service.collect(context, start_date="2026-08-01", end_date="2026-08-15")
    )

    assert identity_provider.calls == ["5016"]
    assert financial_service.calls == [("E01081", date(2026, 8, 15))]
    assert cached.source_statuses[0].from_cache is True
    document = cached.provider_metadata["EDINET DB Financials"]["financial_document"]
    assert document["source_record_id"] == "earnings-5016-q1"


def test_structured_financials_do_not_leak_into_shared_japan_context(tmp_path):
    adapter, _, _ = _adapter()
    bundle = asyncio.run(
        JapanDataService((_LegacyProvider(), adapter), cache=JapanDataCache(tmp_path), timeout_seconds=1).collect(
            resolve_market_context("5016.T"),
            start_date="2026-08-01",
            end_date="2026-08-15",
        )
    ).to_dict()
    state = _state(bundle)

    shared = render_japan_agent_context(state)
    financial = render_japan_financial_context(state)

    assert "EDINET DB structured financials" not in shared
    assert "260604" not in shared
    assert "earnings-5016-q1" not in shared
    assert "Japan financial authority assessment" in financial


def test_fundamentals_analyst_adds_financial_context_only_for_jp(monkeypatch, tmp_path):
    from tradingagents.agents.analysts import fundamentals_analyst as module

    adapter, _, _ = _adapter()
    bundle = asyncio.run(
        JapanDataService((_LegacyProvider(), adapter), cache=JapanDataCache(tmp_path), timeout_seconds=1).collect(
            resolve_market_context("5016.T"),
            start_date="2026-08-01",
            end_date="2026-08-15",
        )
    ).to_dict()
    captured: list[str] = []

    class _Prompt:
        def __init__(self):
            self.values = {}

        def partial(self, **kwargs):
            self.values.update(kwargs)
            return self

        def __or__(self, _bound_llm):
            prompt = self

            class _Chain:
                def invoke(self, _messages):
                    captured.append(prompt.values["system_message"])
                    return AIMessage(content="fundamentals complete")

            return _Chain()

    monkeypatch.setattr(module.ChatPromptTemplate, "from_messages", lambda _messages: _Prompt())
    llm = MagicMock()
    llm.bind_tools.return_value = object()
    analyst = module.create_fundamentals_analyst(llm)

    analyst(_state(bundle))
    us_state = Propagator().create_initial_state("NVDA", "2026-08-15")
    analyst(us_state)

    assert "Japan financial authority assessment" in str(captured[0])
    assert "260604" in str(captured[0])
    assert "Japan financial authority assessment" not in str(captured[1])
    assert "260604" not in str(captured[1])


def test_news_analyst_does_not_receive_structured_financial_context(monkeypatch, tmp_path):
    from tradingagents.agents.analysts import news_analyst as module

    adapter, _, _ = _adapter()
    bundle = asyncio.run(
        JapanDataService((_LegacyProvider(), adapter), cache=JapanDataCache(tmp_path), timeout_seconds=1).collect(
            resolve_market_context("5016.T"),
            start_date="2026-08-01",
            end_date="2026-08-15",
        )
    ).to_dict()
    captured: list[str] = []

    class _Prompt:
        def __init__(self):
            self.values = {}

        def partial(self, **kwargs):
            self.values.update(kwargs)
            return self

        def __or__(self, _bound_llm):
            prompt = self

            class _Chain:
                def invoke(self, _messages):
                    captured.append(prompt.values["system_message"])
                    return AIMessage(content="news complete")

            return _Chain()

    monkeypatch.setattr(module.ChatPromptTemplate, "from_messages", lambda _messages: _Prompt())
    llm = MagicMock()
    llm.bind_tools.return_value = object()

    module.create_news_analyst(llm)(_state(bundle))

    assert "Japan financial authority assessment" not in captured[0]
    assert "260604" not in captured[0]
    assert "earnings-5016-q1" not in captured[0]


def test_identity_failure_does_not_call_earnings_or_remove_legacy_items(tmp_path):
    adapter, identity_provider, financial_service = _adapter(
        identity=_identity(IssuerIdentityStatus.DATA_UNAVAILABLE, detail="NO_EXACT_MATCH")
    )
    bundle = asyncio.run(
        JapanDataService(
            (_LegacyProvider(), adapter),
            cache=JapanDataCache(tmp_path),
            timeout_seconds=1,
        ).collect(
            resolve_market_context("5016.T"),
            start_date="2026-08-08",
            end_date="2026-08-15",
        )
    )

    assert identity_provider.calls == ["5016"]
    assert financial_service.calls == []
    assert any(item.source == "TDnet" for item in bundle.items)
    status = {entry.source: entry for entry in bundle.source_statuses}
    assert status["TDnet"].status == DataStatus.OK
    assert status["EDINET DB Financials"].status == DataStatus.DATA_UNAVAILABLE
    assert "EDINET DB Financials" not in bundle.provider_metadata


@pytest.mark.parametrize(
    "transport_status",
    (
        EDINETDBStatus.AUTH_REQUIRED,
        EDINETDBStatus.RATE_LIMITED,
        EDINETDBStatus.TIMEOUT,
        EDINETDBStatus.API_ERROR,
        EDINETDBStatus.DATA_UNAVAILABLE,
    ),
)
def test_edinet_failure_isolated_from_existing_japan_sources(transport_status, tmp_path):
    response = EDINETDBResponse(status=transport_status, detail="safe diagnostic")
    adapter, _, _ = _adapter(result=response)
    bundle = asyncio.run(
        JapanDataService(
            (_LegacyProvider(), adapter),
            cache=JapanDataCache(tmp_path),
            timeout_seconds=1,
        ).collect(
            resolve_market_context("5016.T"),
            start_date="2026-08-08",
            end_date="2026-08-15",
        )
    )

    assert any(item.source == "TDnet" for item in bundle.items)
    status = {entry.source: entry for entry in bundle.source_statuses}
    expected = {
        EDINETDBStatus.AUTH_REQUIRED: DataStatus.AUTH_REQUIRED,
        EDINETDBStatus.RATE_LIMITED: DataStatus.RATE_LIMITED,
    }.get(transport_status, DataStatus.DATA_UNAVAILABLE)
    assert status["EDINET DB Financials"].status == expected
    assert render_japan_financial_context(_state(bundle.to_dict())) == ""


def test_raw_response_and_diagnostics_never_enter_financial_context(tmp_path):
    adapter, _, _ = _adapter()
    bundle = asyncio.run(
        JapanDataService((_LegacyProvider(), adapter), cache=JapanDataCache(tmp_path), timeout_seconds=1).collect(
            resolve_market_context("5016.T"),
            start_date="2026-08-01",
            end_date="2026-08-15",
        )
    ).to_dict()
    rendered = render_japan_financial_context(_state(bundle))

    assert "raw_response" not in str(bundle["provider_metadata"])
    assert "safe diagnostic" not in rendered
    assert "HTTP" not in rendered
    assert "API key" not in rendered.lower()


def test_documented_live_amount_unit_is_preserved(tmp_path):
    document = normalize_financial_document(
        {
            "record_id": "live-without-unit",
            "disclosure_date": "2026-08-06",
            "accounting_standard": "IFRS",
            "quarter": 1,
            "fiscal_year_end": "2027-03-31",
            "revenue": 260604,
        },
        fetched_at="2026-08-15T00:00:00+00:00",
    )
    adapter, _, _ = _adapter(result=document)
    bundle = asyncio.run(
        JapanDataService((_LegacyProvider(), adapter), cache=JapanDataCache(tmp_path), timeout_seconds=1).collect(
            resolve_market_context("5016.T"),
            start_date="2026-08-01",
            end_date="2026-08-15",
        )
    ).to_dict()

    revenue = bundle["provider_metadata"]["EDINET DB Financials"]["actual_document"][
        "records"
    ][0]["metrics"]["revenue"]
    rendered = render_japan_financial_context(_state(bundle))

    assert revenue["unit"] == "百万円"
    assert "Normalized critical values: DATA_UNAVAILABLE" in rendered
    assert "Revenue: 260604" not in rendered


def test_q4_guidance_target_is_not_invented(tmp_path):
    adapter, _, _ = _adapter(result=_financial_document(quarter=4, fiscal_year_end="2026-03-31"))
    bundle = asyncio.run(
        JapanDataService((_LegacyProvider(), adapter), cache=JapanDataCache(tmp_path), timeout_seconds=1).collect(
            resolve_market_context("5016.T"),
            start_date="2026-08-01",
            end_date="2026-08-15",
        )
    ).to_dict()
    rendered = render_japan_financial_context(_state(bundle))

    guidance = bundle["provider_metadata"]["EDINET DB Financials"]["guidance_document"][
        "records"
    ][0]
    assert guidance["period_type"] == "FY"
    assert guidance["target_period_end"] is None
    assert "Current Company Guidance" in rendered


def test_provider_is_registered_without_ticker_specific_rules():
    names = [provider.name for provider in build_official_japan_providers()]
    assert "EDINET DB Financials" in names
    source = inspect.getsource(EDINETDBFinancialBundleProvider)
    assert "5016" not in source
    assert "6324" not in source
    assert "6981" not in source


def test_us_context_never_exposes_japan_financials():
    jp_bundle = {
        "provider_metadata": {
            "EDINET DB Financials": {"financial_document": _financial_document().to_dict()}
        }
    }
    state = Propagator().create_initial_state("NVDA", "2026-08-15", japan_data_bundle=jp_bundle)
    assert render_japan_financial_context(state) == ""
    assert render_japan_agent_context(state) == ""
