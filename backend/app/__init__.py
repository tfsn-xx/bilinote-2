from fastapi import FastAPI
from contextlib import asynccontextmanager
from starlette.concurrency import run_in_threadpool

from .routers import note, provider, model, config, chat, cookie_capture



def create_app(lifespan) -> FastAPI:
    @asynccontextmanager
    async def managed_lifespan(app):
        async with lifespan(app):
            try:
                yield
            finally:
                # Flush the login profile before Docker stops the container.
                await run_in_threadpool(cookie_capture.capture.close)

    app = FastAPI(title="BiliNote",lifespan=managed_lifespan)
    app.include_router(note.router, prefix="/api")
    app.include_router(provider.router, prefix="/api")
    app.include_router(model.router,prefix="/api")
    app.include_router(config.router,  prefix="/api")
    app.include_router(chat.router, prefix="/api")
    app.include_router(cookie_capture.router, prefix="/api")

    return app
