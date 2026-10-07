import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import models  # noqa: F401  (registers tables on Base.metadata)
from app.api import files
from app.config import settings
from app.db import Base, engine
from app.exceptions import AppError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)  # swap for Alembic migrations if the schema starts evolving
    yield


app = FastAPI(
    title="GeoMeasure API",
    version="1.0.0",
    description=(
        "Upload a Shapefile (.zip) or KML, extract its features and get area / length "
        "measurements computed in a suitable projected CRS."
    ),
    lifespan=lifespan,
)


@app.exception_handler(AppError)
async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(files.router)
