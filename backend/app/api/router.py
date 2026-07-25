from fastapi import APIRouter

from app.api.routes import ai, auth, inspirations, tags

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["认证"])
api_router.include_router(inspirations.router, prefix="/inspirations", tags=["灵感"])
api_router.include_router(tags.router, prefix="/tags", tags=["标签"])
api_router.include_router(ai.router, prefix="/inspirations", tags=["AI"])

