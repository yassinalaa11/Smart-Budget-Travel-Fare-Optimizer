-- Seed-backed reference dimensions used by collection and analytics.
CREATE TABLE IF NOT EXISTS public.dim_routes (
    route_id                    text PRIMARY KEY,
    origin_iata                 text NOT NULL CHECK (origin_iata ~ '^[A-Z]{3}$'),
    destination_iata            text NOT NULL CHECK (destination_iata ~ '^[A-Z]{3}$'),
    origin_city                 text NOT NULL,
    destination_city            text NOT NULL,
    distance_km                 integer,
    is_international            boolean NOT NULL DEFAULT true,
    typical_flight_duration_min integer,
    is_active                   boolean NOT NULL DEFAULT true,
    CONSTRAINT dim_routes_distinct_od CHECK (origin_iata <> destination_iata),
    CONSTRAINT dim_routes_distance_positive CHECK (distance_km IS NULL OR distance_km > 0),
    CONSTRAINT dim_routes_duration_positive CHECK (
        typical_flight_duration_min IS NULL OR typical_flight_duration_min > 0
    ),
    CONSTRAINT dim_routes_unique_od UNIQUE (origin_iata, destination_iata)
);

CREATE TABLE IF NOT EXISTS public.dim_hotels (
    hotel_market_id  text PRIMARY KEY,
    destination_iata text NOT NULL CHECK (destination_iata ~ '^[A-Z]{3}$'),
    city_name        text NOT NULL,
    star_tier        smallint NOT NULL CHECK (star_tier BETWEEN 1 AND 5),
    market_category  text NOT NULL,
    is_active        boolean NOT NULL DEFAULT true,
    CONSTRAINT dim_hotels_unique_market_tier
        UNIQUE (destination_iata, star_tier, market_category)
);

CREATE TABLE IF NOT EXISTS public.dim_calendar (
    date_key                     date PRIMARY KEY,
    day_of_week                  smallint NOT NULL CHECK (day_of_week BETWEEN 1 AND 7),
    is_weekend                   boolean NOT NULL,
    month_num                    smallint NOT NULL CHECK (month_num BETWEEN 1 AND 12),
    season_name                  text NOT NULL CHECK (
        season_name IN ('winter', 'spring', 'summer', 'autumn')
    ),
    is_origin_public_holiday      boolean,
    is_destination_public_holiday boolean,
    school_break_flag             boolean
);

CREATE INDEX IF NOT EXISTS dim_hotels_destination_tier_idx
    ON public.dim_hotels (destination_iata, star_tier);
