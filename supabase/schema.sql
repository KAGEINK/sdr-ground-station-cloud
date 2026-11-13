-- ============================================================
-- SDR Weather Satellite Ground Station - Supabase schema
-- Run once: Supabase Dashboard -> SQL Editor -> New query -> Run
-- Safe to re-run (idempotent).
-- ============================================================

-- 1) One row per satellite pass -------------------------------
create table if not exists public.passes (
    id            uuid primary key default gen_random_uuid(),
    folder_name   text not null unique,          -- SatDump output folder (dedup key)
    satellite     text not null default 'METEOR-M2',
    pass_time     timestamptz not null,          -- start of reception (UTC)
    frequency_mhz numeric(8,3),
    station       text,
    notes         text,
    created_at    timestamptz not null default now()
);

-- 2) One row per decoded image --------------------------------
create table if not exists public.images (
    id           uuid primary key default gen_random_uuid(),
    pass_id      uuid not null references public.passes(id) on delete cascade,
    product      text not null,                  -- e.g. "MSU-MR / msu_mr_rgb_221"
    file_name    text not null,
    storage_path text not null unique,           -- key inside the bucket (dedup key)
    public_url   text not null,
    thumb_url    text,
    size_bytes   bigint,
    width        integer,
    height       integer,
    created_at   timestamptz not null default now()
);

create index if not exists passes_pass_time_idx on public.passes (pass_time desc);
create index if not exists images_pass_id_idx   on public.images (pass_id);

-- 3) Row Level Security ---------------------------------------
-- The dashboard uses the public (anon / publishable) key: READ ONLY.
-- The station uploader uses the secret (service_role) key, which bypasses RLS.
alter table public.passes enable row level security;
alter table public.images enable row level security;

drop policy if exists "public read passes" on public.passes;
create policy "public read passes" on public.passes
    for select to anon, authenticated using (true);

drop policy if exists "public read images" on public.images;
create policy "public read images" on public.images
    for select to anon, authenticated using (true);

-- 4) Public storage bucket for the images ---------------------
insert into storage.buckets (id, name, public)
values ('satellite-images', 'satellite-images', true)
on conflict (id) do update set public = true;

-- 5) Explicit grants (needed when "Automatically expose new tables" is off)
grant usage on schema public to anon, authenticated, service_role;
grant select on public.passes, public.images to anon, authenticated;
grant all on public.passes, public.images to service_role;
