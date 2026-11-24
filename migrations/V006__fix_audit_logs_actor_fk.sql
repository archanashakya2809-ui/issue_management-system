-- Migration: V006__fix_audit_logs_actor_fk
-- Purpose : Fix FK constraint on audit_logs that prevents System Admin from
--           writing cross-company audit events.
--
-- Root cause
-- ----------
-- The existing constraint is:
--
--   FOREIGN KEY (company_id, user_id) REFERENCES users(company_id, id)
--     ON DELETE SET NULL (user_id)
--
-- This composite FK requires the audit actor (user_id) to exist in the users
-- table with the SAME company_id as the audit log.  For same-company
-- operations this is correct, but the System Admin belongs to the System Core
-- company (11111111-...) and writes audit logs into tenant companies.
--
-- app_write_audit() already enforces the correct business rule:
--   - Non-system users may only audit their own company.
--   - System admins may audit any company.
-- That PL/pgSQL check passes, but the table-level FK then rejects the INSERT
-- because (Hudsmer_id, SysAdmin_id) has no matching row in users.
--
-- Fix
-- ---
-- Drop the composite FK (which couples the actor to the audited company).
-- Replace it with a simple FK on user_id -> users(id) so referential integrity
-- is preserved (the actor must be a real, existing user) without restricting
-- which company the audit record belongs to.
--
-- Security impact: NONE.  The app_write_audit() SECURITY DEFINER function
-- continues to enforce the single-company rule for all non-system-admin
-- callers at the PL/pgSQL level.  The FK change only relaxes the implicit
-- "same company" coupling for the actor column.
--
-- Rollback strategy (safe to run if this must be reverted):
--
--   ALTER TABLE public.audit_logs DROP CONSTRAINT IF EXISTS fk_audit_actor;
--   ALTER TABLE public.audit_logs
--     ADD CONSTRAINT fk_audit_actor_company
--     FOREIGN KEY (company_id, user_id)
--     REFERENCES public.users(company_id, id)
--     ON DELETE SET NULL (user_id);
--
-- NOTE: the rollback will fail for any existing audit rows written by system
-- admins into non-platform companies.  Truncate or delete those rows first
-- if a rollback is ever necessary.

BEGIN;

-- Step 1: drop the composite FK
ALTER TABLE public.audit_logs
    DROP CONSTRAINT IF EXISTS fk_audit_actor_company;

-- Step 2: add a simple FK that validates the actor is a real user
--         without coupling them to the audited company
ALTER TABLE public.audit_logs
    ADD CONSTRAINT fk_audit_actor
    FOREIGN KEY (user_id)
    REFERENCES public.users(id)
    ON DELETE SET NULL;

COMMIT;

-- Verification
DO $$
DECLARE
    v_old_exists boolean;
    v_new_exists boolean;
BEGIN
    SELECT EXISTS(
        SELECT 1 FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        WHERE t.relname = 'audit_logs' AND c.conname = 'fk_audit_actor_company'
    ) INTO v_old_exists;

    SELECT EXISTS(
        SELECT 1 FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        WHERE t.relname = 'audit_logs' AND c.conname = 'fk_audit_actor'
    ) INTO v_new_exists;

    IF v_old_exists THEN
        RAISE EXCEPTION 'V006 failed: old constraint fk_audit_actor_company still present';
    END IF;
    IF NOT v_new_exists THEN
        RAISE EXCEPTION 'V006 failed: new constraint fk_audit_actor not found';
    END IF;

    RAISE NOTICE 'V006 applied: fk_audit_actor_company replaced with fk_audit_actor.';
END;
$$;
