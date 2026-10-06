"""Plugin-style broker adapter registry.

Built-in: dhan, zerodha, shoonya.
Reserved slots (stubs): angel, fyers, upstox, ibkr — register when ready.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from datafeedbot.brokers.base import MarketDataProvider

logger = logging.getLogger("datafeedbot.brokers.registry")

ProviderFactory = Callable[..., MarketDataProvider]

_REGISTRY: dict[str, ProviderFactory] = {}
_PLANNED = ("angel", "fyers", "upstox", "ibkr")


def register_provider(name: str, factory: ProviderFactory) -> None:
    key = name.strip().lower()
    if not key:
        raise ValueError("provider name required")
    _REGISTRY[key] = factory
    logger.debug("registered broker provider %s", key)


def get_provider_factory(name: str) -> ProviderFactory | None:
    return _REGISTRY.get(name.strip().lower())


def list_providers() -> list[str]:
    return sorted(_REGISTRY.keys())


def planned_providers() -> tuple[str, ...]:
    return _PLANNED


def create_provider(name: str, **kwargs: Any) -> MarketDataProvider:
    factory = get_provider_factory(name)
    if factory is None:
        raise KeyError(
            f"unknown broker provider {name!r}; "
            f"registered={list_providers()} planned={list(_PLANNED)}"
        )
    return factory(**kwargs)


def _register_builtins() -> None:
    if _REGISTRY:
        return
    from datafeedbot.brokers.dhan.provider import DhanMarketDataProvider
    from datafeedbot.brokers.shoonya.provider import ShoonyaMarketDataProvider
    from datafeedbot.brokers.zerodha.provider import ZerodhaMarketDataProvider

    register_provider("dhan", DhanMarketDataProvider)
    register_provider("zerodha", ZerodhaMarketDataProvider)
    register_provider("shoonya", ShoonyaMarketDataProvider)

    def _stub(name: str) -> ProviderFactory:
        def factory(**_kwargs: Any) -> MarketDataProvider:
            raise NotImplementedError(
                f"broker provider {name!r} is reserved but not implemented yet"
            )

        return factory

    for planned in _PLANNED:
        if planned not in _REGISTRY:
            register_provider(planned, _stub(planned))


_register_builtins()
