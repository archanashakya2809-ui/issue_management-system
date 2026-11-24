"""Read-only preflight check for the existing Ticket Management PostgreSQL schema."""

import sys

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings

REQUIRED_TABLES = {
    "companies", "departments", "teams", "domains", "users", "user_sessions",
    "team_members", "roles", "permissions", "role_permissions", "user_role_assignments",
    "ticket_categories", "ticket_priorities", "ticket_statuses", "workflow_transitions",
    "tickets", "ticket_assignments", "ticket_comments", "ticket_attachments",
    "ticket_escalations", "ticket_status_history", "notifications",
    "notification_deliveries", "ticket_sla", "sla_policies", "audit_logs",
}
REQUIRED_FUNCTIONS = {
    "app_current_user_id", "app_current_user_company_id", "app_ticket_access",
    "app_user_can_create_ticket", "app_write_audit",
}


def main() -> int:
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            db_name = connection.execute(text("SELECT current_database()")).scalar_one()
            tables = set(connection.execute(text("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            """)).scalars())
            functions = set(connection.execute(text("""
                SELECT p.proname FROM pg_proc p
                JOIN pg_namespace n ON n.oid = p.pronamespace
                WHERE n.nspname = 'public'
            """)).scalars())
            missing_tables = sorted(REQUIRED_TABLES - tables)
            missing_functions = sorted(REQUIRED_FUNCTIONS - functions)
            print(f"Connected database: {db_name}")
            print(f"Tables found: {len(REQUIRED_TABLES) - len(missing_tables)}/{len(REQUIRED_TABLES)}")
            print(f"Functions found: {len(REQUIRED_FUNCTIONS) - len(missing_functions)}/{len(REQUIRED_FUNCTIONS)}")
            if missing_tables:
                print("Missing tables:", ", ".join(missing_tables))
            if missing_functions:
                print("Missing functions:", ", ".join(missing_functions))
            if missing_tables or missing_functions:
                print("FAILED: no database changes were made.")
                return 1
            print("PASSED for the minimum object set.")
            print("This does not validate RLS policies, grants, constraints, triggers, or production readiness.")
            return 0
    except SQLAlchemyError as exc:
        print(f"Could not connect/check schema: {exc}")
        return 2
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
