"""Tests for shared async progress helpers."""

import asyncio

import pytest

from skene import progress


class TestProgressHelpers:
    @pytest.mark.asyncio
    async def test_run_with_progress_returns_awaitable_result(self):
        """Returns the wrapped awaitable result after progress handling."""

        async def _compute() -> str:
            await asyncio.sleep(0)
            return "ok"

        result = await progress.run_with_progress(_compute())
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_show_progress_indicator_stops_when_event_set(self, monkeypatch):
        """Stops the indicator loop cleanly when stop_event is signaled."""
        printed: list[tuple[tuple[object, ...], dict[str, object]]] = []
        monkeypatch.setattr(progress.console, "print", lambda *args, **kwargs: printed.append((args, kwargs)))

        stop_event = asyncio.Event()
        task = asyncio.create_task(progress.show_progress_indicator(stop_event))
        await asyncio.sleep(0.01)
        stop_event.set()
        await task

        assert printed
