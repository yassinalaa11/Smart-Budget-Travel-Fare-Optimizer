create table if not exists public.click_events (
    click_id            uuid primary key default gen_random_uuid(),
    sub_id              text not null unique,
    route_id            text not null,
    origin_iata         char(3) not null,
    destination_iata    char(3) not null,
    departure_date      date not null,
    return_date         date,
    partner             text not null default 'aviasales',
    target_url          text not null,
    quoted_price        numeric(12, 2),
    quoted_currency     char(3),
    source_tab          text not null default 'booking_advisor',
    clicked_at          timestamptz not null default now(),
    converted_at        timestamptz,
    booking_value_usd   numeric(12, 2),
    commission_usd      numeric(12, 2),
    conversion_status   text not null default 'clicked',
    constraint click_events_status_check check (
        conversion_status in ('clicked', 'pending', 'confirmed', 'cancelled')
    )
);

create index if not exists click_events_route_clicked_idx
    on public.click_events (route_id, clicked_at desc);

create index if not exists click_events_status_clicked_idx
    on public.click_events (conversion_status, clicked_at desc);

create table if not exists public.price_alerts (
    alert_id                uuid primary key default gen_random_uuid(),
    route_id                text not null,
    departure_date          date not null,
    return_date             date,
    target_price_usd        numeric(12, 2) not null,
    channel                 text not null default 'all',
    contact                 text,
    is_active               boolean not null default true,
    created_at              timestamptz not null default now(),
    last_checked_at         timestamptz,
    last_triggered_at       timestamptz,
    last_observed_price_usd numeric(12, 2),
    trigger_count           integer not null default 0,
    constraint price_alerts_target_positive check (target_price_usd > 0),
    constraint price_alerts_channel_check check (
        channel in ('all', 'slack', 'telegram', 'email')
    )
);

create index if not exists price_alerts_active_route_idx
    on public.price_alerts (is_active, route_id, departure_date);
