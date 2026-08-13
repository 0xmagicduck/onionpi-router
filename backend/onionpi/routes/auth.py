from __future__ import annotations

import asyncio
import secrets
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..auth import (
    PasswordHashingBusy,
    dummy_verify,
    hash_password,
    hashing_slot,
    token_hash,
    verify_password,
)
from .context import COOKIE_NAME, RouteContext


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=1, max_length=256)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


class RecoveryRequest(BaseModel):
    recovery_code: str = Field(min_length=20, max_length=64)
    new_password: str = Field(min_length=12, max_length=256)


def create_router(context: RouteContext) -> APIRouter:
    router = APIRouter()
    settings = context.settings
    services = context.services
    database = services.database
    login_limiter = services.login_limiter
    current_session = context.current_session
    csrf_session = context.csrf_session

    @router.post("/api/v1/auth/login")
    def login(
        payload: LoginRequest, request: Request, response: Response
    ) -> dict[str, Any]:
        address = request.client.host if request.client else "unknown"
        reservation = login_limiter.reserve(address)
        if reservation is None:
            raise HTTPException(
                status_code=429,
                detail="Trop de tentatives. Réessayez dans quelques minutes.",
            )
        authenticated = False
        reservation_active = True
        try:
            user = database.user_by_name(payload.username)
            try:
                with hashing_slot():
                    if user is None:
                        dummy_verify()
                        valid = False
                    else:
                        valid = verify_password(payload.password, user["password_hash"])
            except PasswordHashingBusy as error:
                login_limiter.complete(reservation, success=True)
                reservation_active = False
                raise HTTPException(
                    status_code=429,
                    detail="Trop de connexions simultanées. Réessayez dans un instant.",
                ) from error
            if not valid:
                time.sleep(0.2)
                raise HTTPException(status_code=401, detail="Identifiants incorrects")
            token = secrets.token_urlsafe(32)
            csrf = secrets.token_urlsafe(24)
            database.create_session(
                token_hash(token, settings.session_secret),
                csrf,
                int(user["id"]),
                settings.session_ttl_seconds,
            )
            authenticated = True
            response.set_cookie(
                COOKIE_NAME,
                token,
                max_age=settings.session_ttl_seconds,
                httponly=True,
                secure=settings.cookie_secure and not context.is_onion_request(request),
                samesite="strict",
                path="/",
            )
            return {
                "user": {
                    "username": user["username"],
                    "display_name": user["display_name"],
                },
                "csrf": csrf,
            }
        finally:
            if reservation_active:
                login_limiter.complete(reservation, success=authenticated)

    @router.get("/api/v1/auth/session")
    def session_info(
        session: dict[str, Any] = Depends(current_session),
    ) -> dict[str, Any]:
        return {
            "user": {
                "username": session["username"],
                "display_name": session["display_name"],
            },
            "csrf": session["csrf_token"],
        }

    @router.post("/api/v1/auth/password")
    async def change_password(
        payload: PasswordChangeRequest,
        response: Response,
        session: dict[str, Any] = Depends(csrf_session),
    ) -> dict[str, Any]:
        user = await asyncio.to_thread(database.user_by_name, session["username"])
        if not user:
            await asyncio.sleep(0.2)
            raise HTTPException(status_code=403, detail="Mot de passe actuel incorrect")

        def verify_current_password() -> bool:
            with hashing_slot():
                return verify_password(payload.current_password, user["password_hash"])

        try:
            verified = await asyncio.to_thread(verify_current_password)
        except PasswordHashingBusy as error:
            raise HTTPException(
                status_code=429,
                detail="Trop de vérifications simultanées. Réessayez dans un instant.",
            ) from error
        if not verified:
            await asyncio.sleep(0.2)
            raise HTTPException(status_code=403, detail="Mot de passe actuel incorrect")
        if payload.current_password == payload.new_password:
            raise HTTPException(
                status_code=400, detail="Choisissez un mot de passe différent"
            )

        def create_new_hash() -> str:
            with hashing_slot():
                return hash_password(payload.new_password)

        try:
            new_hash = await asyncio.to_thread(create_new_hash)
        except PasswordHashingBusy as error:
            raise HTTPException(
                status_code=429,
                detail="Trop de vérifications simultanées. Réessayez dans un instant.",
            ) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        await asyncio.to_thread(
            database.create_user, user["username"], user["display_name"], new_hash
        )
        services.onboarding.mark("password")
        await context.chat.disconnect_user(int(user["id"]))
        database.add_activity("secure", f"Mot de passe modifié pour {user['username']}")
        response.delete_cookie(COOKIE_NAME, path="/")
        return {"ok": True, "message": "Mot de passe modifié. Reconnectez-vous."}

    @router.post("/api/v1/auth/recover")
    async def recover_account(
        payload: RecoveryRequest, request: Request
    ) -> dict[str, Any]:
        if not services.maintenance.active:
            raise HTTPException(
                status_code=403,
                detail="Activez d’abord le mode maintenance depuis la console physique.",
            )
        address = request.client.host if request.client else "unknown"
        reservation = login_limiter.reserve(f"recovery:{address}")
        if reservation is None:
            raise HTTPException(
                status_code=429, detail="Trop de tentatives de récupération"
            )
        success = False
        try:
            encoded = services.onboarding.recovery_hash()
            compact = "".join(
                character
                for character in payload.recovery_code.upper()
                if character.isalnum()
            )
            normalised = "-".join(
                compact[index : index + 5] for index in range(0, len(compact), 5)
            )

            def verify_recovery() -> bool:
                with hashing_slot():
                    return bool(encoded) and verify_password(normalised, encoded)

            if not await asyncio.to_thread(verify_recovery):
                services.onboarding.record_recovery(False)
                await asyncio.sleep(0.2)
                raise HTTPException(status_code=403, detail="Code de récupération refusé")

            def replace_password() -> None:
                # Reset the account this appliance already has rather than
                # minting a second "admin": recovery exists to take control
                # back, and a parallel account would leave the old password
                # working. Every session goes with it, including any the person
                # being locked out still holds on another device.
                administrator = database.administrator()
                username = str(administrator["username"]) if administrator else "admin"
                display_name = (
                    str(administrator["display_name"])
                    if administrator
                    else "Administrateur"
                )
                with hashing_slot():
                    digest = hash_password(payload.new_password)
                database.create_user(username, display_name, digest)
                database.delete_all_sessions()

            await asyncio.to_thread(replace_password)
            await context.chat.disconnect_all()
            services.onboarding.record_recovery(True)
            database.add_activity(
                "secure", "Compte administrateur récupéré en présence physique"
            )
            success = True
            return {
                "ok": True,
                "message": "Mot de passe remplacé. Vous pouvez vous connecter.",
            }
        except PasswordHashingBusy as error:
            raise HTTPException(
                status_code=429, detail="Récupération occupée, réessayez"
            ) from error
        finally:
            login_limiter.complete(reservation, success=success)

    @router.post("/api/v1/auth/logout", status_code=204)
    async def logout(
        response: Response,
        request: Request,
        _: dict[str, Any] = Depends(csrf_session),
    ) -> Response:
        raw_token = request.cookies.get(COOKIE_NAME)
        if raw_token:
            digest = token_hash(raw_token, settings.session_secret)
            await asyncio.to_thread(database.delete_session, digest)
            await context.chat.disconnect_token(digest)
        response.delete_cookie(COOKIE_NAME, path="/")
        response.status_code = 204
        return response

    return router
