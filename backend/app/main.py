import asyncio
import logging
import os
import time
import json
from contextlib import suppress, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .extraction import extract_html
from .security import UnsafeURL
from .service import ImportError, result_payload
from .engine import execute, ScrapeRequest, load_settings
from .providers import ProviderError
from .scraping_api import router, shutdown_jobs

async def import_profile(url, render_js=None, follow_links=True, on_progress=None):
    return await execute(ScrapeRequest(input=url,renderJs=render_js,followLinks=follow_links,bypassCache=False),on_progress)

@asynccontextmanager
async def lifespan(app):
    yield
    await shutdown_jobs()

app = FastAPI(lifespan=lifespan, title="SKRIV Scrapper", version="2.0.0", description="Self-hosted business profile extraction")
logger = logging.getLogger("profilekit")
logging.getLogger("scrapling").disabled = True
slots = asyncio.Semaphore(2)


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=4096)
    renderJs: bool | None = None
    followLinks: bool = True


@app.middleware("http")
async def limit_body(request: Request, call_next):
    if request.method in {"POST", "PUT"}:
        # Read at most 8 KiB, including when no Content-Length is supplied.
        size = 0
        chunks = []
        async for chunk in request.stream():
            size += len(chunk)
            if size > 8192:
                return JSONResponse({"detail": "Request exceeds the 8 KiB limit."}, status_code=413)
            chunks.append(chunk)
        request._body = b"".join(chunks)
    response = await call_next(request)
    response.headers.setdefault("Cache-Control", "no-store")
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.get("/api/health")
async def health():
    cfg=await load_settings()
    return {"status": "ok", "version": "2.0.0", "browserEnabled": cfg.renderJs, "browserAvailable": bool(os.getenv("BROWSER_BASE_URL")) or os.getenv("ENABLE_BROWSER", "false").lower() == "true",
            "mapsService": os.getenv("MAPS_BASE_URL", "http://127.0.0.1:8001")}


@app.get("/api/demo")
async def demo():
    started = time.monotonic()
    html = (Path(__file__).parent.parent / "fixtures" / "demo.html").read_text()
    extraction = extract_html(html, "https://demo.example/")
    extraction.warnings.insert(0, "Demo profile of a fictional business.")
    return result_payload(extraction, started, 1)


@app.post("/api/extract")
async def extract(body: ImportRequest):
    try:
        await asyncio.wait_for(slots.acquire(), timeout=0.05)
    except TimeoutError:
        raise HTTPException(429, "Could not process this source. Check the URL and try again.")
    try:
        async with asyncio.timeout(245):
            return await import_profile(body.url, body.renderJs, body.followLinks)
    except UnsafeURL as exc:
        raise HTTPException(400, str(exc)) from exc
    except (ImportError, ProviderError) as exc:
        raise HTTPException(422, str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(504, "Could not process this source. Check the URL and try again.") from exc
    except Exception as exc:
        logger.exception("Import failed")
        raise HTTPException(502, "Source data could not be verified. Review the result.") from exc
    finally:
        slots.release()


@app.post("/api/extract-stream")
async def extract_stream(body: ImportRequest):
    async def events():
        acquired = False
        producer = None
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=16)

        async def on_progress(snapshot: dict):
            await queue.put({"type": "progress", **snapshot})

        async def produce():
            try:
                async with asyncio.timeout(245):
                    result = await import_profile(body.url, body.renderJs, body.followLinks, on_progress=on_progress)
                await queue.put({"type": "complete", **result})
            except (UnsafeURL, ImportError, ProviderError, ValueError) as exc:
                await queue.put({"type": "error", "detail": str(exc)})
            except TimeoutError:
                await queue.put({"type": "error", "detail": "Could not process this source. Check the URL and try again."})
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Streaming import failed")
                await queue.put({"type": "error", "detail": "Could not process this source. Check the URL and try again."})

        try:
            try:
                await asyncio.wait_for(slots.acquire(), timeout=0.05)
                acquired = True
            except TimeoutError:
                yield json.dumps({"type": "error", "detail": "Could not process this source. Check the URL and try again."}, ensure_ascii=False) + "\n"
                return
            producer = asyncio.create_task(produce())
            while True:
                event = await queue.get()
                yield json.dumps(event, ensure_ascii=False) + "\n"
                if event["type"] in {"complete", "error"}:
                    break
        finally:
            if producer is not None:
                if not producer.done():
                    producer.cancel()
                with suppress(asyncio.CancelledError):
                    await producer
            if acquired:
                slots.release()

    return StreamingResponse(events(), media_type="application/x-ndjson", headers={
        "Cache-Control": "no-store, no-transform",
        "X-Accel-Buffering": "no",
    })

app.include_router(router)
