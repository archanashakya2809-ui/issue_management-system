-- WARNING: This rollback permanently deletes tenant company settings data.
-- Export/backup public.company_settings before running.

BEGIN;
DROP TABLE IF EXISTS public.company_settings;
COMMIT;
