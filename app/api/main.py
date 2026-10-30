"""HTTP API: health checks and (disabled) payment-provider webhooks.

Run: ``uvicorn app.api.main:app --host 0.0.0.0 --port 8080``.
The Telegram bot uses long polling, so no Telegram webhook endpoint is exposed.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request

from app.config import get_settings
from app.container import build_container
from app.logging_setup import setup_logging
from app.services.crypto import CryptoDisabled, MockCryptoProvider, WebhookRejected

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level)
    app.state.settings = settings
    app.state.container = build_container(settings)
    yield
    await app.state.container.close()


app = FastAPI(title="telegram-market", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/health")
async def health() -> dict:
    """Liveness: the process is up."""
    return {"status": "ok"}


@app.get("/ready")
async def ready(request: Request) -> dict:
    """Readiness: database and Redis reachable. Details are logged, not returned."""
    c = request.app.state.container
    checks = {}
    try:
        await c.repo.ping()
        checks["database"] = "ok"
    except Exception:
        log.exception("database not ready")
        checks["database"] = "error"
    try:
        if c.redis is not None:
            await c.redis.ping()
        checks["redis"] = "ok" if c.redis is not None else "absent"
    except Exception:
        log.exception("redis not ready")
        checks["redis"] = "error"
    if "error" in checks.values():
        raise HTTPException(status_code=503, detail=checks)
    return {"status": "ok", **checks}


@app.post("/webhooks/crypto")
async def crypto_webhook(request: Request, x_signature: str = Header(default="")) -> dict:
    """Disabled unless CRYPTO_ENABLED=true (see docs/PAYMENTS.md). Never grants access by itself."""
    settings = request.app.state.settings
    if not settings.crypto_enabled:
        raise HTTPException(status_code=404)
    provider = MockCryptoProvider(settings.crypto_webhook_secret, enabled=True)
    body = await request.body()
    try:
        event = provider.parse_webhook(body, x_signature)
    except (WebhookRejected, CryptoDisabled):
        raise HTTPException(status_code=400, detail="rejected")
    c = request.app.state.container
    from app.domain.timing import utcnow

    _, is_new = await c.repo.record_payment_event("crypto", event.status, event.event_id,
                                                  {"invoice_id": event.invoice_id, "status": event.status}, utcnow())
    # Fulfilment of crypto payments is intentionally not implemented (blocked by Telegram rules).
    return {"received": True, "duplicate": not is_new}
