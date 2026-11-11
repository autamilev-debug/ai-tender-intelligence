-- Run explicitly as the Beta table owner with psql --single-transaction.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

DO $migration$
DECLARE
    legacy_index regclass;
BEGIN
    IF lower(current_database()) IN ('p5_luna_eval', 'p5_luna_v3_batch', 'p5_semantic_eval') THEN
        RAISE EXCEPTION 'PROTECTED_EVALUATION_DATABASE';
    END IF;
    IF to_regclass('public.beta67_requests') IS NULL THEN
        RAISE EXCEPTION 'BETA_REQUEST_TABLE_MISSING';
    END IF;
    IF to_regclass('public.uq_beta68_one_analysis_account') IS NULL THEN
        RETURN;
    END IF;

    -- Fence the idle check and the index drop against new reservations.
    LOCK TABLE public.beta67_requests IN ACCESS EXCLUSIVE MODE;
    legacy_index := to_regclass('public.uq_beta68_one_analysis_account');
    IF legacy_index IS NULL THEN
        RETURN;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_index
        WHERE indexrelid = legacy_index AND indrelid = 'public.beta67_requests'::regclass AND indisunique
    ) THEN
        RAISE EXCEPTION 'UNEXPECTED_LIFETIME_INDEX';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_index
        WHERE indexrelid = to_regclass('public.uq_beta67_capacity')
          AND indrelid = 'public.beta67_requests'::regclass AND indisunique AND indisvalid
    ) THEN
        RAISE EXCEPTION 'BETA_PROCESSING_SLOT_INDEX_MISSING';
    END IF;
    IF EXISTS (SELECT 1 FROM public.beta67_requests WHERE state = 'RUNNING') THEN
        RAISE EXCEPTION 'BETA_PROCESSING_SLOT_BUSY';
    END IF;

    DROP INDEX IF EXISTS public.uq_beta68_one_analysis_account;
END
$migration$;
