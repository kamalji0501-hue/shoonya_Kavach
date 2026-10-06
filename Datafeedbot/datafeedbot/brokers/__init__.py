"""Broker adapters package — plugin registry for multi-broker market data."""

from datafeedbot.brokers.base import MarketDataProvider
from datafeedbot.brokers.registry import (
    create_provider,
    list_providers,
    planned_providers,
    register_provider,
)

__all__ = [
    "MarketDataProvider",
    "create_provider",
    "list_providers",
    "planned_providers",
    "register_provider",
]
