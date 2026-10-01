# Fresh acceptance graph observation

`AcceptanceGraphObserver` is an opt-in acceptance adapter, not a production
execution implementation. Production `Propagator.get_graph_args()` remains the
owner of the invocation arguments. Pass them unchanged through the normal
`TradingAgentsGraph.propagate()` entry point. The adapter delegates to the
original compiled graph's `invoke()` (or `stream()` for production debug mode).
Pregel, not the observer, chooses internal stream modes and aggregates results.

```python
from dataclasses import asdict
from tradingagents.graph.acceptance_observer import AcceptanceGraphObserver

# Capture callbacks must use the existing secret-safe persistence boundary.
observer = AcceptanceGraphObserver(
    ta.graph,
    on_initial=capture_initial,
    on_event=capture_event,
    on_lifecycle=lambda lifecycle: save_counts(asdict(lifecycle)),
)
ta.graph = observer
observer.request_propagation()
state, signal = ta.propagate(ticker, analysis_as_of)  # exactly once, no retry
```

The observer temporarily wraps only that dedicated compiled instance's stream
method during `invoke()`, restoring the exact prior attribute even on failure.
Do not share the instance with concurrent callers. No callback is inserted into
the production config; no checkpoint, memory, provider or authority code changes.
Exceptions from production and capture are propagated without retry.

## Lifecycle meanings

| Counter | Evidence |
| --- | --- |
| `propagate_requested` | The harness requested one normal production call. |
| `observer_entered` | Production reached the observer interface. Not execution start. |
| `compiled_stream_calls` | One underlying stream call/iteration was attempted. Binding can still fail. |
| `compiled_graph_execution_started` | The first real stream event was received. |
| `first_agent_or_graph_step_observed` | A named updates event was received, not just initial values. |
| `graph_completed` | Original invoke returned, or the debug stream exhausted normally. |
| `stream_events_observed` | Actual events received, including initial values. |
| `values_states_observed` | Values events received; not a count of completed Agents. |

`compiled_graph_execution_started=0` means **no start confirmed**. It does not
claim that no internal work happened if a graph failed before its first event.
`graph_completed` is not artifact acceptance; production canonical validation and
persistence still happen after graph return.

The compatibility marker `ONE_GRAPH_STARTED` may only be written after
`compiled_graph_execution_started=1`. Historical markers from failed acceptance
runs remain historical evidence; do not rewrite them. Replace the ambiguous
`graph_invocations` entry counter with the named lifecycle fields above.

The e54d62ca failure was in an external acceptance harness: it replaced `invoke`
with `stream(initial, stream_mode="values", **kwargs)` even though production
already supplied `kwargs["stream_mode"]`. No Pregel execution or Agent started.
The external adapter must import this helper rather than duplicate forwarding.

Test with fake graphs or a local StateGraph containing only pure local nodes.
No production TradingAgents graph, provider or external data source is needed.
