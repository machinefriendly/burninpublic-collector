-- Remote tables + the three metric views. Only salted hashes and daily
-- rollups ever land here — raw MACs and per-request rows stay local.

CREATE TABLE public.aiwork_daily (
    day                   DATE NOT NULL,
    place_hash            TEXT,             -- sha256(salt:mac)[:16], null = unknown
    source                TEXT NOT NULL,    -- claude | codex
    model                 TEXT NOT NULL DEFAULT '',
    requests              INTEGER NOT NULL DEFAULT 0,
    input_tokens          BIGINT  NOT NULL DEFAULT 0,
    output_tokens         BIGINT  NOT NULL DEFAULT 0,
    cache_read_tokens     BIGINT  NOT NULL DEFAULT 0,
    cache_creation_tokens BIGINT  NOT NULL DEFAULT 0,
    active_minutes        INTEGER NOT NULL DEFAULT 0,
    uploaded_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (day, place_hash, source, model)
);

ALTER TABLE public.aiwork_daily ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE ON public.aiwork_daily TO authenticated;
GRANT ALL ON public.aiwork_daily TO service_role;

CREATE POLICY aiwork_daily_service_all ON public.aiwork_daily
    FOR ALL TO service_role USING (true) WITH CHECK (true);

-- View 1: daily token totals per place
CREATE VIEW public.v_tokens_by_place AS
SELECT day, place_hash,
       SUM(input_tokens + output_tokens
           + cache_read_tokens + cache_creation_tokens) AS total_tokens,
       SUM(output_tokens) AS output_tokens,
       SUM(requests)      AS requests
FROM public.aiwork_daily
GROUP BY day, place_hash;

-- View 2: daily active time per place
CREATE VIEW public.v_active_time_by_place AS
SELECT day, place_hash, SUM(active_minutes) AS active_minutes
FROM public.aiwork_daily
GROUP BY day, place_hash;

-- View 3: model mix per day
CREATE VIEW public.v_model_mix AS
SELECT day, source, model,
       SUM(input_tokens + output_tokens
           + cache_read_tokens + cache_creation_tokens) AS total_tokens,
       SUM(requests) AS requests
FROM public.aiwork_daily
GROUP BY day, source, model;
