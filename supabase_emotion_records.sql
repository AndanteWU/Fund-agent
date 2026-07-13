-- Supabase SQL for Fund Investor Emotion Management Agent
-- Run this in Supabase SQL Editor before deploying the emotion calendar persistence.

create table if not exists public.emotion_records (
  id text primary key,
  user_id text not null,
  record_date date not null,
  account_check_frequency text,
  strongest_emotion text,
  operation_impulse text,
  impulse_source text,
  actual_action text,
  anxiety_level integer default 0,
  fomo_level integer default 0,
  impulse_level integer default 0,
  note text,
  ai_emotion_label text,
  ai_risk_level text,
  ai_behavior_biases jsonb default '[]'::jsonb,
  ai_reminder text,
  ai_observation_point text,
  ai_analysis jsonb default '{}'::jsonb,
  created_at timestamptz default now(),
  updated_at timestamptz default now(),
  constraint emotion_records_user_date_unique unique (user_id, record_date)
);

create index if not exists idx_emotion_records_user_date
on public.emotion_records (user_id, record_date desc);

-- MVP note:
-- This Streamlit app writes data server-side and always filters by Supabase auth user_id in Python.
-- For a stricter production setup, enable RLS and pass the user's Supabase access token to the database client.
-- For the current Render MVP, keep RLS disabled on this table or use a server-side key that can write this table.
