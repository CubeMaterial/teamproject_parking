from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import logging
import os
import re
import socket
import ssl
from typing import Any

import certifi
import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, HTTPException
import pymysql
from pydantic import BaseModel

from sql_creator import CRUD, SQLBuilderError, build_sql

router = APIRouter(prefix="/parking", tags=["parking"])

REGION8_URL = "https://www.ihangangpark.kr/parking/region/region8"
PARKING_TABLE = "parkinglot"

logger = logging.getLogger(__name__)

connection_factory: Any = None


def set_connection_factory(factory: Any) -> None:
    global connection_factory
    connection_factory = factory


def get_connection() -> pymysql.connections.Connection:
    if connection_factory is None:
        raise RuntimeError("DB 연결 팩토리가 설정되어 있지 않습니다.")
    return connection_factory()


def execute_read(sql: str, params: list[Any]) -> list[dict]:
    try:
        with closing(get_connection()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                return cursor.fetchall()
    except pymysql.MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"DB 조회 실패: {exc}") from exc


def execute_read_one(sql: str, params: list[Any]) -> dict[str, Any] | None:
    try:
        with closing(get_connection()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                return cursor.fetchone()
    except pymysql.MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"DB 조회 실패: {exc}") from exc


def execute_write(sql: str, params: list[Any]) -> int:
    try:
        with closing(get_connection()) as connection:
            with connection.cursor() as cursor:
                affected_rows = cursor.execute(sql, params)
            connection.commit()
            return affected_rows
    except pymysql.MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"DB 작업 실패: {exc}") from exc


class ParkingCreateRequest(BaseModel):
    parkinglot_lat: float | None = None
    parkinglot_long: float | None = None
    parkinglot_name: str | None = None
    parkinglot_max: int | None = None

    # Frontend-friendly aliases
    parking_lat: float | None = None
    parking_lng: float | None = None
    parking_name: str | None = None
    parking_max: int | None = None


class ParkingUpdateRequest(BaseModel):
    parkinglot_lat: float | None = None
    parkinglot_long: float | None = None
    parkinglot_name: str | None = None
    parkinglot_max: int | None = None

    # Frontend-friendly aliases
    parking_lat: float | None = None
    parking_lng: float | None = None
    parking_name: str | None = None
    parking_max: int | None = None


