create table if not exists public.fx_rates (
    rate_date       date not null,
    base_currency   char(3) not null,
    quote_currency  char(3) not null,
    rate            numeric(18, 6) not null,
    source          text not null default 'open.er-api.com',
    fetched_at      timestamptz not null default now(),
    constraint fx_rates_base_format check (base_currency ~ '^[A-Z]{3}$'),
    constraint fx_rates_quote_format check (quote_currency ~ '^[A-Z]{3}$'),
    constraint fx_rates_positive check (rate > 0),
    primary key (rate_date, base_currency, quote_currency)
);

create index if not exists fx_rates_quote_date_idx
    on public.fx_rates (quote_currency, rate_date desc);
