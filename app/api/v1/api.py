from fastapi import APIRouter

from app.api.v1.routes import (
    admin,
    attachments,
    auth,
    comments,
    company_settings,
    health,
    lookup,
    notifications,
    org,
    system_admin,
    tickets,
    users,
    workload,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(attachments.router)
api_router.include_router(company_settings.router)
api_router.include_router(tickets.router)
api_router.include_router(comments.router)
api_router.include_router(lookup.router)
api_router.include_router(users.router)
api_router.include_router(workload.router)
api_router.include_router(notifications.router)
api_router.include_router(org.router)
api_router.include_router(admin.router)
api_router.include_router(system_admin.router)
