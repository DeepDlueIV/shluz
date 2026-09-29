begin;

create or replace function public.shluz_set_updated_at()
returns trigger
language plpgsql
security invoker
set search_path = public
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

create table if not exists public.accounts (
    id uuid primary key default gen_random_uuid(),
    display_name varchar(200) not null default 'Без имени',
    role varchar(32) not null default 'user',
    status varchar(32) not null default 'active',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.account_balances (
    account_id uuid primary key references public.accounts(id) on delete cascade,
    available_credits numeric(20, 6) not null default 0 check (available_credits >= 0),
    reserved_credits numeric(20, 6) not null default 0 check (reserved_credits >= 0),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.identities (
    id uuid primary key default gen_random_uuid(),
    account_id uuid not null references public.accounts(id) on delete cascade,
    kind varchar(32) not null,
    external_id varchar(255) not null,
    label varchar(255),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint uq_identity_kind_external_id unique (kind, external_id)
);

create table if not exists public.plans (
    id uuid primary key default gen_random_uuid(),
    code varchar(64) not null unique,
    name varchar(120) not null,
    currency varchar(3) not null default 'RUB',
    monthly_price_minor bigint not null default 0 check (monthly_price_minor >= 0),
    included_credits numeric(20, 6) not null default 0 check (included_credits >= 0),
    entitlements jsonb not null default '{}'::jsonb,
    usage_limits jsonb not null default '{}'::jsonb,
    is_active boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.subscriptions (
    id uuid primary key default gen_random_uuid(),
    account_id uuid not null references public.accounts(id) on delete cascade,
    plan_id uuid not null references public.plans(id) on delete restrict,
    status varchar(32) not null default 'active',
    starts_at timestamptz not null default now(),
    ends_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint ck_subscription_period check (ends_at is null or ends_at > starts_at)
);

create table if not exists public.api_tokens (
    id uuid primary key default gen_random_uuid(),
    account_id uuid not null references public.accounts(id) on delete cascade,
    name varchar(120) not null,
    token_prefix varchar(32) not null,
    token_hash varchar(255) not null unique,
    scopes jsonb not null default '[]'::jsonb,
    status varchar(32) not null default 'active',
    expires_at timestamptz,
    last_used_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.usage_events (
    id uuid primary key default gen_random_uuid(),
    account_id uuid references public.accounts(id) on delete set null,
    channel varchar(64) not null default 'api',
    provider varchar(64) not null,
    model varchar(255) not null,
    provider_request_id varchar(255),
    status varchar(32) not null default 'pending',
    error_code varchar(120),
    prompt_tokens bigint not null default 0 check (prompt_tokens >= 0),
    completion_tokens bigint not null default 0 check (completion_tokens >= 0),
    cost_usd numeric(20, 8) not null default 0 check (cost_usd >= 0),
    billed_credits numeric(20, 6) not null default 0 check (billed_credits >= 0),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.audit_events (
    id uuid primary key default gen_random_uuid(),
    actor_account_id uuid references public.accounts(id) on delete set null,
    action varchar(120) not null,
    object_type varchar(120) not null,
    object_id varchar(255),
    details jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create index if not exists ix_accounts_role on public.accounts(role);
create index if not exists ix_accounts_status on public.accounts(status);
create index if not exists ix_identities_account_id on public.identities(account_id);
create index if not exists ix_identities_account_kind on public.identities(account_id, kind);
create index if not exists ix_plans_is_active on public.plans(is_active);
create index if not exists ix_subscriptions_account_id on public.subscriptions(account_id);
create index if not exists ix_subscriptions_plan_id on public.subscriptions(plan_id);
create index if not exists ix_subscriptions_account_status on public.subscriptions(account_id, status);
create index if not exists ix_api_tokens_account_id on public.api_tokens(account_id);
create index if not exists ix_api_tokens_token_prefix on public.api_tokens(token_prefix);
create index if not exists ix_api_tokens_account_status on public.api_tokens(account_id, status);
create index if not exists ix_usage_events_account_id on public.usage_events(account_id);
create index if not exists ix_usage_events_provider_request_id on public.usage_events(provider_request_id);
create index if not exists ix_usage_events_created_status on public.usage_events(created_at, status);
create index if not exists ix_usage_events_account_created on public.usage_events(account_id, created_at);
create index if not exists ix_usage_events_provider_model on public.usage_events(provider, model);
create index if not exists ix_audit_events_actor_account_id on public.audit_events(actor_account_id);
create index if not exists ix_audit_events_created_action on public.audit_events(created_at, action);

-- Keep updated_at reliable even when a future administrative tool writes directly.
do $$
declare
    table_name text;
begin
    foreach table_name in array array[
        'accounts',
        'account_balances',
        'identities',
        'plans',
        'subscriptions',
        'api_tokens',
        'usage_events'
    ]
    loop
        execute format('drop trigger if exists shluz_set_updated_at on public.%I', table_name);
        execute format(
            'create trigger shluz_set_updated_at before update on public.%I '
            'for each row execute function public.shluz_set_updated_at()',
            table_name
        );
    end loop;
end;
$$;

-- No browser/mobile client gets direct table access in the first version.
alter table public.accounts enable row level security;
alter table public.account_balances enable row level security;
alter table public.identities enable row level security;
alter table public.plans enable row level security;
alter table public.subscriptions enable row level security;
alter table public.api_tokens enable row level security;
alter table public.usage_events enable row level security;
alter table public.audit_events enable row level security;

-- Supabase roles are granted/revoked only when they exist, so the same migration
-- remains usable on an ordinary PostgreSQL server.
do $$
declare
    client_role text;
begin
    foreach client_role in array array['anon', 'authenticated']
    loop
        if exists (select 1 from pg_roles where rolname = client_role) then
            execute format(
                'revoke all on table public.accounts, public.account_balances, '
                'public.identities, public.plans, public.subscriptions, '
                'public.api_tokens, public.usage_events, public.audit_events from %I',
                client_role
            );
        end if;
    end loop;

    if exists (select 1 from pg_roles where rolname = 'service_role') then
        grant usage on schema public to service_role;
        grant all on table
            public.accounts,
            public.account_balances,
            public.identities,
            public.plans,
            public.subscriptions,
            public.api_tokens,
            public.usage_events,
            public.audit_events
        to service_role;
    end if;
end;
$$;

comment on table public.api_tokens is
    'Stores only token prefixes and cryptographic hashes; never store a raw token.';
comment on table public.usage_events is
    'One row per model request, created before the provider call and finalized afterwards.';

commit;
