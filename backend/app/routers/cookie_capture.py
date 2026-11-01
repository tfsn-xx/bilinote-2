import os

from fastapi import APIRouter, HTTPException, Response
from app.services.cookie_capture import CookieCapture

router = APIRouter(prefix="/cookie_capture")
capture = CookieCapture()


def result(data):
    return {"code": 0, "msg": "success", "data": data}


def require_browser():
    if os.getenv("COOKIE_BROWSER_ENABLED") != "true":
        raise HTTPException(503, "请使用包含 Cookie 登录服务的 Docker 镜像。")


@router.post("/capture/{platform}")
def start(platform: str, response: Response):
    require_browser()
    response.headers["Cache-Control"] = "no-store"
    try:
        return result(capture.start(platform))
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except (RuntimeError, OSError, TimeoutError) as error:
        raise HTTPException(503, str(error)) from error


@router.post("/session/{session_id}")
def poll(session_id: str, response: Response):
    require_browser()
    response.headers["Cache-Control"] = "no-store"
    try:
        return result(capture.poll(session_id))
    except ValueError as error:
        raise HTTPException(410, str(error)) from error
    except (RuntimeError, OSError, TimeoutError) as error:
        raise HTTPException(503, str(error)) from error


@router.post("/session/{session_id}/cancel")
def cancel(session_id: str):
    return result(capture.cancel(session_id))
