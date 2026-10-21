"""FastAPI dependencies shared by the routers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, Request

from oncallpilot.api.auth import AuthUser, bearer_token

if TYPE_CHECKING:
    from oncallpilot.api.app import Services


def get_services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


async def current_user(request: Request) -> AuthUser:
    return await get_services(request).verifier.verify(bearer_token(request))


CurrentUser = Annotated[AuthUser, Depends(current_user)]
