from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.domain.evidence import EvidenceRecord, FactType
from app.domain.intent import RouteIntent
from app.domain.routes import RouteCandidate


class SearchProvider(Protocol):
    async def search(self, intent: RouteIntent, *, limit: int) -> list[RouteCandidate]: ...


class EvidenceProvider(Protocol):
    async def collect(
        self,
        route_ids: Sequence[str],
        fact_types: Sequence[FactType],
    ) -> list[EvidenceRecord]: ...


class ProviderErrorKind:
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"
    INVALID_RESPONSE = "invalid_response"


@dataclass(frozen=True, slots=True)
class FactProviderMetadata:
    name: str
    version: str
    fact_types: tuple[FactType, ...]
    read_only: bool = True
    max_batch_size: int = 20
    timeout_seconds: float = 3.0


FACT_PROVIDER_CATALOG: tuple[FactProviderMetadata, ...] = (
    FactProviderMetadata(
        name="local_route_search",
        version="v1",
        fact_types=tuple(FactType),
        max_batch_size=50,
        timeout_seconds=2.0,
    ),
)
