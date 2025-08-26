# app/templating.py
from fastapi.templating import Jinja2Templates
from .config import settings

def build_embed_url(provider: str, vid: str) -> str:
    p = (provider or "").lower()
    if p == "vimeo":
        return f"https://player.vimeo.com/video/{vid}?dnt=1&title=0&byline=0&badge=0"
    if p == "youtube":
        return f"https://www.youtube.com/embed/{vid}?rel=0"
    if p == "cloudflare":
        return f"https://iframe.videodelivery.net/{vid}"
    return vid

templates = Jinja2Templates(directory="templates")
templates.env.globals["settings"] = settings
templates.env.globals["build_embed_url"] = build_embed_url
