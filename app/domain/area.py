""""Deal area": where the owner can quickly pick items up.

Statistics (medians, charts) always use the whole of Poland - more data, better prices. Only the
offers the bot LINKS to (hot notifications, "Okazje", fresh offers on the model card, the evening
digest, AI comparison) are limited to the chosen city + radius.

Distance uses the coordinates OLX returns for every offer (``map.lat/lon``; OLX blurs them by a few
km for privacy, so the radius is approximate). Offers without coordinates count as "in the area"
only when their city name equals the chosen city.
"""

from __future__ import annotations

from app.domain.matcher import haversine_km
from app.domain.text import normalize

# code -> (display name, lat, lon): city centres
CITIES: dict[str, tuple[str, float, float]] = {
    "krakow": ("Kraków", 50.0614, 19.9366),
    "warszawa": ("Warszawa", 52.2297, 21.0122),
    "wroclaw": ("Wrocław", 51.1079, 17.0385),
    "poznan": ("Poznań", 52.4064, 16.9252),
    "gdansk": ("Gdańsk", 54.3520, 18.6466),
    "lodz": ("Łódź", 51.7592, 19.4560),
    "katowice": ("Katowice", 50.2649, 19.0238),
    "lublin": ("Lublin", 51.2465, 22.5684),
    "szczecin": ("Szczecin", 53.4285, 14.5528),
    "bydgoszcz": ("Bydgoszcz", 53.1235, 18.0084),
    "rzeszow": ("Rzeszów", 50.0412, 21.9991),
    "bialystok": ("Białystok", 53.1325, 23.1688),
}
RADII = [10, 30, 50]


def city_name(code: str | None) -> str | None:
    return CITIES[code][0] if code in CITIES else None


def distance_km(user, listing) -> float | None:
    """Distance from the user's area centre, or None (no area chosen / no coordinates)."""
    code = getattr(user, "deal_city", None)
    if code not in CITIES or listing.latitude is None or listing.longitude is None:
        return None
    _, lat, lon = CITIES[code]
    return haversine_km(lat, lon, float(listing.latitude), float(listing.longitude))


def in_area(user, listing) -> bool:
    """True when no area is chosen (whole Poland) or the offer is within city + radius."""
    code = getattr(user, "deal_city", None)
    if code not in CITIES:
        return True
    radius = getattr(user, "deal_radius_km", None) or RADII[0]
    dist = distance_km(user, listing)
    if dist is not None:
        return dist <= radius
    return bool(listing.city) and normalize(listing.city) == normalize(CITIES[code][0])


def area_label(user) -> str | None:
    """'Kraków +30 km' or None for the whole of Poland."""
    name = city_name(getattr(user, "deal_city", None))
    if name is None:
        return None
    return f"{name} +{getattr(user, 'deal_radius_km', None) or RADII[0]} km"
