from __future__ import annotations

# 파일 생성일: 2026-04-11 12:01:57 KST
# 생성자: Chansol Park
# 설명: CRUD SQL문자동 생성 함수
# 변경 로그:
# - 2026-04-11 Codex: 깨져 있던 한글 문서 주석을 다시 작성
# - 2026-04-11 Codex: build_sql표기 기법을 snake_case로 바꿈
# - 2026-04-11 Codex: 기본적으로 WHERE 조건 없는 UPDATE와 DELETE 생성을 차단

from enum import Enum
from typing import Any, List, Optional, Sequence, Tuple


class CRUD(Enum):
    CREATE = "CREATE"
    READ = "READ"
    UPDATE = "UPDATE"
    DELETE = "DELETE"


class SQLBuilderError(ValueError):
    pass


def _validate_string_list(values: Optional[Sequence[str]], param_name: str) -> List[str]:
    if values is None:
        return []

    if not isinstance(values, (list, tuple)):
        raise SQLBuilderError(f"{param_name}은(는) 문자열 리스트여야 합니다.")

    result = list(values)

    for v in result:
        if not isinstance(v, str):
            raise SQLBuilderError(f"{param_name}에는 문자열만 포함할 수 있습니다.")
        if not v.strip():
            raise SQLBuilderError(f"{param_name}에는 빈 문자열을 포함할 수 없습니다.")

    return result


def _validate_value_list(values: Optional[Sequence[Any]], param_name: str) -> List[Any]:
    if values is None:
        return []

    if not isinstance(values, (list, tuple)):
        raise SQLBuilderError(f"{param_name}은(는) 리스트여야 합니다.")

    return list(values)


def _build_where_clause(attribute_names: List[str], attribute_values: List[Any]) -> Tuple[str, List[Any]]:
    if len(attribute_names) != len(attribute_values):
        raise SQLBuilderError(
            "condition_attribute_name과 condition_attribute_value의 길이는 같아야 합니다."
        )

    if not attribute_names:
        return "", []

    conditions = [f"{name} = %s" for name in attribute_names]
    clause = " WHERE " + " AND ".join(conditions)
    return clause, attribute_values


def _build_set_clause(attribute_names: List[str], attribute_values: List[Any]) -> Tuple[str, List[Any]]:
    if len(attribute_names) != len(attribute_values):
        raise SQLBuilderError("attribute_name과 attribute_value의 길이는 같아야 합니다.")

    if not attribute_names:
        raise SQLBuilderError("UPDATE에는 최소 하나 이상의 attribute_name이 필요합니다.")

    assignments = [f"{name} = %s" for name in attribute_names]
    clause = " SET " + ", ".join(assignments)
    return clause, attribute_values


def _build_insert_clause(attribute_names: List[str], attribute_values: List[Any]) -> Tuple[str, List[Any]]:
    if len(attribute_names) != len(attribute_values):
        raise SQLBuilderError("attribute_name과 attribute_value의 길이는 같아야 합니다.")

    if not attribute_names:
        raise SQLBuilderError("CREATE에는 attribute_name이 필요합니다.")
    if not attribute_values:
        raise SQLBuilderError("CREATE에는 attribute_value가 필요합니다.")

    columns = ", ".join(attribute_names)
    placeholders = ", ".join(["%s"] * len(attribute_names))
    clause = f" ({columns}) VALUES ({placeholders})"
    return clause, attribute_values


