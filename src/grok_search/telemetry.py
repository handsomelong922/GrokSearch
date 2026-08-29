"""Low-overhead, request-scoped timing instrumentation for GrokSearch."""

import contextvars
import time
import uuid

from .providers import openai_compatible
from .providers import router as router_module


_REQUEST_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "grok_search_telemetry_request_id", default=None
)
_LAST_TIMING: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "grok_search_telemetry_last_timing", default=None
)
_PROVIDER_TIMINGS: dict[str, dict[str, float]] = {}
_INSTALLED = False


def _elapsed_ms(started: float) -> float:
    return round(max(time.perf_counter() - started, 0.0) * 1000.0, 2)


def reset_last_search_timing() -> None:
    _LAST_TIMING.set({})


def get_last_search_timing() -> dict:
    return dict(_LAST_TIMING.get() or {})


def install_search_telemetry() -> None:
    """Install additive wrappers once without changing provider/router semantics."""
    global _INSTALLED
    if _INSTALLED:
        return

    provider_cls = openai_compatible.OpenAICompatibleSearchProvider
    original_provider_search = provider_cls.search
    original_run_search = router_module.ProviderRouter.run_search

    async def timed_provider_search(self, *args, **kwargs):
        started = time.perf_counter()
        try:
            return await original_provider_search(self, *args, **kwargs)
        finally:
            request_id = _REQUEST_ID.get()
            if request_id:
                provider_name = self.get_provider_name()
                bucket = _PROVIDER_TIMINGS.setdefault(request_id, {})
                bucket[provider_name] = _elapsed_ms(started)

    async def timed_run_search(self, *args, **kwargs):
        request_id = uuid.uuid4().hex
        token = _REQUEST_ID.set(request_id)
        _PROVIDER_TIMINGS[request_id] = {}
        started = time.perf_counter()
        try:
            result = await original_run_search(self, *args, **kwargs)
            providers_ms = dict(_PROVIDER_TIMINGS.get(request_id, {}))
            router_ms = _elapsed_ms(started)
            result.provider_timings_ms = providers_ms
            result.router_elapsed_ms = router_ms
            _LAST_TIMING.set({
                "provider_router_ms": router_ms,
                "providers_ms": providers_ms,
            })
            return result
        finally:
            _PROVIDER_TIMINGS.pop(request_id, None)
            _REQUEST_ID.reset(token)

    provider_cls.search = timed_provider_search
    router_module.ProviderRouter.run_search = timed_run_search
    _INSTALLED = True
