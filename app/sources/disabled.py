"""Adapters for real marketplaces whose automated monitoring is NOT enabled.

Reason (details and sources in docs/SOURCES.md):
* OLX.pl — Regulamin Serwisu OLX.pl, pt. 2.3, prohibits aggregating and processing data
  available in the service to pass it on to third parties; the OLX Partner API is meant for
  managing one's own adverts, not for searching the market.
* Allegro Lokalnie — Allegro REST API rules, pt. 4.1(e), prohibit extracting (scraping)
  Allegro materials without consent; no public API for searching Lokalnie was found.

The adapters keep the common interface so the rest of the system (search wizard, scheduler,
admin panel) already works with them. ``fetch`` always raises ``SourceDisabled``; it never
sends a request to the marketplace. To enable a source, implement ``fetch`` on top of an
authorised access method (written consent / official API) using ``app.sources.http``.
"""

from __future__ import annotations

from app.domain.models import Listing, SearchCriteria
from app.sources.base import SourceAdapter, SourceCapabilities, SourceDisabled


class _DisabledMarketplace(SourceAdapter):
    reason: str = ""

    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            keywords=True, price=True, category=True, condition=True, location=True,
            radius=True, delivery=True, sort_newest=True,
            notes=["Monitoring wyłączony do czasu potwierdzenia legalnego dostępu."],
        )

    async def fetch(self, criteria: SearchCriteria, limit: int = 50) -> list[Listing]:
        raise SourceDisabled(self.reason)


class OlxSource(_DisabledMarketplace):
    code = "olx"
    display_name = "OLX.pl"
    reason = "OLX.pl: regulamin pkt 2.3 zabrania agregowania danych serwisu (docs/SOURCES.md)"


class AllegroLokalnieSource(_DisabledMarketplace):
    code = "allegro_lokalnie"
    display_name = "Allegro Lokalnie"
    reason = "Allegro: zasady pkt 4.1(e) zabraniają scrapingu bez zgody (docs/SOURCES.md)"