def _pick(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    parking_id = row.get("parkinglot_id")
    parking_lat = row.get("parkinglot_lat")
    parking_lng = row.get("parkinglot_long")
    parking_name = row.get("parkinglot_name")
    parking_max = row.get("parkinglot_max")

    return {
        **row,
        "parking_id": parking_id,
        "parking_lat": parking_lat,
        "parking_lng": parking_lng,
        "parking_name": parking_name,
        "parking_max": parking_max,
    }


@router.get("")
@router.get("/")
def get_all_parkinglots() -> list[dict]:
    try:
        sql, params = build_sql(
            CRUD.READ,
            PARKING_TABLE,
            select_all_if_attribute_empty=True,
        )
    except SQLBuilderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    rows = execute_read(sql, params)
    return [_normalize_row(r) for r in rows]


@router.get("/{parkinglot_id:int}")
def get_parkinglot_detail(parkinglot_id: int) -> dict:
    try:
        sql, params = build_sql(
            CRUD.READ,
            PARKING_TABLE,
            condition_attribute_name=["parkinglot_id"],
            condition_attribute_value=[parkinglot_id],
            select_all_if_attribute_empty=True,
        )
    except SQLBuilderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    row = execute_read_one(sql, params)
    if row is None:
        raise HTTPException(status_code=404, detail="주차장 없음")
    return _normalize_row(row)


@router.post("", status_code=201)
@router.post("/", status_code=201)
def create_parkinglot(request: ParkingCreateRequest) -> dict[str, Any]:
    lat = _pick(request.parkinglot_lat, request.parking_lat)
    lng = _pick(request.parkinglot_long, request.parking_lng)
    name = _pick(request.parkinglot_name, request.parking_name)
    max_count = _pick(request.parkinglot_max, request.parking_max)

    if lat is None or lng is None:
        raise HTTPException(status_code=400, detail="parkinglot_lat/parkinglot_long 값이 필요합니다.")
    if name is None or str(name).strip() == "":
        raise HTTPException(status_code=400, detail="parkinglot_name 값이 필요합니다.")
    if max_count is None:
        raise HTTPException(status_code=400, detail="parkinglot_max 값이 필요합니다.")

    try:
        sql, params = build_sql(
            CRUD.CREATE,
            PARKING_TABLE,
            attribute_name=[
                "parkinglot_lat",
                "parkinglot_long",
                "parkinglot_name",
                "parkinglot_max",
            ],
            attribute_value=[lat, lng, str(name).strip(), int(max_count)],
        )
    except (SQLBuilderError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        with closing(get_connection()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                created_id = cursor.lastrowid
            connection.commit()
    except pymysql.MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"주차장 생성 실패: {exc}") from exc

    return _normalize_row(
        {
            "parkinglot_id": created_id,
            "parkinglot_lat": lat,
            "parkinglot_long": lng,
            "parkinglot_name": str(name).strip(),
            "parkinglot_max": int(max_count),
        }
    )


@router.patch("/{parkinglot_id:int}")
def update_parkinglot(parkinglot_id: int, request: ParkingUpdateRequest) -> dict[str, Any]:
    update_data: dict[str, Any] = {}

    lat = _pick(request.parkinglot_lat, request.parking_lat)
    lng = _pick(request.parkinglot_long, request.parking_lng)
    name = _pick(request.parkinglot_name, request.parking_name)
    max_count = _pick(request.parkinglot_max, request.parking_max)

    if lat is not None:
        update_data["parkinglot_lat"] = lat
    if lng is not None:
        update_data["parkinglot_long"] = lng
    if name is not None:
        update_data["parkinglot_name"] = str(name).strip()
    if max_count is not None:
        update_data["parkinglot_max"] = int(max_count)

    update_data = {k: v for k, v in update_data.items() if v is not None}
    if not update_data:
        raise HTTPException(status_code=400, detail="수정할 값이 없습니다.")

    try:
        sql, params = build_sql(
            CRUD.UPDATE,
            PARKING_TABLE,
            attribute_name=list(update_data.keys()),
            attribute_value=list(update_data.values()),
            condition_attribute_name=["parkinglot_id"],
            condition_attribute_value=[parkinglot_id],
        )
    except SQLBuilderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    affected = execute_write(sql, params)
    if affected == 0:
        raise HTTPException(status_code=404, detail="주차장 없음")
    return {"status": "updated", "affected_rows": affected, "parkinglot_id": parkinglot_id}


@router.delete("/{parkinglot_id:int}")
def delete_parkinglot(parkinglot_id: int) -> dict[str, Any]:
    try:
        sql, params = build_sql(
            CRUD.DELETE,
            PARKING_TABLE,
            condition_attribute_name=["parkinglot_id"],
            condition_attribute_value=[parkinglot_id],
        )
    except SQLBuilderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    affected = execute_write(sql, params)
    if affected == 0:
        raise HTTPException(status_code=404, detail="주차장 없음")
    return {"status": "deleted", "affected_rows": affected, "parkinglot_id": parkinglot_id}


def _upstream_verify_setting() -> str | bool | ssl.SSLContext:
    ca_bundle = os.getenv("UPSTREAM_CA_BUNDLE")
    if ca_bundle:
        return ca_bundle
    if os.getenv("UPSTREAM_INSECURE_SKIP_VERIFY") == "1":
        return False
    return certifi.where()


def _parse_int(text: str) -> int:
    digits = re.sub(r"[^0-9]", "", text or "")
    if not digits:
        return 0
    try:
        return int(digits)
    except ValueError:
        return 0


@router.get("/region8")
async def region8() -> dict:
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=5.0),
            verify=_upstream_verify_setting(),
            trust_env=False,
            headers={
                "User-Agent": "Mozilla/5.0 (YeouidoParkingFastAPI)",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
                "Referer": "https://www.ihangangpark.kr/",
            },
            follow_redirects=True,
        ) as client:
            response = await client.get(REGION8_URL)
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        upstream_status = exc.response.status_code
        logger.warning(
            "Upstream returned error status=%s url=%s",
            upstream_status,
            REGION8_URL,
            exc_info=True,
        )
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Upstream returned non-2xx status",
                "upstream_status": upstream_status,
                "source_url": REGION8_URL,
            },
        ) from exc
    except httpx.ConnectError as exc:
        cause = exc.__cause__
        errno = getattr(cause, "errno", None)
        is_dns_error = isinstance(cause, socket.gaierror)
        is_ssl_verify_error = isinstance(cause, ssl.SSLCertVerificationError)
        logger.warning("Upstream connect failed url=%s", REGION8_URL, exc_info=True)
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Upstream connect failed",
                "error": str(exc),
                "errno": errno,
                "hint": (
                    "DNS lookup failed"
                    if is_dns_error
                    else "TLS cert verify failed (set UPSTREAM_CA_BUNDLE or UPSTREAM_INSECURE_SKIP_VERIFY=1)"
                    if is_ssl_verify_error
                    else None
                ),
                "source_url": REGION8_URL,
            },
        ) from exc
    except httpx.HTTPError as exc:
        logger.warning("Upstream request failed url=%s", REGION8_URL, exc_info=True)
        raise HTTPException(
            status_code=502,
            detail={
                "message": f"Upstream request failed ({type(exc).__name__})",
                "error": str(exc),
                "source_url": REGION8_URL,
            },
        ) from exc

    soup = BeautifulSoup(response.text, "html.parser")
    tab = soup.select_one("#regionTab01")
    rows = tab.select("table tbody tr") if tab else []

    lots: list[dict] = []
    for row in rows:
        cells = [td.get_text(strip=True) for td in row.select("td")]
        if len(cells) < 4:
            continue

        name = cells[0].strip() or "주차장"
        total = _parse_int(cells[1])
        used = _parse_int(cells[2])
        available = _parse_int(cells[3])

        if used == 0 and total > 0 and available > 0:
            used = max(0, total - available)

        lots.append(
            {
                "name": name,
                "total": total,
                "used": used,
                "available": available,
                "raw": cells[:4],
            }
        )

    return {
        "source_url": REGION8_URL,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "lots": lots,
    }
