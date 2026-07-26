-- Self-hosting schema snapshot — matches what runs behind burninpublic.com.
-- Only salted place hashes and daily rollups ever land here; raw MACs,
-- location samples, and per-request rows stay on the collector machine.
--
-- Every row belongs to a user. Row-level security means an authenticated
-- user can only ever read or write their own rows; anonymous access is
-- not granted at all.

-- ============ usage rollups ============
-- Buckets are UTC (day, hour). Hour granularity is what lets the dashboard
-- regroup activity into the *viewer's* timezone — a day-level aggregate can
-- never be re-cut across a timezone boundary, and the collector machine's
-- timezone is not necessarily the one you live in.
CREATE TABLE public.aiwork_daily (
    user_id               UUID NOT NULL,
    day                   DATE NOT NULL,    -- UTC date
    hour                  SMALLINT NOT NULL, -- UTC hour, 0..23
    place_hash            TEXT,             -- sha256(salt:place_key)[:16], null = unknown
    source                TEXT NOT NULL,    -- claude | codex
    model                 TEXT NOT NULL DEFAULT '',
    requests              INTEGER NOT NULL DEFAULT 0,
    input_tokens          BIGINT  NOT NULL DEFAULT 0,
    output_tokens         BIGINT  NOT NULL DEFAULT 0,
    cache_read_tokens     BIGINT  NOT NULL DEFAULT 0,
    cache_creation_tokens BIGINT  NOT NULL DEFAULT 0,
    active_minutes        INTEGER NOT NULL DEFAULT 0,
    uploaded_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT aiwork_daily_hour_range CHECK (hour BETWEEN 0 AND 23),
    CONSTRAINT aiwork_daily_unique
        UNIQUE NULLS NOT DISTINCT (user_id, day, hour, place_hash, source, model)
);
ALTER TABLE public.aiwork_daily ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE ON public.aiwork_daily TO authenticated;
CREATE POLICY aiwork_daily_own ON public.aiwork_daily
    FOR ALL TO authenticated
    USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());

-- ============ named places (labels + coords the user chose to sync) ============
CREATE TABLE public.aiwork_places (
    user_id    UUID NOT NULL,
    place_hash TEXT NOT NULL,
    label      TEXT NOT NULL,
    kind       TEXT,
    lat        DOUBLE PRECISION,
    lon        DOUBLE PRECISION,
    geo_name   TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, place_hash)
);
ALTER TABLE public.aiwork_places ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE ON public.aiwork_places TO authenticated;
CREATE POLICY aiwork_places_own ON public.aiwork_places
    FOR ALL TO authenticated
    USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());

-- ============ metric views (run as the querying user so RLS applies) ============
-- `day` in these views is the UTC day. The dashboard does not use them; it
-- reads (day, hour) from the table and regroups in the viewer's timezone.
CREATE VIEW public.v_tokens_by_place WITH (security_invoker = true) AS
SELECT day, place_hash,
       SUM(input_tokens + output_tokens
           + cache_read_tokens + cache_creation_tokens) AS total_tokens,
       SUM(output_tokens) AS output_tokens,
       SUM(requests)      AS requests
FROM public.aiwork_daily GROUP BY day, place_hash;

CREATE VIEW public.v_active_time_by_place WITH (security_invoker = true) AS
SELECT day, place_hash, SUM(active_minutes) AS active_minutes
FROM public.aiwork_daily GROUP BY day, place_hash;

CREATE VIEW public.v_model_mix WITH (security_invoker = true) AS
SELECT day, source, model,
       SUM(input_tokens + output_tokens
           + cache_read_tokens + cache_creation_tokens) AS total_tokens,
       SUM(requests) AS requests
FROM public.aiwork_daily GROUP BY day, source, model;

GRANT SELECT ON public.v_tokens_by_place,
                public.v_active_time_by_place,
                public.v_model_mix TO authenticated;
