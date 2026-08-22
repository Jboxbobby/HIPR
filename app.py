import random
import sys
import logging
import ipaddress
import json
import socket
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import anyio
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

logger = logging.getLogger("uvicorn.error")

if sys.platform != "win32":
    try:
        import uvloop
        uvloop.install()
    except ImportError:
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create shared resources at startup and always close them at shutdown."""
    app.state.client = httpx.AsyncClient(
        timeout=CUSTOM_TIMEOUT,
        limits=LIMITS,
        http2=True,
        follow_redirects=False,
        trust_env=False,
    )
    try:
        yield
    finally:
        await app.state.client.aclose()


app = FastAPI(title="Timeout-Resistant Service", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
    allow_methods=["POST"],
    allow_headers=["Content-Type"],
)

ALLOWED_HOSTS = frozenset({
    "jsonplaceholder.typicode.com",
    "httpbin.org",
    "192.168.1.1",
    "192.168.1.50",
})
ALLOWED_PORTS = frozenset({80, 443, 8080, 3000, 5000})
MAX_RESPONSE_BYTES = 1_000_000  # 1 MiB
PROJECT_DIR = Path(__file__).resolve().parent

CUSTOM_TIMEOUT = httpx.Timeout(
    connect=3.0,   
    read=15.0,     
    write=5.0,     
    pool=5.0       
)

LIMITS = httpx.Limits(
    max_keepalive_connections=20, 
    max_connections=100,          
    keepalive_expiry=30.0         
)


def resolve_host(host: str, port: int) -> set[str]:
    """Resolve for a defense-in-depth private-address check."""
    try:
        results = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise HTTPException(status_code=400, detail="Target hostname could not be resolved.") from exc
    return {result[4][0] for result in results}


async def validate_target_url(url: str) -> str:
    """Apply allow-list, URL, and network-boundary checks before any request."""
    if len(url) > 2_048 or any(char.isspace() for char in url):
        raise HTTPException(status_code=400, detail="Target URL is invalid.")

    try:
        parsed = urlsplit(url)
        port = parsed.port or (80 if parsed.scheme == "http" else 443)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Target URL has an invalid port.") from exc

    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(status_code=400, detail="Only complete HTTP or HTTPS URLs are allowed.")
    if parsed.username or parsed.password:
        raise HTTPException(status_code=400, detail="URLs with credentials are not allowed.")
    if parsed.hostname not in ALLOWED_HOSTS:
        raise HTTPException(status_code=403, detail="This hostname is not approved for outbound requests.")
    if port not in ALLOWED_PORTS:
        raise HTTPException(status_code=403, detail="This port is not approved for outbound requests.")

    
    addresses = await anyio.to_thread.run_sync(resolve_host, parsed.hostname, port)
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            logger.warning("Blocked protected address %s for host %s", address, parsed.hostname)
            raise HTTPException(status_code=403, detail="Target resolves to a protected network address.")

       
        if ip.is_private and (parsed.hostname != address or parsed.hostname not in ALLOWED_HOSTS):
            logger.warning("Blocked unapproved private address %s for host %s", address, parsed.hostname)
            raise HTTPException(status_code=403, detail="Target resolves to an unapproved private network address.")

    return url


async def fetch_with_retry(client: httpx.AsyncClient, url: str, max_retries: int = 3):
    """
    Executes an HTTP GET with Exponential Backoff + Full Jitter
    to prevent synchronization storms on failing connections.
    """
    attempt = 0
    base_delay = 0.5  

    while attempt < max_retries:
        try:
            logger.info("Fetching %s (attempt %d of %d)", url, attempt + 1, max_retries)
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                content_length = response.headers.get("content-length")
                if content_length:
                    try:
                        declared_size = int(content_length)
                    except ValueError as exc:
                        raise HTTPException(status_code=502, detail="Upstream sent an invalid Content-Length header.") from exc
                    if declared_size > MAX_RESPONSE_BYTES:
                        raise HTTPException(status_code=502, detail="Upstream response is too large.")

                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise HTTPException(status_code=502, detail="Upstream response is too large.")

            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                data = {"raw_text": body.decode("utf-8", errors="replace")}

            logger.info("200 OK: fetched %s", url)
            return data
        except httpx.TransportError as exc:
            attempt += 1
            if attempt >= max_retries:
                logger.error("504 Gateway Timeout: %s failed after %d attempts: %s", url, max_retries, exc)
                raise HTTPException(
                    status_code=504,
                    detail=f"Request failed after {max_retries} attempts: {str(exc)}"
                )
            
            backoff = min(4.0, base_delay * (2 ** attempt))
            jittered_delay = random.uniform(0.1, backoff)
            logger.warning(
                "Attempt %d failed for %s: %s. Retrying in %.2f seconds.",
                attempt,
                url,
                exc,
                jittered_delay,
            )
            await anyio.sleep(jittered_delay)
            
        except httpx.HTTPStatusError as exc:
            logger.error("Upstream returned %d for %s", exc.response.status_code, url)
            raise HTTPException(status_code=exc.response.status_code, detail=str(exc))


class RequestPayload(BaseModel):
    target_url: str


@app.get("/", include_in_schema=False)
async def homepage():
    """Serve the local frontend from the same origin as the API."""
    return FileResponse(PROJECT_DIR / "index.html")


@app.post("/api/fetch")
async def proxy_fetch(payload: RequestPayload):
    target_url = await validate_target_url(payload.target_url)
    data = await fetch_with_retry(app.state.client, target_url)
    return {"status": "success", "data": data}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
