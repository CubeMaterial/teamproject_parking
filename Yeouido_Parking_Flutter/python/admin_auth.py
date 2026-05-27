from __future__ import annotations

from contextlib import closing
import secrets
from typing import Any

import pymysql
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr, Field

from sql_creator import CRUD, SQLBuilderError, build_sql


router = APIRouter(prefix="/auth/admin", tags=["admin-auth"])

ADMIN_TABLE = "admin"
ADMIN_LOGIN_COLUMNS = [
    "admin_id",
    "admin_email",
    "admin_password",
    "admin_name",
]

connection_factory: Any = None


class AdminLoginRequest(BaseModel):
    admin_email: EmailStr | None = Field(default=None, max_length=45)
    admin_password: str | None = Field(default=None, min_length=1, max_length=128)


def set_connection_factory(factory: Any) -> None:
    global connection_factory
    connection_factory = factory


def get_admin_auth_connection() -> pymysql.connections.Connection:
    if connection_factory is None:
        raise RuntimeError("DB 연결 팩토리가 설정되어 있지 않습니다.")
    return connection_factory()


def request_admin_email(request: AdminLoginRequest) -> str:
    admin_email = request.admin_email
    if admin_email is None:
        raise HTTPException(status_code=422, detail="관리자 이메일이 필요합니다.")
    return str(admin_email).strip().lower()


def request_admin_password(request: AdminLoginRequest) -> str:
    admin_password = request.admin_password
    if admin_password is None:
        raise HTTPException(status_code=422, detail="관리자 비밀번호가 필요합니다.")
    return admin_password


def execute_read_one(sql: str, params: list[Any]) -> dict[str, Any] | None:
    try:
        with closing(get_admin_auth_connection()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                return cursor.fetchone()
    except pymysql.MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"DB 조회에 실패했습니다: {exc}") from exc


def get_admin_by_email(admin_email: str) -> dict[str, Any] | None:
    try:
        sql, params = build_sql(
            CRUD.READ,
            ADMIN_TABLE,
            attribute_name=ADMIN_LOGIN_COLUMNS,
            condition_attribute_name=["admin_email"],
            condition_attribute_value=[admin_email],
        )
    except SQLBuilderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return execute_read_one(sql, params)


@router.post("/login")
def login_admin(request: AdminLoginRequest) -> dict[str, Any]:
    admin_email = request_admin_email(request)
    admin_password = request_admin_password(request)
    admin = get_admin_by_email(admin_email)

    if admin is None:
        raise HTTPException(status_code=404, detail="등록된 관리자 계정이 아닙니다.")

    stored_password = str(admin["admin_password"])
    if not secrets.compare_digest(
        admin_password.encode("utf-8"),
        stored_password.encode("utf-8"),
    ):
        raise HTTPException(
            status_code=401,
            detail="관리자 이메일 또는 비밀번호가 올바르지 않습니다.",
        )

    return {
        "status": "authenticated",
        "admin_id": admin["admin_id"],
        "admin_email": admin["admin_email"],
        "admin_name": admin["admin_name"],
    }
