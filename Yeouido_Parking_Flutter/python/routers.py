from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI

import auth
import admin_auth
import parking
import facility
import reservation


ROUTER_MODULES = (
    auth,
    admin_auth,
    parking,
    reservation,
    facility,
)


def include_routers(app: FastAPI, connection_factory: Callable[[], Any]) -> None:
    for module in ROUTER_MODULES:
        set_connection_factory = getattr(module, "set_connection_factory", None)
        if callable(set_connection_factory):
            set_connection_factory(connection_factory)

        app.include_router(module.router)
