# app/templating.py
from fastapi.templating import Jinja2Templates
from .config import settings

def build_embed_url(provider: str, vid: str, vimeo_hash: str | None = None) -> str:
    p = (provider or "").lower()
    if p in ("vimeo",):
        base = f"https://player.vimeo.com/video/{vid}"
        params = []
        if vimeo_hash:
            params.append(f"h={vimeo_hash}")
        # نفس الإعدادات التي كنت تضيفها
        params += ["dnt=1", "title=0", "byline=0", "badge=0"]
        return base + "?" + "&".join(params)
    if p in ("youtube", "yt"):
        return f"https://www.youtube.com/embed/{vid}?rel=0"
    if p in ("cloudflare", "cf", "cloudflare_stream", "stream"):
        return f"https://iframe.videodelivery.net/{vid}"
    return str(vid)

templates = Jinja2Templates(directory="templates")
templates.env.globals["settings"] = settings
templates.env.globals["build_embed_url"] = build_embed_url
