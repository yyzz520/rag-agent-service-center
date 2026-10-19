from fastapi.responses import JSONResponse


def ok(data=None):
    """统一成功响应：{"code":0,"message":"success","data":...}"""
    return {"code": 0, "message": "success", "data": data}


def error(code: int, message: str, status: int = 400) -> JSONResponse:
    """统一错误响应：{"code":...,"message":"...","data":null}"""
    return JSONResponse(
        status_code=status,
        content={"code": code, "message": message, "data": None},
    )
