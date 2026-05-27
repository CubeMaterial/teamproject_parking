from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta
from typing import Any

import pymysql
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, model_validator

from sql_creator import CRUD, SQLBuilderError, build_sql


router = APIRouter(prefix="/reservation", tags=["reservation"])

RESERVATION_TABLE = "reservation"

RESERVATION_CREATE_COLUMNS = [
    "reservation_start_date",
    "reservation_end_date",
    "reservation_state",
    "reservation_date",
    "user_id",
    "facility_id",
]

connection_factory: Any = None


class ReservationCreateRequest(BaseModel):
    user_id: int
    facility_id: int
    start_date: datetime
    end_date: datetime

    @model_validator(mode="after")
    def validate_dates(self) -> "ReservationCreateRequest":
        now = datetime.now()

        if self.start_date <= now:
            raise ValueError("예약 시작 시간은 현재 시간 이후여야 합니다.")

        if self.end_date <= self.start_date:
            raise ValueError("예약 종료 시간은 시작 시간보다 늦어야 합니다.")

        if self.end_date - self.start_date > timedelta(hours=24):
            raise ValueError("예약은 최대 24시간까지만 가능합니다.")

        return self


class ReservationStateUpdateRequest(BaseModel):
    reservation_state: int

    @model_validator(mode="after")
    def validate_state(self) -> "ReservationStateUpdateRequest":
        if self.reservation_state not in {1, 2, 3, 4}:
            raise ValueError("reservation_state는 1(대기), 2(승인 완료), 3(반려), 4(완료) 중 하나여야 합니다.")
        return self


def set_connection_factory(factory: Any) -> None:
    global connection_factory
    connection_factory = factory


def get_connection() -> pymysql.connections.Connection:
    if connection_factory is None:
        raise RuntimeError("DB 연결 팩토리가 설정되어 있지 않습니다.")
    return connection_factory()


def execute_write(sql: str, params: list[Any]) -> int:
    try:
        with closing(get_connection()) as connection:
            with connection.cursor() as cursor:
                affected_rows = cursor.execute(sql, params)
            connection.commit()
            return affected_rows
    except pymysql.MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"DB 작업 실패: {exc}") from exc


def execute_read(sql: str, params: list[Any]) -> list[dict]:
    try:
        with closing(get_connection()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                return cursor.fetchall()
    except pymysql.MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"DB 조회 실패: {exc}") from exc


