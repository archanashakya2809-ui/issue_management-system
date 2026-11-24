-- V013 rollback. Restores the supplied pre-V013 authorization behavior.
-- Requires V011 to remain installed. Review before running.
BEGIN;

DROP POLICY IF EXISTS tickets_insert_policy ON public.tickets;
CREATE POLICY tickets_insert_policy ON public.tickets FOR INSERT
WITH CHECK (
    requester_id = public.app_current_user_id()
    AND public.app_user_can_create_ticket(
        company_id, requester_id, requester_department_id, domain_id, category_id,
        public.app_default_team_for_category(company_id, category_id)
    )
);

DROP POLICY IF EXISTS departments_select_policy ON public.departments;
CREATE POLICY departments_select_policy ON public.departments FOR SELECT
USING (public.app_user_has_permission_in_scope(company_id, 'department.view', id, NULL));

DROP POLICY IF EXISTS domains_select_policy ON public.domains;
CREATE POLICY domains_select_policy ON public.domains FOR SELECT
USING (
    public.app_user_has_permission_in_scope(company_id, 'domain.view', department_id, NULL)
    AND (
        public.app_user_has_permission_in_scope(company_id, 'department.view', department_id, NULL)
        OR public.app_user_has_permission_in_scope(company_id, 'company.manage', NULL, NULL)
    )
);

DROP POLICY IF EXISTS ticket_categories_select_policy ON public.ticket_categories;
CREATE POLICY ticket_categories_select_policy ON public.ticket_categories FOR SELECT
USING (
    (public.app_has_permission_in_company(company_id, 'ticket.view')
     OR public.app_has_permission_in_company(company_id, 'ticket.create'))
    AND public.app_user_has_domain_access(company_id, domain_id)
);

