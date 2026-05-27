from __future__ import annotations

from contextlib import closing
from typing import Any

import pymysql
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from sql_creator import CRUD, SQLBuilderError, build_sql


router = APIRouter(prefix="/facilities", tags=["facilities"])

FACILITY_TABLE = "facility"

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


def execute_write(sql: str, params: list[Any]) -> int:
    try:
        with closing(get_connection()) as connection:
            with connection.cursor() as cursor:
                affected_rows = cursor.execute(sql, params)
            connection.commit()
            return affected_rows
    except pymysql.MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"DB 작업 실패: {exc}") from exc


class FacilityCreateRequest(BaseModel):
    f_lat: float
    f_long: float
    f_name: str
    f_info: str | None = None
    f_image: str | None = None
    f_possible: int = 0


class FacilityUpdateRequest(BaseModel):
    f_lat: float | None = None
    f_long: float | None = None
    f_name: str | None = None
    f_info: str | None = None
    f_image: str | None = None
    f_possible: int | None = None


# 시설 탭용: 전체 시설 조회
@router.get("")
def get_all_facilities() -> list[dict]:
    try:
        sql, params = build_sql(
            CRUD.READ,
            FACILITY_TABLE,
            select_all_if_attribute_empty=True,
        )
    except SQLBuilderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return execute_read(sql, params)


# 예약 탭용: 예약 가능한 시설만 조회
@router.get("/reservable")
def get_reservable_facilities() -> list[dict]:
    try:
        sql, params = build_sql(
            CRUD.READ,
            FACILITY_TABLE,
            condition_attribute_name=["f_possible"],
            condition_attribute_value=[1],
            select_all_if_attribute_empty=True,
        )
    except SQLBuilderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return execute_read(sql, params)


@router.post("", status_code=201)
def create_facility(request: FacilityCreateRequest) -> dict:
    try:
        sql, params = build_sql(
            CRUD.CREATE,
            FACILITY_TABLE,
            attribute_name=["f_lat", "f_long", "f_name", "f_info", "f_image", "f_possible"],
            attribute_value=[
                request.f_lat,
                request.f_long,
                request.f_name,
                request.f_info,
                request.f_image,
                request.f_possible,
            ],
        )
    except SQLBuilderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        with closing(get_connection()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                facility_id = cursor.lastrowid
            connection.commit()
    except pymysql.MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"시설 생성 실패: {exc}") from exc

    return {
        "f_id": facility_id,
        "f_lat": request.f_lat,
        "f_long": request.f_long,
        "f_name": request.f_name,
        "f_info": request.f_info,
        "f_image": request.f_image,
        "f_possible": request.f_possible,
    }


@router.get("/{facility_id}")
def get_facility_detail(facility_id: int) -> dict:
    try:
        sql, params = build_sql(
            CRUD.READ,
            FACILITY_TABLE,
            condition_attribute_name=["f_id"],
            condition_attribute_value=[facility_id],
            select_all_if_attribute_empty=True,
        )
    except SQLBuilderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    rows = execute_read(sql, params)

    if not rows:
        raise HTTPException(status_code=404, detail="시설 없음")

    return rows[0]


@router.patch("/{facility_id}")
def update_facility(facility_id: int, request: FacilityUpdateRequest) -> dict[str, Any]:
    update_data = request.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="수정할 값이 없습니다.")

    try:
        sql, params = build_sql(
            CRUD.UPDATE,
            FACILITY_TABLE,
            attribute_name=list(update_data.keys()),
            attribute_value=list(update_data.values()),
            condition_attribute_name=["f_id"],
            condition_attribute_value=[facility_id],
        )
    except SQLBuilderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    affected = execute_write(sql, params)
    if affected == 0:
        raise HTTPException(status_code=404, detail="시설 없음")

    return {"status": "updated", "affected_rows": affected, "f_id": facility_id}
