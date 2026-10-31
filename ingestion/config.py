"""Initial route markets and daily flight search horizons for SmartFare."""

from datetime import date, timedelta

# Initial markets center on Cairo, matching the primary use case in the project spec.
# Route entries use IATA airport codes; hotel markets use destination city codes.
ROUTES: tuple[dict[str, str], ...] = (
    {"origin": "CAI", "destination": "LHR"},
    {"origin": "CAI", "destination": "CDG"},
    {"origin": "CAI", "destination": "FRA"},
    {"origin": "CAI", "destination": "FCO"},
    {"origin": "CAI", "destination": "IST"},
    {"origin": "CAI", "destination": "ATH"},
    {"origin": "CAI", "destination": "DXB"},
    {"origin": "CAI", "destination": "DOH"},
    {"origin": "CAI", "destination": "AUH"},
    {"origin": "CAI", "destination": "JED"},
    {"origin": "CAI", "destination": "RUH"},
    {"origin": "CAI", "destination": "AMM"},
    {"origin": "CAI", "destination": "JFK"},
    {"origin": "CAI", "destination": "YYZ"},
    {"origin": "CAI", "destination": "BOM"},
)

HOTEL_MARKETS: tuple[str, ...] = tuple(
    route["destination"] for route in ROUTES
)
HOTEL_STAR_TIERS: tuple[int, ...] = (3, 4, 5)
LENGTH_OF_STAY_NIGHTS: tuple[int, ...] = (3, 7, 14)


def lead_time_days(max_days: int = 180) -> tuple[int, ...]:
    """Return the planned horizon: daily 1–30d, every 3d through 90d, then weekly."""
    if max_days < 1:
        return ()

    days = set(range(1, min(max_days, 30) + 1))
    if max_days > 30:
        days.update(range(31, min(max_days, 90) + 1, 3))
    if max_days > 90:
        days.update(range(91, max_days + 1, 7))
    return tuple(sorted(days))


def departure_dates(observed_on: date, max_days: int = 180) -> tuple[date, ...]:
    """Map the lead-time grid to concrete departure dates for one collection day."""
    return tuple(observed_on + timedelta(days=lead) for lead in lead_time_days(max_days))
