"""Credentials never cross logs, error, persistence, report, or Web boundaries."""

from __future__ import annotations

import copy
import io
import json
import logging
from pathlib import Path
from unittest import mock

import pytest
import requests
from rich.console import Console

import tradingagents.dataflows.config as config_module
import tradingagents.default_config as default_config
from tradingagents.dataflows import interface
from tradingagents.dataflows.config import set_config
from tradingagents.reporting import write_report_tree
from tradingagents.secret_redaction import (
    REDACTED,
    redacted_exception,
    safe_exception_text,
    sanitize_data,
    sanitize_text,
)
from web.server import _render_report_html, _safe_web_event

SECRET = "TEST_SECRET_DO_NOT_LOG_123"
SECRET_URL = (
    "https://api.example.test/fred/series/observations?"
    f"series_id=CPIAUCSL&api_key={SECRET}&file_type=json"
)


def _assert_safe(output: str) -> None:
    assert SECRET not in output
    assert REDACTED in output


@pytest.mark.unit
def test_query_parameter_redaction_preserves_debuggable_url_parts():
    output = sanitize_text(f"GET {SECRET_URL} HTTP 502")

    _assert_safe(output)
    assert "api.example.test/fred/series/observations" in output
    assert "series_id=CPIAUCSL" in output
    assert "api_key=[REDACTED]" in output
    assert "HTTP 502" in output


@pytest.mark.unit
def test_headers_bearer_and_nested_error_payload_are_redacted():
    payload = {
        "headers": {
            "Authorization": f"Bearer {SECRET}",
            "X-Api-Key": SECRET,
            "Accept": "application/json",
        },
        "errors": [
            {"access_token": SECRET, "status": 401},
            f'{{"client_secret":"{SECRET}","reason":"denied"}}',
            f"upstream said Bearer {SECRET}",
        ],
    }

    safe = sanitize_data(payload)
    rendered = json.dumps(safe, ensure_ascii=False)

    assert SECRET not in rendered
    assert safe["headers"]["Authorization"] == REDACTED
    assert safe["headers"]["X-Api-Key"] == REDACTED
    assert safe["headers"]["Accept"] == "application/json"
    assert safe["errors"][0]["status"] == 401
    assert "denied" in rendered


@pytest.mark.unit
def test_requests_and_httpx_style_exception_messages_are_safe():
    requests_error = requests.HTTPError(f"502 Server Error for url: {SECRET_URL}")

    class HttpxStyleRequestError(Exception):
        pass

    httpx_error = HttpxStyleRequestError(
        f"request failed Authorization: Bearer {SECRET}; POST https://api.minimax.io/v1/text"
    )

    requests_output = safe_exception_text(requests_error)
    httpx_output = safe_exception_text(httpx_error)
    raised = redacted_exception(requests_error)

    _assert_safe(requests_output)
    _assert_safe(httpx_output)
    _assert_safe(str(raised))
    assert isinstance(raised, requests.HTTPError)
    assert "502" in requests_output
    assert "api.minimax.io/v1/text" in httpx_output


@pytest.mark.unit
def test_requests_raise_for_status_reproduces_and_redacts_prepared_url():
    response = requests.Response()
    response.status_code = 502
    response.url = SECRET_URL

    with pytest.raises(requests.HTTPError) as captured:
        response.raise_for_status()

    # The transport's raw error really does contain the prepared credential URL.
    assert SECRET in str(captured.value)
    safe = safe_exception_text(captured.value)
    assert SECRET not in safe
    assert "api_key=[REDACTED]" in safe
    assert "502 Server Error" in safe


@pytest.mark.unit
def test_log_record_boundary_redacts_retry_and_exception_traceback(caplog):
    logger = logging.getLogger("tests.secret-boundary")
    error = requests.HTTPError(f"502 Server Error for url: {SECRET_URL}")

    with caplog.at_level(logging.WARNING):
        logger.warning("retry attempt=%s failed: %s", 2, error)
        try:
            raise ValueError(f"Authorization: Bearer {SECRET}")
        except ValueError:
            logger.exception("provider request failed")

    output = caplog.text
    assert SECRET not in output
    assert "retry attempt=2" in output
    assert "502 Server Error" in output
    assert "provider request failed" in output


@pytest.mark.unit
def test_structured_log_extra_headers_are_redacted_before_handler_formatting():
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(logging.Formatter("%(message)s %(request_metadata)s"))
    logger = logging.getLogger("tests.structured-secret-boundary")
    logger.addHandler(handler)
    logger.propagate = False
    try:
        logger.warning(
            "retry failed",
            extra={
                "request_metadata": {
                    "url": SECRET_URL,
                    "headers": {"Authorization": f"Bearer {SECRET}"},
                    "status": 502,
                }
            },
        )
    finally:
        logger.removeHandler(handler)
        logger.propagate = True

    rendered = output.getvalue()
    assert SECRET not in rendered
    assert "api_key=[REDACTED]" in rendered
    assert "status" in rendered and "502" in rendered


