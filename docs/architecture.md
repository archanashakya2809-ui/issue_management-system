# Backend architecture

- **Routes**: HTTP request/response handling only.
- **Dependencies**: request-scoped DB session, authentication, permission checks.
- **Schemas**: Pydantic request/response validation.
- **Services**: business rules, transaction boundaries, audit calls.
- **Models**: SQLAlchemy mappings for the first vertical slice.
- **Core**: settings, logging, JWT and token hashing.
- **PostgreSQL**: final row-scope enforcement, constraints, triggers, and audit helper.

Request flow: React -> FastAPI route -> authentication -> named permission check -> service -> PostgreSQL RLS and constraints -> typed response.

This starter uses synchronous SQLAlchemy and Psycopg 3 to make transactions and RLS context easier to understand. Do not mix sync and async sessions in a single request path.