def has_overlapping_reservation(
    facility_id: int,
    start_date: datetime,
    end_date: datetime,
) -> bool:
    """
    겹침 조건:
    기존.start < 새.end AND 기존.end > 새.start
    """
    try:
        with closing(get_connection()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM reservation
                    WHERE facility_id = %s
                      AND reservation_state IN (1, 2)
                      AND reservation_start_date < %s
                      AND reservation_end_date > %s
                    """,
                    (facility_id, end_date, start_date),
                )
                row = cursor.fetchone()
                return (row or {}).get("cnt", 0) > 0
    except pymysql.MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"예약 중복 확인 실패: {exc}") from exc


@router.post("", status_code=201)
def create_reservation(request: ReservationCreateRequest) -> dict:
    if has_overlapping_reservation(
        facility_id=request.facility_id,
        start_date=request.start_date,
        end_date=request.end_date,
    ):
        raise HTTPException(status_code=400, detail="해당 시간대에 이미 예약이 있습니다.")

    try:
        created_at = datetime.now()

        sql, params = build_sql(
            CRUD.CREATE,
            RESERVATION_TABLE,
            attribute_name=RESERVATION_CREATE_COLUMNS,
            attribute_value=[
                request.start_date,
                request.end_date,
                1,  # 완료/활성 예약
                created_at,
                request.user_id,
                request.facility_id,
            ],
        )
    except SQLBuilderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        with closing(get_connection()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                reservation_id = cursor.lastrowid
            connection.commit()
    except pymysql.MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"예약 생성 실패: {exc}") from exc

    return {
        "reservation_id": reservation_id,
        "reservation_start_date": request.start_date,
        "reservation_end_date": request.end_date,
        "reservation_state": 1,
        "reservation_date": created_at,
        "user_id": request.user_id,
        "facility_id": request.facility_id,
    }


@router.get("")
def list_reservations(
    limit: int = 50,
    offset: int = 0,
    state: int | None = None,
) -> list[dict]:
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    if state is not None and state not in {1, 2, 3, 4}:
        raise HTTPException(
            status_code=400,
            detail="state는 1(대기), 2(승인 완료), 3(반려), 4(완료) 중 하나여야 합니다.",
        )

    try:
        with closing(get_connection()) as connection:
            with connection.cursor() as cursor:
                where_sql = ""
                params: tuple[Any, ...]
                if state is not None:
                    where_sql = "WHERE reservation_state = %s"
                    params = (state, limit, offset)
                else:
                    params = (limit, offset)
                cursor.execute(
                    f"""
                    SELECT
                        reservation_id,
                        reservation_start_date,
                        reservation_end_date,
                        reservation_state,
                        reservation_date,
                        user_id,
                        facility_id
                    FROM reservation
                    {where_sql}
                    ORDER BY reservation_date DESC
                    LIMIT %s OFFSET %s
                    """,
                    params,
                )
                return cursor.fetchall()
    except pymysql.MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"예약 목록 조회 실패: {exc}") from exc


@router.get("/stats/dashboard")
def reservation_dashboard(top: int = 3, year: int | None = None, month: int | None = None) -> dict[str, Any]:
    """
    관리자 대시보드용 통계.
    - top_facilities: 전체 예약 건수 기준 상위 시설 N개
    - monthly_counts: 해당 월(기본: 현재 월)의 전체/상태별 건수

    reservation_state:
      1 = 대기
      2 = 승인 완료
      3 = 반려
      4 = 완료
    """
    top = max(1, min(top, 10))

    now = datetime.now()
    year = year or now.year
    month = month or now.month
    if month < 1 or month > 12:
        raise HTTPException(status_code=400, detail="month는 1~12 사이여야 합니다.")

    try:
        with closing(get_connection()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                      r.facility_id AS facility_id,
                      f.f_name AS facility_name,
                      COUNT(*) AS cnt
                    FROM reservation r
                    LEFT JOIN facility f ON r.facility_id = f.f_id
                    GROUP BY r.facility_id, f.f_name
                    ORDER BY cnt DESC
                    LIMIT %s
                    """,
                    (top,),
                )
                top_rows = cursor.fetchall() or []

                cursor.execute(
                    """
                    SELECT reservation_state, COUNT(*) AS cnt
                    FROM reservation
                    WHERE YEAR(reservation_date) = %s AND MONTH(reservation_date) = %s
                    GROUP BY reservation_state
                    """,
                    (year, month),
                )
                state_rows = cursor.fetchall() or []
    except pymysql.MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"예약 통계 조회 실패: {exc}") from exc

    by_state = {int(row["reservation_state"]): int(row["cnt"]) for row in state_rows}

    waiting = by_state.get(1, 0)
    approved = by_state.get(2, 0)
    rejected = by_state.get(3, 0)
    completed = by_state.get(4, 0)

    return {
        "month": f"{year:04d}-{month:02d}",
        "top_facilities": [
            {
                "facility_id": row.get("facility_id"),
                "facility_name": row.get("facility_name"),
                "count": int(row.get("cnt", 0)),
            }
            for row in top_rows
        ],
        "monthly_counts": {
            "total": waiting + approved + rejected + completed,
            "waiting": waiting,
            "approved": approved,
            "rejected": rejected,
            "completed": completed,
        },
    }


@router.get("/stats/summary")
def reservation_summary() -> dict[str, int]:
    """
    예약 리스트 화면 상단 요약용 통계.

    reservation_state:
      1 = 대기
      2 = 승인 완료
      3 = 반려
      4 = 완료
    """
    try:
        rows = execute_read(
            """
            SELECT reservation_state, COUNT(*) AS cnt
            FROM reservation
            GROUP BY reservation_state
            """,
            [],
        )
    except HTTPException:
        raise

    by_state = {int(row["reservation_state"]): int(row["cnt"]) for row in (rows or [])}

    waiting = by_state.get(1, 0)
    approved = by_state.get(2, 0)
    rejected = by_state.get(3, 0)
    completed = by_state.get(4, 0)

    return {
        "total": waiting + approved + rejected + completed,
        "waiting": waiting,
        "approved": approved,
        "rejected": rejected,
        "completed": completed,
    }