@pytest.mark.unit
def test_vendor_router_sanitizes_log_tool_output_and_raised_error(caplog):
    config_module._config = copy.deepcopy(default_config.DEFAULT_CONFIG)

    def failing_vendor(*_args, **_kwargs):
        raise requests.HTTPError(f"502 Server Error for url: {SECRET_URL}")

    set_config({"data_vendors": {"macro_data": "fred"}})
    with mock.patch.dict(
        interface.VENDOR_METHODS,
        {"get_macro_indicators": {"fred": failing_vendor}},
        clear=False,
    ), caplog.at_level(logging.WARNING):
        output = interface.route_to_vendor(
            "get_macro_indicators", "cpi", "2026-09-01", 365
        )

    assert SECRET not in caplog.text
    assert SECRET not in output
    assert "api_key=[REDACTED]" in output
    assert "502 Server Error" in output

    set_config({"data_vendors": {"core_stock_apis": "failing"}})
    with mock.patch.dict(
        interface.VENDOR_METHODS,
        {"get_stock_data": {"failing": failing_vendor}},
        clear=False,
    ), pytest.raises(requests.HTTPError) as captured:
        interface.route_to_vendor("get_stock_data", "AAPL", "2026-08-01", "2026-09-01")
    assert SECRET not in str(captured.value)
    assert "api_key=[REDACTED]" in str(captured.value)


def _state_with_secret() -> dict:
    return {
        "market_context": {
            "symbol": "AAPL",
            "market": "US",
            "currency": "USD",
            "instrument_type": "EQUITY",
        },
        "run_manifest": {
            "provider": "synthetic",
            "request": {"Authorization": f"Bearer {SECRET}"},
        },
        "market_report": f"Market source failed at {SECRET_URL}",
        "news_report": "News diagnostics retained status=502",
        "investment_debate_state": {"judge_decision": "No executable plan."},
        "trader_investment_plan": "No executable plan.",
        "risk_debate_state": {"judge_decision": "Hold."},
        "raw_agent_outputs": {
            "market_report": f"raw token={SECRET}",
            "nested": [{"password": SECRET, "status": 403}],
        },
        "evidence_registry": [
            {"source": "synthetic", "source_url": SECRET_URL, "status": "ERROR"}
        ],
    }


@pytest.mark.unit
def test_report_and_full_agent_log_persistence_redacts_recursively(tmp_path):
    report_path = write_report_tree(_state_with_secret(), "AAPL", tmp_path)
    persisted = "\n".join(
        path.read_text(encoding="utf-8") for path in tmp_path.rglob("*.md")
    )

    assert report_path.exists()
    assert SECRET not in persisted
    assert "api.example.test/fred/series/observations" in persisted
    assert "status=502" in persisted
    assert REDACTED in persisted


@pytest.mark.unit
def test_web_error_and_html_boundaries_redact_without_losing_context():
    event = _safe_web_event(
        {"type": "error", "message": f"HTTP 401 Authorization: Bearer {SECRET}"}
    )
    html = _render_report_html(_state_with_secret(), auto_print=False)

    rendered_event = json.dumps(event)
    assert SECRET not in rendered_event
    assert "HTTP 401" in rendered_event
    assert SECRET not in html
    assert "api.example.test/fred/series/observations" in html
    assert REDACTED in html


@pytest.mark.unit
def test_user_report_sanitization_does_not_remove_normal_financial_content(tmp_path):
    state = _state_with_secret()
    state["fundamentals_report"] = (
        "Revenue 365,221 million JPY; EPS 31.60; "
        f"diagnostic x-api-key={SECRET}."
    )

    report = write_report_tree(state, "AAPL", tmp_path).read_text(encoding="utf-8")

    assert SECRET not in report
    assert "Revenue 365,221 million JPY; EPS 31.60" in report
    assert "x-api-key=[REDACTED]" in report


@pytest.mark.unit
def test_no_secret_is_written_to_any_report_artifact(tmp_path):
    write_report_tree(_state_with_secret(), "AAPL", tmp_path)

    for path in Path(tmp_path).rglob("*"):
        if path.is_file():
            assert SECRET not in path.read_text(encoding="utf-8")


@pytest.mark.unit
def test_console_boundary_uses_safe_exception_formatting():
    output = io.StringIO()
    console = Console(file=output, force_terminal=False, color_system=None, width=500)
    error = requests.HTTPError(f"502 Server Error for url: {SECRET_URL}")

    console.print(f"Provider failed: {safe_exception_text(error)}")

    rendered = output.getvalue()
    assert SECRET not in rendered
    assert "api_key=[REDACTED]" in rendered
    assert "502 Server Error" in rendered


@pytest.mark.unit
def test_full_state_json_persistence_redacts_raw_tool_errors(tmp_path):
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    graph.log_states_dict = {}
    graph.ticker = "AAPL"
    graph.config = {"results_dir": str(tmp_path)}
    state = _state_with_secret() | {
        "company_of_interest": "AAPL",
        "trade_date": "2026-09-01",
        "verified_market_snapshot": "",
        "japan_data_bundle": {},
        "evidence_audit": [],
        "validated_execution": {},
        "final_output_contract": {},
        "sentiment_report": "",
        "fundamentals_report": "",
        "investment_plan": "No plan.",
        "final_trade_decision": "Hold.",
        "investment_debate_state": {
            "bull_history": "",
            "bear_history": "",
            "history": "",
            "current_response": "",
            "judge_decision": "No executable plan.",
        },
        "risk_debate_state": {
            "aggressive_history": "",
            "conservative_history": "",
            "neutral_history": "",
            "history": "",
            "judge_decision": "Hold.",
        },
    }

    graph._log_state("2026-09-01", state)

    persisted = next(tmp_path.rglob("full_states_log_*.json")).read_text(encoding="utf-8")
    assert SECRET not in persisted
    assert "api_key=[REDACTED]" in persisted
    assert "api.example.test/fred/series/observations" in persisted
