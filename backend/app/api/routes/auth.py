from fastapi import APIRouter, Request, status

from app.api.deps import CurrentUser, SessionDep, SettingsDep
from app.schemas.auth import AuthResponse, LoginRequest, RegisterRequest, UserResponse
from app.services.auth import AuthService

router = APIRouter()


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest, session: SessionDep, settings: SettingsDep
) -> AuthResponse:
    user, token = await AuthService(session, settings).register(payload)
    return AuthResponse(access_token=token, user=UserResponse.model_validate(user))


@router.post("/login", response_model=AuthResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> AuthResponse:
    client_ip = request.client.host if request.client else "unknown"
    user, token = await AuthService(session, settings).login(payload, client_ip)
    return AuthResponse(access_token=token, user=UserResponse.model_validate(user))


@router.get("/me", response_model=UserResponse)
async def me(user: CurrentUser) -> UserResponse:
    return UserResponse.model_validate(user)
