#exception/exception_handler.py
from fastapi import Request
from fastapi.responses import JSONResponse


# =======================
# Central Exception Classes
# =======================

class ValidationError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code


class NetworkException(Exception):
    def __init__(self, message: str = "Network error occurred", status_code: int = 502):
        self.message = message
        self.status_code = status_code


class SelectorException(Exception):
    def __init__(self, message: str = "selector errors", status_code: int = 400):
        self.message = message
        self.status_code = status_code


# =======================
# Centralized Exception Handlers
# =======================

async def validation_exception_handler(request: Request, exc: ValidationError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.message}
    )


async def network_exception_handler(request: Request, exc: NetworkException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.message}
    )


async def selection_exception_handler(request: Request, exc: SelectorException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error", exc.message}
    )


async def generic_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"error": f"Internal Server Error: {str(exc)}"}
    )
