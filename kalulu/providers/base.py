from __future__ import annotations

from ..core.models import Result


class Provider:
    """Base provider.

    Static providers implement items(): the engine fuzzy-matches them.
    Dynamic providers implement query(): they score their own results.
    Set slow = True to run query() off the UI thread.
    """

    name = "provider"
    slow = False
    weight = 0.0          # added to every score from this provider
    prefixes: tuple[str, ...] = ()   # if set, only runs when the query starts with one
    delay_ms = 120        # debounce for slow providers
    min_len = 1           # slow providers skip shorter queries

    def __init__(self, ctx):
        self.ctx = ctx

    def items(self) -> list[Result]:
        return []

    def query(self, text: str) -> list[Result]:
        return []

    def refresh(self) -> None:
        pass
