from __future__ import annotations

from contextlib import closing
from datetime import datetime
import base64
import binascii
import hashlib
import secrets
from typing import Any

import pymysql
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr, Field

from sql_creator import CRUD, SQLBuilderError, build_sql


router = APIRouter(prefix="/auth", tags=["auth"])

PASSWORD_HASH_ALGORITHM = "pbkdf2_sha256"
PASSWORD_HASH_ITERATIONS = 260_000
PASSWORD_SALT_BYTES = 16

USER_TABLE = "user"
USER_CREATE_COLUMNS = [
    "user_email",
    "user_password",
    "user_date",
    "user_name",
    "user_phone",
]
USER_UPDATE_COLUMNS = {"user_email", "user_password", "user_name", "user_phone"}
USER_LOGIN_COLUMNS = [
    "user_id",
    "user_email",
    "user_password",
    "user_date",
    "user_name",
    "user_phone",
]

connection_factory: Any = None


class UserCreateRequest(BaseModel):
    user_email: EmailStr | None = Field(default=None, max_length=45)
    user_password: str | None = Field(default=None, min_length=8, max_length=128)
    user_name: str | None = Field(default=None, max_length=45)
    user_phone: str | None = Field(default=None, max_length=45)
    email: EmailStr | None = Field(default=None, max_length=45)
    password: str | None = Field(default=None, min_length=8, max_length=128)
    name: str | None = Field(default=None, max_length=45)
    phone: str | None = Field(default=None, max_length=45)


class UserUpdateRequest(BaseModel):
    user_email: EmailStr | None = Field(default=None, max_length=45)
    user_password: str | None = Field(default=None, min_length=8, max_length=128)
    user_name: str | None = Field(default=None, max_length=45)
    user_phone: str | None = Field(default=None, max_length=45)


class UserLoginRequest(BaseModel):
    user_email: EmailStr | None = Field(default=None, max_length=45)
    user_password: str | None = Field(default=None, min_length=8, max_length=128)
    email: EmailStr | None = Field(default=None, max_length=45)
    password: str | None = Field(default=None, min_length=8, max_length=128)


def set_connection_factory(factory: Any) -> None:
    global connection_factory
    connection_factory = factory


def get_auth_connection() -> pymysql.connections.Connection:
    if connection_factory is None:
        raise RuntimeError("DB 연결 팩토리가 설정되어 있지 않습니다.")
    return connection_factory()


def hash_password(raw_password: str) -> str:
    salt = secrets.token_bytes(PASSWORD_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        raw_password.encode("utf-8"),
        salt,
        PASSWORD_HASH_ITERATIONS,
    )
    salt_text = base64.urlsafe_b64encode(salt).decode("ascii").rstrip("=")
    digest_text = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return (
        f"{PASSWORD_HASH_ALGORITHM}"
        f"${PASSWORD_HASH_ITERATIONS}"
        f"${salt_text}"
        f"${digest_text}"
    )


def verify_password(raw_password: str, stored_password: str) -> bool:
    try:
        algorithm, iterations_text, salt_text, digest_text = stored_password.split("$")
        iterations = int(iterations_text)
    except ValueError:
        return False

    if algorithm != PASSWORD_HASH_ALGORITHM:
        return False

    try:
        salt = base64.urlsafe_b64decode(salt_text + "=" * (-len(salt_text) % 4))
        expected_digest = base64.urlsafe_b64decode(
            digest_text + "=" * (-len(digest_text) % 4)
        )
    except (ValueError, binascii.Error):
        return False

    actual_digest = hashlib.pbkdf2_hmac(
        "sha256",
        raw_password.encode("utf-8"),
        salt,
        iterations,
    )
    return secrets.compare_digest(actual_digest, expected_digest)


def request_email(request: UserCreateRequest | UserLoginRequest) -> str:
    user_email = request.user_email or request.email
    if user_email is None:
        raise HTTPException(status_code=422, detail="이메일이 필요합니다.")
    return str(user_email).strip().lower()


def request_password(request: UserCreateRequest | UserLoginRequest) -> str:
    user_password = request.user_password or request.password
    if user_password is None:
        raise HTTPException(status_code=422, detail="비밀번호가 필요합니다.")
    return user_password


