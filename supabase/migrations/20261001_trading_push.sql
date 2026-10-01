create extension if not exists pgcrypto;

create table if not exists public.trading_push_subscriptions (
  id uuid primary key default gen_random_uuid(),
  endpoint text not null unique,
  p256dh text not null,
  auth text not null,
  device_id text not null,
  locale text not null default 'pl',
  preferences jsonb not null default '{"channels":{"daily":true,"weekly":true,"stock":true},"events":{"open":true,"close":true}}'::jsonb,
  access_tier text not null default 'PUBLIC' check (access_tier in ('PUBLIC','AUTHENTICATED','PAID')),
  user_id uuid null,
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now()
);

create index if not exists trading_push_subscriptions_active_idx
  on public.trading_push_subscriptions (is_active)
  where is_active = true;

create index if not exists trading_push_subscriptions_device_idx
  on public.trading_push_subscriptions (device_id);

create table if not exists public.trading_push_deliveries (
  id uuid primary key default gen_random_uuid(),
  event_id text not null,
  subscription_id uuid not null references public.trading_push_subscriptions(id) on delete cascade,
  engine text not null,
  event_type text not null,
  status text not null check (status in ('PENDING','SENT','EXPIRED','FAILED','CLICKED')),
  http_status integer null,
  error_code text null,
  created_at timestamptz not null default now(),
  sent_at timestamptz null,
  clicked_at timestamptz null,
  unique(event_id, subscription_id)
);

create index if not exists trading_push_deliveries_event_idx
  on public.trading_push_deliveries (event_id);
create index if not exists trading_push_deliveries_created_idx
  on public.trading_push_deliveries (created_at desc);

alter table public.trading_push_subscriptions enable row level security;
alter table public.trading_push_deliveries enable row level security;

-- No public table policies are intentionally created.
-- Browser writes go only through Edge Functions; service-role functions own DB mutation.
