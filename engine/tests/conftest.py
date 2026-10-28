from pathlib import Path

import pytest

from engine import Engine, InMemorySiteConfigStore
from engine.fetchers import FixtureFetcher

FIXTURES = Path(__file__).parent / "fixtures"


class FakeTime:
    """Deterministic clock: sleeping advances time and is recorded."""

    def __init__(self):
        self.t = 0.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


class SequenceFetcher:
    """Returns the queued (status, html) responses in order, repeating the last."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def get(self, url, **kwargs):
        from engine.fetchers import FetchResponse

        item = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        status, text = item
        return FetchResponse(status, text, url)


@pytest.fixture
def pages():
    return {p.stem: p.read_text(encoding="utf-8") for p in FIXTURES.glob("*.html")}


@pytest.fixture
def store():
    return InMemorySiteConfigStore()


@pytest.fixture
def fake_time():
    return FakeTime()


def make_engine(fetcher, store=None, fake_time=None):
    ft = fake_time or FakeTime()
    return Engine(
        config_store=store,
        fetcher=fetcher,
        sleep=ft.sleep,
        clock=ft.now,
        jitter=lambda: 0.0,
    )
