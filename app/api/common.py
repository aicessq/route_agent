from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse


def success(data: Any, *, status_code: int = 200) -> dict[str, Any]:
    return {"code": 0, "message": "ok", "data": data}


def error_response(*, status_code: int, code: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message, "data": None},
    )
