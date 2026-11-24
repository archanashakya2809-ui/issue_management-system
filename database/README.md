# Existing database and migration policy

The database is already created through separate SQL scripts. The backend must not create or alter tables at startup.

1. Do not call `Base.metadata.create_all()`.
2. Do not run seed scripts automatically.
3. Keep the approved SQL schema and verification scripts under version control.
4. Use the schema preflight script before starting feature work.
5. Verify the application login is not a superuser and has no `BYPASSRLS`.
6. Review `SECURITY DEFINER` functions, ownership, `search_path`, grants, and RLS interaction.
7. Do not run Alembic autogeneration from these partial ORM mappings. Create a reviewed baseline only after the complete schema has been mapped and compared.
