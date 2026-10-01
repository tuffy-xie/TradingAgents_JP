"""Offline observation tests: no production agents, providers or data sources."""

from dataclasses import asdict
from typing import TypedDict
from unittest.mock import Mock

import pytest
from langgraph.graph import END, START, StateGraph

from tradingagents.graph.acceptance_observer import (
    AcceptanceGraphObserver,
    SecondGraphExecutionForbidden,
)
from tradingagents.graph.propagation import Propagator


class FakeCompiledGraph:
    def __init__(self, *, before_stream=None, before_event=None, after_event=None):
        self.before_stream = before_stream
        self.before_event = before_event
        self.after_event = after_event
        self.invoke_calls = []
        self.stream_calls = []
        self.result = object()

    def invoke(self, initial, config=None, *, stream_mode="production_default", **kwargs):
        self.invoke_calls.append((initial, config, stream_mode, kwargs))
        if self.before_stream:
            raise self.before_stream
        list(self.stream(initial, config, stream_mode=stream_mode, **kwargs))
        return self.result

    def stream(self, initial, config=None, *, stream_mode="production_default", **kwargs):
        self.stream_calls.append((initial, config, stream_mode, kwargs))
        if self.before_event:
            raise self.before_event
        yield ("values", initial)
        yield ("updates", {"local_step": {"done": True}})
        if self.after_event:
            raise self.after_event


def test_original_adapter_failure_reproduced_without_execution():
    graph = FakeCompiledGraph()
    args = Propagator().get_graph_args()
    with pytest.raises(TypeError, match="multiple values.*stream_mode"):
        graph.stream({}, stream_mode="values", **args)
    assert not graph.stream_calls
    # The normal production interface has only one parameter owner.
    assert graph.invoke({}, **args) is graph.result
    assert len(graph.stream_calls) == 1


@pytest.mark.parametrize("explicit_mode", [True, False])
def test_forwarding_keeps_production_args_defaults_and_result(explicit_mode):
    graph = FakeCompiledGraph()
    callbacks = [object()]
    config = {"callbacks": callbacks, "configurable": {"thread_id": "local"}}
    args = {"config": config, "context": object(), "durability": "sync"}
    if explicit_mode:
        args["stream_mode"] = "values"
    original_args = dict(args)
    initial = {"input": "local"}
    observer = AcceptanceGraphObserver(graph)
    observer.request_propagation()
    assert observer.invoke(initial, **args) is graph.result
    assert args == original_args
    assert graph.invoke_calls[0][0] is initial
    assert graph.invoke_calls[0][1] is config
    assert graph.stream_calls[0][1] is config
    assert config["callbacks"] is callbacks
    assert graph.stream_calls[0][2] == ("values" if explicit_mode else "production_default")
    assert graph.invoke_calls[0][3] == {key: value for key, value in args.items() if key not in {"config", "stream_mode"}}
    assert observer.lifecycle.graph_completed == 1
    assert observer.lifecycle.compiled_graph_execution_started == 1
    assert observer.lifecycle.first_agent_or_graph_step_observed == 1
    assert "stream" not in vars(graph)


@pytest.mark.parametrize("failure_point", ["before_stream", "before_event", "after_event"])
def test_production_exception_propagates_same_object_without_retry(failure_point):
    error = RuntimeError("local production failure")
    graph = FakeCompiledGraph(**{failure_point: error})
    observer = AcceptanceGraphObserver(graph)
    observer.request_propagation()
    with pytest.raises(RuntimeError) as caught:
        observer.invoke({}, **Propagator().get_graph_args())
    assert caught.value is error
    assert len(graph.invoke_calls) == 1
    assert len(graph.stream_calls) == (0 if failure_point == "before_stream" else 1)
    assert observer.lifecycle.compiled_graph_execution_started == (1 if failure_point == "after_event" else 0)
    assert observer.lifecycle.graph_completed == 0
    assert "stream" not in vars(graph)
    with pytest.raises(SecondGraphExecutionForbidden):
        observer.invoke({})
    assert len(graph.invoke_calls) == 1