CREATE OR REPLACE FUNCTION app_user_can_create_ticket(
    p_company_id uuid,
    p_requester_id uuid,
    p_requester_department_id uuid,
    p_domain_id uuid,
    p_category_id uuid,
    p_team_id uuid
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
    WITH expected_route AS (
        SELECT app_default_team_for_category(
            p_company_id,
            p_category_id
        ) AS team_id
    )
    SELECT EXISTS (
        SELECT 1
        FROM users u
        JOIN user_role_assignments ura
          ON ura.user_id = u.id
         AND ura.revoked_at IS NULL
        JOIN roles r
          ON r.id = ura.role_id
         AND r.is_active = true
        JOIN role_permissions rp
          ON rp.role_id = r.id
        JOIN permissions p
          ON p.id = rp.permission_id
         AND p.code = 'ticket.create'
         AND p.is_active = true
        CROSS JOIN expected_route er
        JOIN ticket_categories c
          ON c.company_id = p_company_id
         AND c.id = p_category_id
         AND c.domain_id = p_domain_id
         AND c.is_active = true
        JOIN domains d
          ON d.company_id = p_company_id
         AND d.id = p_domain_id
         AND d.status = 'active'
        JOIN teams target_team
          ON target_team.company_id = p_company_id
         AND target_team.id = p_team_id
         AND target_team.status = 'active'
        WHERE u.id = app_current_user_id()
          AND u.status = 'active'
          AND p_company_id = u.company_id
          AND p_requester_id = u.id
          AND p_requester_department_id IS NOT DISTINCT FROM u.department_id
          AND p_team_id = er.team_id
          AND target_team.department_id = d.department_id
          AND (
                (
                    ura.scope_type = 'system'
                    AND r.code = 'SYSTEM_ADMIN'
                )
                OR (
                    ura.scope_type <> 'system'
                    AND ura.company_id = p_company_id
                )
              )
          AND (
                (
                    ura.scope_type = 'system'
                    AND r.code = 'SYSTEM_ADMIN'
                )
                OR ura.scope_type = 'company'
                OR (
                    ura.scope_type = 'department'
                    AND p_requester_department_id = ura.scope_department_id
                )
                OR (
                    ura.scope_type = 'team'
                    AND p_team_id = ura.scope_team_id
                    AND EXISTS (
                        SELECT 1
                        FROM team_members tm
                        WHERE tm.company_id = ura.company_id
                          AND tm.team_id = ura.scope_team_id
                          AND tm.user_id = ura.user_id
                          AND tm.left_at IS NULL
                    )
                )
                OR (
                    ura.scope_type = 'self'
                    AND p_requester_id = ura.user_id
                )
              )
          AND (
                r.domain_access_mode = 'all'
                OR EXISTS (
                    SELECT 1
                    FROM role_domains rd
                    WHERE rd.company_id = p_company_id
                      AND rd.role_id = r.id
                      AND rd.domain_id = p_domain_id
                )
                OR EXISTS (
                    SELECT 1
                    FROM user_domains ud
                    WHERE ud.company_id = p_company_id
                      AND ud.user_id = u.id
                      AND ud.domain_id = p_domain_id
                      AND ud.revoked_at IS NULL
                )
              )
    );
$$;

CREATE OR REPLACE FUNCTION app_ticket_access(
    p_ticket_id uuid,
    p_permission_code varchar
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT EXISTS (
        SELECT 1
        FROM tickets t
        JOIN users u
          ON u.id = app_current_user_id()
         AND u.status = 'active'
        JOIN user_role_assignments ura
          ON ura.user_id = u.id
         AND ura.revoked_at IS NULL
        JOIN roles r
          ON r.id = ura.role_id
         AND r.is_active = true
        JOIN role_permissions rp
          ON rp.role_id = r.id
        JOIN permissions p
          ON p.id = rp.permission_id
         AND p.code = p_permission_code
         AND p.is_active = true
        LEFT JOIN LATERAL (
            SELECT ta.assigned_team_id, ta.assigned_user_id
            FROM ticket_assignments ta
            WHERE ta.company_id = t.company_id
              AND ta.ticket_id = t.id
              AND ta.unassigned_at IS NULL
            ORDER BY ta.assigned_at DESC
            LIMIT 1
        ) ca ON true
        LEFT JOIN LATERAL (
            SELECT r2.team_id
            FROM ticket_category_team_routes r2
            JOIN teams rt
              ON rt.company_id = r2.company_id
             AND rt.id = r2.team_id
             AND rt.status = 'active'
            WHERE r2.company_id = t.company_id
              AND r2.category_id = t.category_id
              AND r2.is_active = true
            ORDER BY r2.routing_priority ASC, r2.created_at ASC
            LIMIT 1
        ) dr ON true
        LEFT JOIN teams current_team
          ON current_team.id = COALESCE(ca.assigned_team_id, dr.team_id)
         AND current_team.company_id = t.company_id
        WHERE t.id = p_ticket_id
          AND (
                (
                    ura.scope_type = 'system'
                    AND r.code = 'SYSTEM_ADMIN'
                )
                OR (
                    ura.scope_type <> 'system'
                    AND ura.company_id = t.company_id
                )
              )
          AND (
                (
                    ura.scope_type = 'system'
                    AND r.code = 'SYSTEM_ADMIN'
                )
                OR (
                    ura.scope_type = 'company'
                    AND t.company_id = ura.company_id
                )
                OR (
                    ura.scope_type = 'department'
                    AND COALESCE(current_team.department_id, t.requester_department_id)
                        = ura.scope_department_id
                )
                OR (
                    ura.scope_type = 'team'
                    AND COALESCE(ca.assigned_team_id, dr.team_id) = ura.scope_team_id
                    AND current_team.status = 'active'
                    AND EXISTS (
                        SELECT 1
                        FROM team_members tm
                        WHERE tm.company_id = ura.company_id
                          AND tm.team_id = ura.scope_team_id
                          AND tm.user_id = ura.user_id
                          AND tm.left_at IS NULL
                    )
                )
                OR (
                    ura.scope_type = 'self'
                    AND t.requester_id = ura.user_id
                )
                OR (
                    t.requester_id = u.id
                    AND p_permission_code IN ('ticket.view', 'ticket.comment')
                )
              )
          AND app_user_domain_access(
                u.id,
                t.company_id,
                t.domain_id,
                r.id
              )
    );
$$;

CREATE OR REPLACE FUNCTION app_user_role_scope_matches_ticket(
    p_ticket_id uuid,
    p_role_id uuid
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT EXISTS (
        SELECT 1
        FROM tickets t
        JOIN users u
          ON u.id = app_current_user_id()
         AND u.status = 'active'
        JOIN user_role_assignments ura
          ON ura.user_id = u.id
         AND ura.role_id = p_role_id
         AND ura.revoked_at IS NULL
        JOIN roles r
          ON r.id = ura.role_id
         AND r.is_active = true
        LEFT JOIN LATERAL (
            SELECT ta.assigned_team_id
            FROM ticket_assignments ta
            WHERE ta.company_id = t.company_id
              AND ta.ticket_id = t.id
              AND ta.unassigned_at IS NULL
            ORDER BY ta.assigned_at DESC
            LIMIT 1
        ) ca ON true
        LEFT JOIN LATERAL (
            SELECT r2.team_id
            FROM ticket_category_team_routes r2
            JOIN teams rt
              ON rt.company_id = r2.company_id
             AND rt.id = r2.team_id
             AND rt.status = 'active'
            WHERE r2.company_id = t.company_id
              AND r2.category_id = t.category_id
              AND r2.is_active = true
            ORDER BY r2.routing_priority ASC, r2.created_at ASC
            LIMIT 1
        ) dr ON true
        LEFT JOIN teams current_team
          ON current_team.id = COALESCE(ca.assigned_team_id, dr.team_id)
         AND current_team.company_id = t.company_id
        WHERE t.id = p_ticket_id
          AND (
                (
                    ura.scope_type = 'system'
                    AND r.code = 'SYSTEM_ADMIN'
                )
                OR (
                    ura.scope_type <> 'system'
                    AND ura.company_id = t.company_id
                )
              )
          AND (
                (
                    ura.scope_type = 'system'
                    AND r.code = 'SYSTEM_ADMIN'
                )
                OR ura.scope_type = 'company'
                OR (
                    ura.scope_type = 'department'
                    AND COALESCE(current_team.department_id, t.requester_department_id)
                        = ura.scope_department_id
                )
                OR (
                    ura.scope_type = 'team'
                    AND COALESCE(ca.assigned_team_id, dr.team_id) = ura.scope_team_id
                    AND current_team.status = 'active'
                    AND EXISTS (
                        SELECT 1
                        FROM team_members tm
                        WHERE tm.company_id = ura.company_id
                          AND tm.team_id = ura.scope_team_id
                          AND tm.user_id = ura.user_id
                          AND tm.left_at IS NULL
                    )
                )
                OR (
                    ura.scope_type = 'self'
                    AND t.requester_id = ura.user_id
                )
              )
          AND app_user_domain_access(
                u.id,
                t.company_id,
                t.domain_id,
                r.id
              )
    );
$$;

GRANT USAGE ON SCHEMA public TO ticket_security_owner;
GRANT CREATE ON SCHEMA public TO ticket_security_owner;
ALTER FUNCTION public.app_user_can_create_ticket(uuid,uuid,uuid,uuid,uuid,uuid) OWNER TO ticket_security_owner;
ALTER FUNCTION public.app_ticket_access(uuid,character varying) OWNER TO ticket_security_owner;
ALTER FUNCTION public.app_user_role_scope_matches_ticket(uuid,uuid) OWNER TO ticket_security_owner;
GRANT EXECUTE ON FUNCTION public.app_user_can_create_ticket(uuid,uuid,uuid,uuid,uuid,uuid) TO ticket_app;
GRANT EXECUTE ON FUNCTION public.app_ticket_access(uuid,character varying) TO ticket_app;
GRANT EXECUTE ON FUNCTION public.app_user_role_scope_matches_ticket(uuid,uuid) TO ticket_app;
REVOKE CREATE ON SCHEMA public FROM ticket_security_owner;

DROP FUNCTION IF EXISTS public.app_user_can_select_ticket_destination(uuid);
DROP FUNCTION IF EXISTS public.app_ticket_destination_is_valid(uuid,uuid,uuid,uuid,uuid);
REVOKE EXECUTE ON FUNCTION public.app_user_domain_access(uuid,uuid,uuid,uuid)
    FROM ticket_security_owner;
COMMIT;
