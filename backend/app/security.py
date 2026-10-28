from datetime import timedelta

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash

from .models import User, now

passwords = PasswordHash.recommended()
dummy_hash = passwords.hash("dummy-password-for-constant-work-login")
bearer = HTTPBearer(auto_error=False)


def token(user, settings):
    issued = now()
    return jwt.encode({"sub": user.id, "ver": user.token_version, "iat": issued,
                       "exp": issued + timedelta(minutes=settings.token_minutes),
                       "iss": "pricefinder", "aud": "pricefinder-api"},
                      settings.jwt_secret, algorithm="HS256")


def current_user(request: Request, auth: HTTPAuthorizationCredentials | None = Depends(bearer)):
    denied = HTTPException(401, "Invalid or expired credentials", headers={"WWW-Authenticate": "Bearer"})
    if not auth:
        raise denied
    try:
        claims = jwt.decode(auth.credentials, request.app.state.settings.jwt_secret,
                            algorithms=["HS256"], audience="pricefinder-api", issuer="pricefinder",
                            options={"require": ["sub", "exp", "iat", "ver"]})
    except jwt.InvalidTokenError:
        raise denied from None
    with request.app.state.sessions() as db:
        user = db.get(User, claims["sub"])
        if not user or user.token_version != claims["ver"]:
            raise denied
        return user


def admin(user: User = Depends(current_user)):
    if not user.is_admin:
        raise HTTPException(403, "Administrator access required")
    return user
