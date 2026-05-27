from contextlib import closing
from contextlib import asynccontextmanager
import os
from pathlib import Path
from typing import Any

import pymysql
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from routers import include_routers


load_dotenv(Path(__file__).with_name("py.env"))


def _get_env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value if value else None


def _load_db_config() -> dict[str, Any] | None:
    host = _get_env("DB_HOST")
    user = _get_env("DB_USER")
    password = _get_env("DB_PASSWORD")
    database = _get_env("DB_NAME")

    if not all([host, user, password, database]):
        return None

    return {
        "host": host,
        "port": int(os.getenv("DB_PORT", "3306")),
        "user": user,
        "password": password,
        "database": database,
        "charset": "utf8mb4",
        "connect_timeout": int(os.getenv("DB_CONNECT_TIMEOUT", "5")),
        "read_timeout": int(os.getenv("DB_READ_TIMEOUT", "5")),
        "write_timeout": int(os.getenv("DB_WRITE_TIMEOUT", "5")),
        "cursorclass": pymysql.cursors.DictCursor,
    }


DB_CONFIG: dict[str, Any] | None = None


DENIED_COLUMNS = {
    "password",
    "admin_password",
    "user_password",
    "token",
    "code_hash",
}

ALLOWED_TABLES: set[str] = set()
ALLOWED_COLUMNS: dict[str, set[str]] = {}


def get_connection() -> pymysql.connections.Connection:
    if DB_CONFIG is None:
        raise RuntimeError("DB 환경 변수가 설정되어 있지 않습니다.")
    return pymysql.connect(**DB_CONFIG)


def load_allowed_schema() -> None:
    global ALLOWED_TABLES, ALLOWED_COLUMNS

    with closing(get_connection()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT TABLE_NAME, COLUMN_NAME
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                ORDER BY TABLE_NAME, ORDINAL_POSITION
                """
            )
            rows = cursor.fetchall()

    allowed_columns: dict[str, set[str]] = {}

    for row in rows:
        table_name = row["TABLE_NAME"]
        column_name = row["COLUMN_NAME"]

        if column_name.lower() in DENIED_COLUMNS:
            continue

        allowed_columns.setdefault(table_name, set()).add(column_name)

    ALLOWED_TABLES = set(allowed_columns)
    ALLOWED_COLUMNS = allowed_columns


def validate_table_name(table_name: str) -> None:
    if table_name not in ALLOWED_TABLES:
        raise HTTPException(status_code=400, detail="허용되지 않은 테이블입니다.")


def validate_column_names(table_name: str, column_names: list[str]) -> None:
    validate_table_name(table_name)

    allowed_columns = ALLOWED_COLUMNS.get(table_name, set())
    invalid_columns = sorted(set(column_names) - allowed_columns)

    if invalid_columns:
        raise HTTPException(
            status_code=400,
            detail=f"허용되지 않은 컬럼입니다: {', '.join(invalid_columns)}",
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> Any:
    global DB_CONFIG
    DB_CONFIG = _load_db_config()
    if DB_CONFIG is not None:
        load_allowed_schema()
    yield


app = FastAPI(
    title="Yeouido Parking DB Connector",
    description="Simple FastAPI service for checking MySQL connectivity.",
    version="1.0.0",
    lifespan=lifespan,
)
include_routers(app, get_connection)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", summary="Service status")
def read_root() -> dict[str, str]:
    return {
        "message": "FastAPI is running.",
        "docs": "/docs",
        "openapi": "/openapi.json",
    }


@app.get("/db-check", summary="Check MySQL connection")
def db_check() -> dict[str, str]:
    if DB_CONFIG is None:
        raise HTTPException(
            status_code=503,
            detail="DB 환경 변수가 설정되어 있지 않아 DB 체크를 수행할 수 없습니다.",
        )
    try:
        with closing(get_connection()) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT VERSION() AS mysql_version")
                row = cursor.fetchone()

        return {
            "status": "connected",
            "mysql_version": row["mysql_version"] if row else "unknown",
        }
    except pymysql.MySQLError as exc:
        raise HTTPException(
            status_code=500, detail=f"MySQL connection failed: {exc}"
        ) from exc


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("fastApi:app", host="0.0.0.0", port=8000, reload=False)