def request_name(request: UserCreateRequest) -> str | None:
    return request.user_name or request.name


def request_phone(request: UserCreateRequest) -> str | None:
    return request.user_phone or request.phone


def execute_write(sql: str, params: list[Any]) -> int:
    try:
        with closing(get_auth_connection()) as connection:
            with connection.cursor() as cursor:
                affected_rows = cursor.execute(sql, params)
            connection.commit()
            return affected_rows
    except pymysql.MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"DB 작업에 실패했습니다: {exc}") from exc


def execute_read_one(sql: str, params: list[Any]) -> dict[str, Any] | None:
    try:
        with closing(get_auth_connection()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                return cursor.fetchone()
    except pymysql.MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"DB 조회에 실패했습니다: {exc}") from exc


def get_user_by_email(user_email: str) -> dict[str, Any] | None:
    try:
        sql, params = build_sql(
            CRUD.READ,
            USER_TABLE,
            attribute_name=USER_LOGIN_COLUMNS,
            condition_attribute_name=["user_email"],
            condition_attribute_value=[user_email],
        )
    except SQLBuilderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return execute_read_one(sql, params)


@router.post("/users", status_code=201)
def create_user(request: UserCreateRequest) -> dict[str, str]:
    user_email = request_email(request)
    user_password = request_password(request)
    user_name = request_name(request)
    user_phone = request_phone(request)

    if get_user_by_email(user_email) is not None:
        raise HTTPException(status_code=409, detail="이미 가입된 이메일입니다.")

    hashed_password = hash_password(user_password)

    try:
        sql, params = build_sql(
            CRUD.CREATE,
            USER_TABLE,
            attribute_name=USER_CREATE_COLUMNS,
            attribute_value=[
                user_email,
                hashed_password,
                datetime.now(),
                user_name,
                user_phone,
            ],
        )
    except SQLBuilderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    execute_write(sql, params)
    return {"status": "created"}


@router.post("/login")
def login_user(request: UserLoginRequest) -> dict[str, Any]:
    user_email = request_email(request)
    user_password = request_password(request)
    user = get_user_by_email(user_email)

    if user is None:
        raise HTTPException(status_code=404, detail="가입되지 않은 이메일입니다.")

    if not verify_password(user_password, user["user_password"]):
        raise HTTPException(status_code=401, detail="이메일 또는 비밀번호가 올바르지 않습니다.")

    return {
        "status": "authenticated",
        "user_id": user["user_id"],
        "user_email": user["user_email"],
        "user_name": user["user_name"],
        "user_phone": user["user_phone"],
        "user_date": user["user_date"],
    }


@router.patch("/users/{user_id}")
def update_user(user_id: int, request: UserUpdateRequest) -> dict[str, str | int]:
    update_data = request.model_dump(exclude_unset=True)

    if not update_data:
        raise HTTPException(status_code=400, detail="수정할 값이 없습니다.")

    if "user_password" in update_data:
        update_data["user_password"] = hash_password(update_data["user_password"])

    invalid_columns = sorted(set(update_data) - USER_UPDATE_COLUMNS)
    if invalid_columns:
        raise HTTPException(
            status_code=400,
            detail=f"허용되지 않은 컬럼입니다: {', '.join(invalid_columns)}",
        )

    try:
        sql, params = build_sql(
            CRUD.UPDATE,
            USER_TABLE,
            attribute_name=list(update_data.keys()),
            attribute_value=list(update_data.values()),
            condition_attribute_name=["user_id"],
            condition_attribute_value=[user_id],
        )
    except SQLBuilderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    affected_rows = execute_write(sql, params)
    return {"status": "updated", "affected_rows": affected_rows}


@router.delete("/users/{user_id}")
def delete_user(user_id: int) -> dict[str, str | int]:
    try:
        sql, params = build_sql(
            CRUD.DELETE,
            USER_TABLE,
            condition_attribute_name=["user_id"],
            condition_attribute_value=[user_id],
        )
    except SQLBuilderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    affected_rows = execute_write(sql, params)
    return {"status": "deleted", "affected_rows": affected_rows}
