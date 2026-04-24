from fastapi import FastAPI

from api.routes.health import router as health_router
from api.routes.webhooks import router as webhooks_router
from shared.logging import configure_logging

configure_logging()

app = FastAPI(title="dan-agent", version="0.1.0")
app.include_router(health_router)
app.include_router(webhooks_router)
