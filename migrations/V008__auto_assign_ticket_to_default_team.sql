-- V008__auto_assign_ticket_to_default_team.sql
-- Creates a narrow SECURITY DEFINER helper for automatic category routing.
-- Run using an authorized database administrator. Do not disable RLS.

BEGIN;

CREATE OR REPLACE FUNCTION public.app_auto_assign_ticket_to_default_team(
    p_ticket_id uuid
)
RETURNS TABLE (
    assignment_id uuid,
    assigned_team_id uuid,
    created boolean
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    v_actor_id uuid;
    v_actor_company_id uuid;
    v_ticket_company_id uuid;
    v_requester_id uuid;
    v_category_id uuid;
    v_domain_id uuid;
    v_status_code text;
    v_domain_department_id uuid;
    v_default_team_id uuid;
    v_existing_assignment_id uuid;
    v_existing_team_id uuid;
    v_new_assignment_id uuid;
BEGIN
    v_actor_id := public.app_current_user_id();
    v_actor_company_id := public.app_current_user_company_id();

    IF v_actor_id IS NULL OR v_actor_company_id IS NULL THEN
        RAISE EXCEPTION 'Authenticated application user is required for automatic routing'
            USING ERRCODE = '42501';
    END IF;

    -- This helper is not a replacement for the normal endpoint permission
    -- check. It also protects itself if invoked directly through SQL.
    IF NOT public.app_has_permission_in_company(
        v_actor_company_id,
        'ticket.create'::varchar
    ) THEN
        RAISE EXCEPTION 'Permission required: ticket.create'
            USING ERRCODE = '42501';
    END IF;

    SELECT
        t.company_id,
        t.requester_id,
        t.category_id,
        t.domain_id,
        s.code
    INTO
        v_ticket_company_id,
        v_requester_id,
        v_category_id,
        v_domain_id,
        v_status_code
    FROM public.tickets AS t
    JOIN public.ticket_statuses AS s
      ON s.id = t.status_id
     AND s.company_id = t.company_id
    WHERE t.id = p_ticket_id
      AND t.company_id = v_actor_company_id
    FOR UPDATE OF t;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Ticket not found in the caller company'
            USING ERRCODE = '42501';
    END IF;

    -- Only the authenticated requester can trigger automatic routing for
    -- their ticket; no caller-supplied company/team/user IDs are accepted.
    IF v_requester_id <> v_actor_id THEN
        RAISE EXCEPTION 'Automatic routing is allowed only for the ticket requester'
            USING ERRCODE = '42501';
    END IF;

    IF v_ticket_company_id <> v_actor_company_id THEN
        RAISE EXCEPTION 'Ticket company does not match the authenticated company'
            USING ERRCODE = '42501';
    END IF;

    IF v_status_code <> 'OPEN' THEN
        RAISE EXCEPTION 'Automatic team routing requires a ticket in OPEN status'
            USING ERRCODE = '23514';
    END IF;

    -- Be idempotent. Never replace a manual assignment if one already exists.
    SELECT ta.id, ta.assigned_team_id
    INTO v_existing_assignment_id, v_existing_team_id
    FROM public.ticket_assignments AS ta
    WHERE ta.company_id = v_ticket_company_id
      AND ta.ticket_id = p_ticket_id
      AND ta.unassigned_at IS NULL
    ORDER BY ta.assigned_at DESC, ta.id DESC
    LIMIT 1
    FOR UPDATE;

    IF FOUND THEN
        RETURN QUERY
        SELECT v_existing_assignment_id, v_existing_team_id, FALSE;
        RETURN;
    END IF;

    -- Validate the category/domain hierarchy and find its department.
    SELECT d.department_id
    INTO v_domain_department_id
    FROM public.domains AS d
    JOIN public.ticket_categories AS c
      ON c.company_id = d.company_id
     AND c.domain_id = d.id
    JOIN public.departments AS dep
      ON dep.company_id = d.company_id
     AND dep.id = d.department_id
    WHERE d.company_id = v_ticket_company_id
      AND d.id = v_domain_id
      AND c.id = v_category_id
      AND d.status = 'active'
      AND c.is_active = TRUE
      AND dep.status = 'active';

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Ticket category, domain, or department is inactive or inconsistent'
            USING ERRCODE = '23514';
    END IF;

    -- The routing helper chooses the active route with the lowest
    -- routing_priority. A null result means no valid route exists.
    v_default_team_id := public.app_default_team_for_category(
        v_ticket_company_id,
        v_category_id
    );

    IF v_default_team_id IS NULL THEN
        RAISE EXCEPTION 'Ticket category has no active route to a team'
            USING ERRCODE = '23514';
    END IF;

    -- Revalidate tenant, active state and department before assigning.
    IF NOT EXISTS (
        SELECT 1
        FROM public.teams AS tm
        WHERE tm.id = v_default_team_id
          AND tm.company_id = v_ticket_company_id
          AND tm.department_id = v_domain_department_id
          AND tm.status = 'active'
    ) THEN
        RAISE EXCEPTION 'Configured routing team is not active or does not match the domain department'
            USING ERRCODE = '23514';
    END IF;

    INSERT INTO public.ticket_assignments (
        company_id,
        ticket_id,
        assigned_team_id,
        assigned_user_id,
        assigned_by,
        reason
    )
    VALUES (
        v_ticket_company_id,
        p_ticket_id,
        v_default_team_id,
        NULL,
        v_actor_id,
        'Automatic category routing'
    )
    RETURNING id INTO v_new_assignment_id;

    RETURN QUERY
    SELECT v_new_assignment_id, v_default_team_id, TRUE;
END;
$function$;

-- The security owner must be able to create objects in the schema at the
-- moment ownership is transferred. Grant this only inside the migration,
-- then revoke it before committing. The role must retain schema USAGE and
-- the required table privileges for the function to run.
GRANT USAGE ON SCHEMA public TO ticket_security_owner;
GRANT CREATE ON SCHEMA public TO ticket_security_owner;

ALTER FUNCTION public.app_auto_assign_ticket_to_default_team(uuid)
    OWNER TO ticket_security_owner;

REVOKE CREATE ON SCHEMA public FROM ticket_security_owner;

-- Ensure the SECURITY DEFINER owner can call the existing trusted helpers.
-- These grants are limited to the NOLOGIN security-owner role.
GRANT EXECUTE ON FUNCTION public.app_current_user_id()
    TO ticket_security_owner;
GRANT EXECUTE ON FUNCTION public.app_current_user_company_id()
    TO ticket_security_owner;
GRANT EXECUTE ON FUNCTION public.app_has_permission_in_company(uuid, character varying)
    TO ticket_security_owner;
GRANT EXECUTE ON FUNCTION public.app_default_team_for_category(uuid, uuid)
    TO ticket_security_owner;

REVOKE ALL ON FUNCTION public.app_auto_assign_ticket_to_default_team(uuid)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.app_auto_assign_ticket_to_default_team(uuid)
    TO ticket_app;

COMMIT;
