"""Datatruck TMS adapter — registers the provider on import."""

from __future__ import annotations

from adapters.telematics.registry import register_provider

from .provider import DatatruckProvider

register_provider("datatruck", DatatruckProvider)

__all__ = ["DatatruckProvider"]
