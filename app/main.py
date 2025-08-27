from fastapi import FastAPI, Response, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse

import mimetypes

from .config import settings
from .database import engine, Base
from .routers import admin, public, likes
from .templating import templates


# Register additional MIME types
mimetypes.add_type("image/avif", ".avif")
mimetypes.add_type("image/webp", ".webp")


class StaticFilesCached(StaticFiles):
    """StaticFiles with cache-control headers for images, CSS, and JS."""

    def file_response(self, *args, **kwargs):
        resp: FileResponse = super().file_response(*args, **kwargs)
        content_type = resp.headers.get("content-type", "")
        if content_type.startswith("image/"):
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif content_type in ("text/css", "application/javascript"):
            resp.headers["Cache-Control"] = "public, max-age=86400"
        return resp


# ===== FastAPI setup =====
app = FastAPI(
    title=settings.SITE_TITLE,
    docs_url=None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# Media + static mounts
app.mount("/media", StaticFilesCached(directory=str(settings.STORAGE_DIR)), name="media")
app.mount("/static/thumbs", StaticFilesCached(directory=str(settings.THUMBS_DIR)), name="thumbs")
app.mount("/static", StaticFilesCached(directory="static"), name="static")

# Create database tables if they don't exist
Base.metadata.create_all(bind=engine)

# Session middleware
if settings.ENV == "prod":
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.SECRET_KEY,
        session_cookie="df_session",
        domain=".dichfoto.com",
        same_site="none",
        https_only=True,
    )
else:
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.SECRET_KEY,
        session_cookie="df_session",
        domain=None,
        same_site="lax",
        https_only=False,
    )

# Routers
app.include_router(admin.router)
app.include_router(public.router)
app.include_router(likes.router)


# ====== Homepage ======
@app.get("/", response_class=HTMLResponse)
def home():
    """Welcome page with a link to the Admin dashboard."""
    return """
    <!doctype html><html lang="en"><head>
    <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Dich Foto</title>
    <style>
      body{margin:0;font-family:system-ui;background:#0b0b0c;color:#eee;
      display:grid;place-items:center;height:100vh}
      .card{padding:24px 28px;border:1px solid #2a2a2e;border-radius:12px;
      background:#121214;max-width:720px;text-align:center}
      h1{margin:0 0 8px;font-size:28px}
      p{margin:0 0 16px;color:#bbb}
      a{display:inline-block;padding:10px 16px;background:#2563eb;color:#fff;
        border-radius:8px;text-decoration:none}
      a:hover{background:#1d4ed8}
    </style>
    </head><body><div class="card">
      <h1>Dich Foto</h1>
      <p>The service is running.</p>
      <a href="/admin">Go to Admin</a>
    </div></body></html>
    """


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots():
    """robots.txt file."""
    return "User-agent: *\nDisallow: /admin\nDisallow: /docs\nDisallow: /redoc\n"


# --- Health checks ---
@app.api_route("/healthz", methods=["GET", "HEAD"], include_in_schema=False)
def health(request: Request):
    headers = {"Cache-Control": "no-store"}
    if request.method == "HEAD":
        return Response(status_code=200, headers=headers)
    return JSONResponse({"ok": True}, headers=headers)


print("[DB URL]", settings.DATABASE_URL)
print("[ENV]", settings.ENV)