def test_binding_failure_is_not_execution_start():
    class StrictGraph(FakeCompiledGraph):
        def stream(self, initial):
            yield initial

    graph = StrictGraph()
    observer = AcceptanceGraphObserver(graph)
    with pytest.raises(TypeError):
        observer.invoke({}, **Propagator().get_graph_args())
    assert observer.lifecycle.compiled_stream_calls == 1
    assert observer.lifecycle.compiled_graph_execution_started == 0
    assert observer.lifecycle.first_agent_or_graph_step_observed == 0
    assert observer.lifecycle.graph_completed == 0


def test_start_marker_waits_for_actual_event_and_agent_marker_for_update(tmp_path):
    snapshots = []
    marker = tmp_path / "ONE_GRAPH_STARTED"

    def lifecycle_changed(lifecycle):
        snapshots.append(asdict(lifecycle))
        if lifecycle.compiled_graph_execution_started and not marker.exists():
            marker.write_text("1\n", encoding="utf-8")

    def initial_observed(_initial):
        assert not marker.exists()
        assert snapshots[-1]["observer_entered"] == 1
        assert snapshots[-1]["compiled_graph_execution_started"] == 0

    graph = FakeCompiledGraph()
    observer = AcceptanceGraphObserver(graph, on_initial=initial_observed, on_lifecycle=lifecycle_changed)
    observer.request_propagation()
    assert not marker.exists()
    observer.invoke({})
    first_event = next(item for item in snapshots if item["stream_events_observed"] == 1)
    assert first_event["compiled_graph_execution_started"] == 1
    assert first_event["first_agent_or_graph_step_observed"] == 0
    assert observer.lifecycle.first_agent_or_graph_step_observed == 1
    assert marker.read_text() == "1\n"
    assert asdict(observer.lifecycle) == {
        "propagate_requested": 1, "observer_entered": 1, "compiled_stream_calls": 1,
        "compiled_graph_execution_started": 1, "first_agent_or_graph_step_observed": 1,
        "graph_completed": 1, "stream_events_observed": 2, "values_states_observed": 1,
    }


def test_observer_error_is_not_retried_or_hidden():
    error = RuntimeError("local observation failure")

    def fails(*_args):
        raise error

    graph = FakeCompiledGraph()
    observer = AcceptanceGraphObserver(graph, on_event=fails)
    with pytest.raises(RuntimeError) as caught:
        observer.invoke({})
    assert caught.value is error
    assert len(graph.invoke_calls) == len(graph.stream_calls) == 1
    assert observer.lifecycle.graph_completed == 0
    assert "stream" not in vars(graph)


def test_one_propagate_cannot_execute_compiled_stream_twice():
    class TwiceGraph(FakeCompiledGraph):
        def invoke(self, initial, **kwargs):
            list(self.stream(initial, **kwargs))
            list(self.stream(initial, **kwargs))

    graph = TwiceGraph()
    observer = AcceptanceGraphObserver(graph)
    observer.request_propagation()
    with pytest.raises(SecondGraphExecutionForbidden):
        observer.invoke({})
    assert len(graph.stream_calls) == 1
    assert observer.lifecycle.graph_completed == 0
    with pytest.raises(SecondGraphExecutionForbidden):
        observer.request_propagation()


def test_existing_instance_stream_hook_is_restored():
    graph = FakeCompiledGraph()
    previous = graph.stream
    graph.stream = previous
    AcceptanceGraphObserver(graph).invoke({})
    assert vars(graph)["stream"] is previous


