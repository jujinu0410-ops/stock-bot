"""Minimal boundary for narrative providers; no additional provider is connected."""
from typing import Protocol, Any


class AnalystProvider(Protocol):
    def generate(self, prompt: str) -> dict[str, Any]: ...
