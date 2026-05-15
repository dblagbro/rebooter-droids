"""EPG (TV programming guide) cache — v0.5.64 (B17 Layer 2).

`ExternalEpgCache` is a provider-agnostic cache of "what's airing on
which channel at which time", refreshed on a schedule from a TV-guide
provider. v1 ships the TVMaze provider only (free, no auth); the
`provider` column keeps the door open for Schedules Direct later
without a migration.

The `epg_show_airing` watchdog probe (`watchdog_runtime/_probes.py`)
queries this table for "is show X airing right now".

The design's companion `epg_channel_mappings` table (operator-friendly
"Spectrum 27 = ABC" labels) is deferred — the v1 probe matches the
network name directly. See
`docs/notes/2026-05-15-b17-layer2-epg-design.md` §5.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models._helpers import ts_column

# Recognized EPG providers. v1 = TVMaze only.
EPG_PROVIDERS = ("tvmaze",)


class ExternalEpgCache(Base):
    __tablename__ = "external_epg_cache"

    # SQLite test-path variant — see DeviceHeartbeat for the rationale.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        primary_key=True,
        autoincrement=True,
    )

    # "tvmaze" (later "schedulesdirect"). Matches an entry in EPG_PROVIDERS.
    provider: Mapped[str] = mapped_column(String(40), nullable=False)

    # Provider-native channel id. For TVMaze this is the network name
    # ("ABC", "GSN"); the probe matches against it directly.
    channel_id: Mapped[str] = mapped_column(String(80), nullable=False)

    # UTC airing window — half-open [start, end). End is mandatory; if a
    # provider returns only a duration we compute the end before insert.
    airing_start: Mapped[datetime] = ts_column(default_now=False, nullable=False)
    airing_end: Mapped[datetime] = ts_column(default_now=False, nullable=False)

    # show_title is the probe's search key. Episode metadata is for the
    # events log ("fired on S40E142") — not load-bearing for matching.
    show_title: Mapped[str] = mapped_column(String(300), nullable=False)
    episode_title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    season: Mapped[int | None] = mapped_column(Integer, nullable=True)
    episode: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Provider bag-of-extras (genre, runtime, …), capped ~2 KiB at insert.
    extra: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    fetched_at: Mapped[datetime] = ts_column()


# "What's on channel X right now": (provider, channel_id, airing_start)
# covers the equality + range predicates and the ORDER BY.
Index(
    "ix_epg_cache_lookup",
    ExternalEpgCache.provider,
    ExternalEpgCache.channel_id,
    ExternalEpgCache.airing_start.desc(),
)
# Janitor cleanup by airing_end.
Index("ix_epg_cache_airing_end", ExternalEpgCache.airing_end)
# Prevent dupes when a refresh runs twice for the same window.
UniqueConstraint(
    ExternalEpgCache.provider,
    ExternalEpgCache.channel_id,
    ExternalEpgCache.airing_start,
    ExternalEpgCache.show_title,
    name="uq_epg_cache_provider_channel_start_show",
)