def test_debug_stream_preserves_events_and_single_execution():
    graph = FakeCompiledGraph()
    initial = {"input": "local"}
    observer = AcceptanceGraphObserver(graph)
    observer.request_propagation()
    events = list(observer.stream(initial, **Propagator().get_graph_args()))
    assert events == [("values", initial), ("updates", {"local_step": {"done": True}})]
    assert not graph.invoke_calls
    assert len(graph.stream_calls) == 1
    assert observer.lifecycle.graph_completed == 1
    with pytest.raises(SecondGraphExecutionForbidden):
        observer.invoke(initial)


@pytest.mark.parametrize("stream_mode", [None, "values", "updates", ["values", "updates"]])
@pytest.mark.parametrize("version", ["v1", "v2"])
def test_real_local_pregel_interface_is_unchanged(stream_mode, version):
    class LocalState(TypedDict):
        value: int

    runs = []

    def local_step(state):
        runs.append(1)
        return {"value": state["value"] + 1}

    builder = StateGraph(LocalState)
    builder.add_node("local_step", local_step)
    builder.add_edge(START, "local_step")
    builder.add_edge("local_step", END)
    graph = builder.compile()
    args = {"version": version}
    if stream_mode is not None:
        args["stream_mode"] = stream_mode
    expected = graph.invoke({"value": 0}, **args)
    runs.clear()
    observer = AcceptanceGraphObserver(graph)
    observer.request_propagation()
    actual = observer.invoke({"value": 0}, **args)
    assert actual == expected
    assert runs == [1]
    assert observer.lifecycle.compiled_graph_execution_started == 1
    assert observer.lifecycle.graph_completed == 1
    assert "stream" not in vars(graph)


@pytest.mark.parametrize("has_stream_mode", [True, False])
def test_normal_production_propagate_forwards_once_without_sources(monkeypatch, has_stream_mode):
    """Run production orchestration, replacing every source and compiled graph."""
    from tradingagents.dataflows.market import resolve_market_context
    from tradingagents.graph import trading_graph as module

    context = resolve_market_context("AAPL")
    monkeypatch.setattr(module, "resolve_instrument_identity", lambda *_: {})
    monkeypatch.setattr(module, "enrich_market_context", lambda *_: context)
    monkeypatch.setattr(module, "build_instrument_context", lambda *_: "")
    monkeypatch.setattr(module, "collect_japan_data_bundle", lambda *_: {})
    monkeypatch.setattr(module, "build_verified_market_snapshot", lambda *_: "local only")
    monkeypatch.setattr(module, "build_run_manifest", lambda **_: {"run_id": "offline"})
    monkeypatch.setattr(module, "build_canonical_final_state", lambda state: state)
    initial = {"final_trade_decision": "local test output"}
    compiled = FakeCompiledGraph()
    compiled.result = initial
    observer = AcceptanceGraphObserver(compiled)
    graph = object.__new__(module.TradingAgentsGraph)
    graph.graph = observer
    graph.config = {"checkpoint_enabled": False}
    graph.debug = False
    graph._checkpointer_ctx = None
    graph._resolve_pending_entries = Mock()
    graph.memory_log = Mock()
    graph.memory_log.get_past_context.return_value = "existing memory"
    graph.propagator = Mock()
    graph.propagator.create_initial_state.return_value = initial
    args = Propagator().get_graph_args()
    if not has_stream_mode:
        del args["stream_mode"]
    graph.propagator.get_graph_args.return_value = args
    graph._log_state = Mock()
    graph.process_signal = Mock(return_value="local signal")

    observer.request_propagation()
    state, signal = graph.propagate("AAPL", "2026-01-05")
    assert state is initial
    assert signal == "local signal"
    assert len(compiled.invoke_calls) == len(compiled.stream_calls) == 1
    assert compiled.invoke_calls[0][1] is args["config"]
    graph._log_state.assert_called_once_with("2026-01-05", initial)
    graph.memory_log.store_decision.assert_called_once_with(
        ticker="AAPL", trade_date="2026-01-05", final_trade_decision=initial["final_trade_decision"]
    )
    assert observer.lifecycle.graph_completed == 1