def build_sql(
    crud: CRUD,
    table_name: str,
    attribute_name: Optional[Sequence[str]] = None,
    attribute_value: Optional[Sequence[Any]] = None,
    condition_attribute_name: Optional[Sequence[str]] = None,
    condition_attribute_value: Optional[Sequence[Any]] = None,
    select_all_if_attribute_empty: bool = True,
    require_where_for_update_delete: bool = True,
    **legacy_kwargs: Any,
) -> Tuple[str, List[Any]]:
    """
    SQL 문자열과 순서가 보장된 바인딩 파라미터를 반환한다.

    규칙:
    - attribute_name은 컬럼명 리스트 또는 튜플이어야 한다.
    - attribute_value는 attribute_name과 같은 인덱스로 매칭된다.
    - condition_attribute_name과 condition_attribute_value는
      WHERE condition1 = value1 AND condition2 = value2 형태의 조건절을 만든다.
    - READ에서 attribute_name이 비어 있으면 select_all_if_attribute_empty가 False가 아닌 한
      SELECT *를 사용한다.
    - UPDATE는 알 수 없는 컬럼을 수정할 수 없으므로 항상 attribute_name이 필요하다.
    - UPDATE와 DELETE는 실수로 전체 테이블을 변경하지 않도록 기본적으로 WHERE 조건이 필요하다.
      전체 테이블 변경이 의도된 경우에만 require_where_for_update_delete=False를 전달한다.
    - 기존 camelCase 키워드 인자도 계속 허용한다:
      attributeName, attributeValue, conditionAttributeName, conditionAttributeValue.
    """

    if not isinstance(table_name, str) or not table_name.strip():
        raise SQLBuilderError("table_name은(는) 비어 있지 않은 문자열이어야 합니다.")

    if "attributeName" in legacy_kwargs:
        attribute_name = legacy_kwargs.pop("attributeName")
    if "attributeValue" in legacy_kwargs:
        attribute_value = legacy_kwargs.pop("attributeValue")
    if "conditionAttributeName" in legacy_kwargs:
        condition_attribute_name = legacy_kwargs.pop("conditionAttributeName")
    if "conditionAttributeValue" in legacy_kwargs:
        condition_attribute_value = legacy_kwargs.pop("conditionAttributeValue")
    if legacy_kwargs:
        unknown_args = ", ".join(sorted(legacy_kwargs))
        raise SQLBuilderError(f"지원하지 않는 키워드 인자입니다: {unknown_args}.")

    attr_names = _validate_string_list(attribute_name, "attribute_name")
    attr_values = _validate_value_list(attribute_value, "attribute_value")
    cond_names = _validate_string_list(condition_attribute_name, "condition_attribute_name")
    cond_values = _validate_value_list(condition_attribute_value, "condition_attribute_value")

    if crud == CRUD.CREATE:
        insert_clause, insert_params = _build_insert_clause(attr_names, attr_values)
        sql = f"INSERT INTO {table_name}{insert_clause};"
        return sql, insert_params

    if crud == CRUD.READ:
        if attr_names:
            select_clause = ", ".join(attr_names)
        else:
            if not select_all_if_attribute_empty:
                raise SQLBuilderError("attribute_name이 비어 있습니다.")
            select_clause = "*"

        where_clause, where_params = _build_where_clause(cond_names, cond_values)
        sql = f"SELECT {select_clause} FROM {table_name}{where_clause};"
        return sql, where_params

    if crud == CRUD.UPDATE:
        if not attr_names:
            raise SQLBuilderError(
                "attribute_name이 비어 있으면 UPDATE를 생성할 수 없습니다. "
                "실제 DB 컬럼명이 필요합니다."
            )
        if require_where_for_update_delete and not cond_names:
            raise SQLBuilderError("UPDATE는 기본적으로 WHERE 조건이 필요합니다.")

        set_clause, set_params = _build_set_clause(attr_names, attr_values)
        where_clause, where_params = _build_where_clause(cond_names, cond_values)

        sql = f"UPDATE {table_name}{set_clause}{where_clause};"
        return sql, set_params + where_params

    if crud == CRUD.DELETE:
        if require_where_for_update_delete and not cond_names:
            raise SQLBuilderError("DELETE는 기본적으로 WHERE 조건이 필요합니다.")

        where_clause, where_params = _build_where_clause(cond_names, cond_values)
        sql = f"DELETE FROM {table_name}{where_clause};"
        return sql, where_params

    raise SQLBuilderError("지원하지 않는 CRUD 타입입니다.")
