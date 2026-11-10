from __future__ import annotations

import re

MAX_SHEET_ROWS = 501
MAX_SHEET_COLUMNS = 26
MAX_ALLOWED_COLUMNS = 12
MAX_MATCHED_ROWS = 5
MAX_CELL_CHARS = 300
_SHEET_ID = re.compile(r"[A-Za-z0-9_-]{20,256}")
_A1_RANGE = re.compile(
    r"(?P<sheet>'(?:[^']|'')+'|[A-Za-z0-9_-]{1,100})!A1:"
    r"(?P<end_column>[A-Z]{1,2})(?P<end_row>[1-9][0-9]{0,2})"
)


def validate_sheet_id(spreadsheet_id: str) -> str:
    if not _SHEET_ID.fullmatch(spreadsheet_id):
        raise ValueError("Invalid spreadsheet_id")
    return spreadsheet_id


def validate_range_name(range_name: str) -> str:
    match = _A1_RANGE.fullmatch(range_name)
    if match is None:
        raise ValueError("Invalid range; use a bounded A1 range starting at A1")
    end_row = int(match.group("end_row"))
    end_column = _column_number(match.group("end_column"))
    if not 2 <= end_row <= MAX_SHEET_ROWS or end_column > MAX_SHEET_COLUMNS:
        raise ValueError("range exceeds the allowed row or column limit")
    if len(match.group("sheet")) > 100:
        raise ValueError("Sheet name is too long")
    return range_name


def _column_number(column: str) -> int:
    number = 0
    for char in column:
        number = number * 26 + ord(char) - ord("A") + 1
    return number


def search_values(
    values: list[list[object]], allowed_columns: list[str], query: str
) -> list[dict[str, str]]:
    if not isinstance(query, str) or not 2 <= len(query.strip()) <= 200:
        raise ValueError("Invalid query")
    columns = [column.strip() for column in allowed_columns]
    if (
        not 1 <= len(columns) <= MAX_ALLOWED_COLUMNS
        or any(not column or len(column) > 100 for column in columns)
        or len(set(columns)) != len(columns)
    ):
        raise ValueError("Invalid allowed columns")
    if not values:
        return []
    headers = [str(value).strip() for value in values[0]]
    if len(set(headers)) != len(headers) or any(column not in headers for column in columns):
        raise ValueError("Configured columns do not match unique sheet headers")
    indexes = {column: headers.index(column) for column in columns}
    terms = set(re.findall(r"\w{2,}", query.casefold(), flags=re.UNICODE))
    if not terms:
        raise ValueError("Invalid query")

    ranked: list[tuple[int, int, dict[str, str]]] = []
    for row_index, row in enumerate(values[1:], start=2):
        record = {
            column: str(row[indexes[column]])[:MAX_CELL_CHARS] if indexes[column] < len(row) else ""
            for column in columns
        }
        searchable = " ".join(record.values()).casefold()
        score = sum(term in searchable for term in terms)
        if score:
            ranked.append((score, row_index, {"linha": str(row_index), **record}))
    ranked.sort(key=lambda result: (-result[0], result[1]))
    return [record for _, _, record in ranked[:MAX_MATCHED_ROWS]]
