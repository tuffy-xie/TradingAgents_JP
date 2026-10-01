"""Single-use, synchronous observation of the existing production graph interface.

No graph arguments, defaults, callbacks or returned values are synthesized here.
In particular, ``invoke`` remains ``invoke``: Pregel owns its internal stream mode
and output aggregation. A scoped instance hook only watches the events it emits.
Use a dedicated compiled graph instance, not one shared with concurrent callers.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any


class SecondGraphExecutionForbidden(RuntimeError):
    """An acceptance observer must never initiate a second execution."""


@dataclass
class GraphLifecycle:
    propagate_requested: int = 0
    observer_entered: int = 0
    compiled_stream_calls: int = 0
    # Conservative confirmation: entry/binding/iterator creation is NOT start.
    # Zero means no start observed, not proof that no internal work happened.
    compiled_graph_execution_started: int = 0
    first_agent_or_graph_step_observed: int = 0
    graph_completed: int = 0
    stream_events_observed: int = 0
    values_states_observed: int = 0


def stream_event_parts(event: Any, stream_mode: Any) -> tuple[Any, Any]:
    """Read an event for observation, without changing the event or graph result."""
    if isinstance(event, tuple) and len(event) in (2, 3):
        return event[-2], event[-1]
    # Pregel v2 StreamPart; traditional dictionary state remains untouched.
    if (
        isinstance(event, dict)
        and event.get("type") in ("values", "updates")
        and "data" in event
        and isinstance(stream_mode, (list, tuple))
    ):
        return event["type"], event["data"]
    return stream_mode, event


class AcceptanceGraphObserver:
    """Delegate a single production call, preserving exact args and exceptions.

    ``request_propagation`` records the caller's intent before normal propagate().
    Start is confirmed at the first emitted stream event. Agent/step confirmation
    additionally requires a named updates event (an initial values event is not
    a completed Agent). ``graph_completed`` means normal return/exhaustion only.
    Observation failures and production exceptions propagate without any retry.
    """

    def __init__(
        self,
        graph: Any,
        *,
        on_initial: Callable[[Any], None] | None = None,
        on_event: Callable[[Any, Any], None] | None = None,
        on_lifecycle: Callable[[GraphLifecycle], None] | None = None,
    ) -> None:
        self.graph = graph
        self.lifecycle = GraphLifecycle()
        self.on_initial = on_initial
        self.on_event = on_event
        self.on_lifecycle = on_lifecycle

    def __getattr__(self, name: str) -> Any:
        return getattr(self.graph, name)

    def _notify(self) -> None:
        if self.on_lifecycle:
            self.on_lifecycle(self.lifecycle)

    def request_propagation(self) -> None:
        if self.lifecycle.propagate_requested:
            raise SecondGraphExecutionForbidden("SECOND_PROPAGATION_FORBIDDEN")
        self.lifecycle.propagate_requested = 1
        self._notify()

    def _enter(self, initial: Any) -> None:
        if self.lifecycle.observer_entered:
            raise SecondGraphExecutionForbidden("SECOND_GRAPH_FORBIDDEN")
        self.lifecycle.observer_entered = 1
        self._notify()
        if self.on_initial:
            self.on_initial(initial)

    def _observe_stream(self, original: Callable, *args: Any, **kwargs: Any) -> Iterator:
        if self.lifecycle.compiled_stream_calls:
            raise SecondGraphExecutionForbidden("SECOND_COMPILED_STREAM_FORBIDDEN")
        self.lifecycle.compiled_stream_calls = 1
        self._notify()
        # No explicit stream_mode or other override: the original owns binding.
        for event in original(*args, **kwargs):
            self.lifecycle.compiled_graph_execution_started = 1
            self.lifecycle.stream_events_observed += 1
            mode, payload = stream_event_parts(event, kwargs.get("stream_mode"))
            if mode == "values":
                self.lifecycle.values_states_observed += 1
            if (
                mode == "updates"
                and isinstance(payload, dict)
                and any(not str(key).startswith("__") for key in payload)
            ):
                self.lifecycle.first_agent_or_graph_step_observed = 1
            self._notify()
            if self.on_event:
                self.on_event(event, kwargs.get("stream_mode"))
            yield event

    def invoke(self, initial: Any, *args: Any, **kwargs: Any) -> Any:
        self._enter(initial)
        original_stream = self.graph.stream
        missing = object()
        previous = vars(self.graph).get("stream", missing)

        def observed_stream(*stream_args: Any, **stream_kwargs: Any) -> Iterator:
            return self._observe_stream(original_stream, *stream_args, **stream_kwargs)

        self.graph.stream = observed_stream
        try:
            result = self.graph.invoke(initial, *args, **kwargs)
        finally:
            if previous is missing:
                del self.graph.stream
            else:
                self.graph.stream = previous
        self.lifecycle.graph_completed = 1
        self._notify()
        return result

    def stream(self, initial: Any, *args: Any, **kwargs: Any) -> Iterator:
        """Observe the normal production debug stream, without invoking twice."""
        self._enter(initial)
        yield from self._observe_stream(self.graph.stream, initial, *args, **kwargs)
        self.lifecycle.graph_completed = 1
        self._notify()
