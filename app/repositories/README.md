# Repository layer

Keep shared query functions here as the project grows. Suggested modules:
- `ticket_repository.py`
- `user_repository.py`
- `organization_repository.py`
- `catalog_repository.py`
- `audit_repository.py`

The first vertical slice keeps simple queries in service functions. Extract repository methods when query reuse or testability justifies it.