@router.get("/user/{user_id}")
def get_reservations(user_id: int, state: int | None = None) -> list[dict]:
    if state is not None and state not in {1, 2, 3, 4}:
        raise HTTPException(
            status_code=400,
            detail="state는 1(대기), 2(승인 완료), 3(반려), 4(완료) 중 하나여야 합니다.",
        )
    try:
        condition_names = ["user_id"]
        condition_values: list[Any] = [user_id]
        if state is not None:
            condition_names.append("reservation_state")
            condition_values.append(state)
        sql, params = build_sql(
            CRUD.READ,
            RESERVATION_TABLE,
            condition_attribute_name=condition_names,
            condition_attribute_value=condition_values,
        )
    except SQLBuilderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return execute_read(sql, params)


@router.get("/{reservation_id}")
def get_reservation_detail(reservation_id: int) -> dict:
    try:
        with closing(get_connection()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT 
                        r.reservation_id,
                        r.reservation_start_date,
                        r.reservation_end_date,
                        r.reservation_state,
                        r.reservation_date,
                        r.user_id,
                        r.facility_id,
                        f.f_name AS facility_name,
                        f.f_info AS facility_info,
                        f.f_image AS facility_image
                    FROM reservation r
                    JOIN facility f ON r.facility_id = f.f_id
                    WHERE r.reservation_id = %s
                    """,
                    (reservation_id,),
                )
                row = cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="예약 없음")

        return row

    except pymysql.MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"예약 상세 조회 실패: {exc}") from exc


@router.patch("/{reservation_id}/state")
def update_reservation_state(reservation_id: int, request: ReservationStateUpdateRequest) -> dict[str, Any]:
    return _set_reservation_state(reservation_id, request.reservation_state)


@router.post("/{reservation_id}/approve")
def approve_reservation(reservation_id: int) -> dict[str, Any]:
    """
    관리자 승인 처리.
    reservation_state:
      1 = 대기
      2 = 승인 완료
      3 = 반려
      4 = 완료
    """
    return _set_reservation_state(reservation_id, 2)


@router.post("/{reservation_id}/reject")
def reject_reservation(reservation_id: int) -> dict[str, Any]:
    """관리자 반려 처리."""
    return _set_reservation_state(reservation_id, 3)


def _set_reservation_state(reservation_id: int, reservation_state: int) -> dict[str, Any]:
    try:
        sql, params = build_sql(
            CRUD.UPDATE,
            RESERVATION_TABLE,
            attribute_name=["reservation_state"],
            attribute_value=[reservation_state],
            condition_attribute_name=["reservation_id"],
            condition_attribute_value=[reservation_id],
        )
    except SQLBuilderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    affected = execute_write(sql, params)
    if affected == 0:
        raise HTTPException(status_code=404, detail="예약 없음")

    return {
        "status": "updated",
        "affected_rows": affected,
        "reservation_id": reservation_id,
        "reservation_state": reservation_state,
    }


@router.put("/{reservation_id}")
def cancel_reservation(reservation_id: int) -> dict[str, Any]:
    try:
        sql, params = build_sql(
            CRUD.UPDATE,
            RESERVATION_TABLE,
            attribute_name=["reservation_state"],
            attribute_value=[0],
            condition_attribute_name=["reservation_id"],
            condition_attribute_value=[reservation_id],
        )
    except SQLBuilderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    affected = execute_write(sql, params)

    if affected == 0:
        raise HTTPException(status_code=404, detail="예약을 찾을 수 없습니다.")

    return {
        "status": "cancelled",
        "affected_rows": affected
    }


@router.get("/facility/{facility_id}/date/{target_date}")
def get_reservations_by_facility_and_date(facility_id: int, target_date: str) -> list[dict]:
    """
    target_date 형식: YYYY-MM-DD
    해당 날짜에 걸쳐 있는 예약 목록 반환
    """
    try:
        day_start = datetime.strptime(target_date, "%Y-%m-%d")
        day_end = day_start + timedelta(days=1)
    except ValueError:
        raise HTTPException(status_code=400, detail="날짜 형식은 YYYY-MM-DD 이어야 합니다.")

    try:
        with closing(get_connection()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        reservation_id,
                        reservation_start_date,
                        reservation_end_date,
                        reservation_state,
                        reservation_date,
                        user_id,
                        facility_id
                    FROM reservation
                    WHERE facility_id = %s
                      AND reservation_state IN (1, 2)
                      AND reservation_start_date < %s
                      AND reservation_end_date > %s
                    ORDER BY reservation_start_date
                    """,
                    (facility_id, day_end, day_start),
                )
                rows = cursor.fetchall()
                return rows
    except pymysql.MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"예약 조회 실패: {exc}") from exc
