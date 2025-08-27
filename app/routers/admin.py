from fastapi import (
    APIRouter, Depends, Request, UploadFile, File, Form,
    HTTPException, Response
)
from fastapi.responses import (
    HTMLResponse, RedirectResponse, StreamingResponse, FileResponse
)
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime
from slugify import slugify
from pathlib import Path
import json, shutil
from pydantic import BaseModel

from ..templating import templates
from ..database import SessionLocal
from .. import models
from ..config import settings
from ..utils import gen_slug, hash_password, safe_filename
from ..services import thumbs, gdrive
from ..services.variants import make_variants
from PIL import Image, ImageOps
from app.utils import _parse_dt

# helpers لاستخراج الـID من روابط يوتيوب/فيميو/كلودفلير
import re
from urllib.parse import urlparse, parse_qs

def _extract_video_id(provider: str, raw: str) -> str:
    raw = (raw or "").strip()
    p = (provider or "").lower()

    if p == "youtube":
        # https://youtu.be/ID  |  https://www.youtube.com/watch?v=ID
        if "youtu.be/" in raw:
            return raw.rsplit("/", 1)[-1].split("?")[0]
        if "watch" in raw:
            return parse_qs(urlparse(raw).query).get("v", [""])[0]
        return raw  # قد يكون ID مباشرة

    if p == "vimeo":
        # https://vimeo.com/123456789
        m = re.search(r"vimeo\.com/(\d+)", raw)
        return m.group(1) if m else raw

    if p == "cloudflare":
        # https://iframe.videodelivery.net/PLAYBACK_ID
        m = re.search(r"videodelivery\.net/([A-Za-z0-9_-]+)", raw)
        return m.group(1) if m else raw

    return raw


router = APIRouter(prefix="/admin", tags=["admin"])

# ===========================
# Theme config (simple JSON)
# ===========================
THEME_PATH = Path("static/theme.json")

class ThemePayload(BaseModel):
    vars: dict[str, str] = {}
    disableDark: bool = False


def _variant_paths(album_id: int, stem: str) -> list[Path]:
    base = Path(settings.STORAGE_DIR)
    rels = [
        f"albums/{album_id}/thumb/400/{stem}.jpg",
        f"albums/{album_id}/thumb/400/{stem}.webp",
        f"albums/{album_id}/disp/1600/{stem}.jpg",
        f"albums/{album_id}/disp/1600/{stem}.webp",
        f"albums/{album_id}/big/2048/{stem}.jpg",
        f"albums/{album_id}/big/2048/{stem}.webp",
    ]
    return [base / r for r in rels]


# ================
# Helpers
# ================
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def is_admin(request: Request) -> bool:
    return bool(request.session.get("admin"))

def require_admin(request: Request):
    if not is_admin(request):
        raise HTTPException(status_code=403, detail="Not authorized")


# ================
# Routes
# ================
@router.get("", include_in_schema=False)
def admin_no_slash():
    return RedirectResponse(url="/admin/", status_code=301)

@router.get("/", response_class=HTMLResponse)
def admin_home(request: Request):
    if not is_admin(request):
        return templates.TemplateResponse(
            "admin_login.html",
            {"request": request, "site_title": settings.SITE_TITLE},
        )
    return RedirectResponse(url="/admin/albums", status_code=302)

# ---- Theme pages ----
@router.get("/theme", response_class=HTMLResponse)
@router.get("/theme/", response_class=HTMLResponse, include_in_schema=False)
def theme_page(request: Request):
    require_admin(request)
    return templates.TemplateResponse("admin/theme.html", {"request": request})

@router.post("/theme/save")
def theme_save(payload: ThemePayload, request: Request):
    require_admin(request)
    THEME_PATH.write_text(
        json.dumps(payload.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {"ok": True}

@router.post("/theme/reset")
def theme_reset(request: Request):
    require_admin(request)
    if THEME_PATH.exists():
        THEME_PATH.unlink()
    return {"ok": True}

@router.get("/theme/config")
def theme_config():
    if THEME_PATH.exists():
        data = json.loads(THEME_PATH.read_text(encoding="utf-8"))
    else:
        data = {"vars": {}, "disableDark": False}
    return data

# ---- Albums: create/view/list ----
@router.get("/albums/new", response_class=HTMLResponse)
def album_new_form(request: Request):
    require_admin(request)
    return templates.TemplateResponse(
        "admin_album_new.html",
        {"request": request, "site_title": settings.SITE_TITLE},
    )

@router.post("/albums/new")
def create_album(
    request: Request,
    title: str = Form(...),
    photographer: Optional[str] = Form(None),
    photographer_url: Optional[str] = Form(None),
    event_date: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    require_admin(request)
    album = models.Album(
        title=title.strip(),
        photographer=photographer or None,
        photographer_url=photographer_url or None,
        event_date=_parse_dt(event_date),
    )
    db.add(album)
    db.commit()
    db.refresh(album)
    dest = request.url_for("view_album", album_id=album.id)
    return RedirectResponse(url=dest, status_code=302)

@router.get("/albums/{album_id}", response_class=HTMLResponse)
def view_album(request: Request, album_id: int, db: Session = Depends(get_db)):
    require_admin(request)
    album = db.get(models.Album, album_id)
    if not album:
        raise HTTPException(404)
    assets = sorted(album.assets, key=lambda a: ((a.sort_order or 0), a.id))
    videos = getattr(album, "videos", [])
    return templates.TemplateResponse(
        "admin_album_view.html",
        {
            "request": request,
            "site_title": settings.SITE_TITLE,
            "album": album,
            "assets": assets,
            "videos": videos,
        },
    )

@router.get("/albums", response_class=HTMLResponse)
@router.get("/albums/", response_class=HTMLResponse, include_in_schema=False)
def list_albums(request: Request, db: Session = Depends(get_db)):
    require_admin(request)
    albums = db.query(models.Album).order_by(models.Album.created_at.desc()).all()
    return templates.TemplateResponse(
        "admin_album_list.html",
        {"request": request, "albums": albums, "site_title": settings.SITE_TITLE},
    )

# ---- Upload assets ----
@router.post("/albums/{album_id}/upload")
async def upload_files(
    request: Request,
    album_id: int,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    require_admin(request)

    album = db.get(models.Album, album_id)
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")

    STORAGE_ROOT = Path(settings.STORAGE_DIR)
    album_root = STORAGE_ROOT / "albums" / str(album.id)
    orig_dir = album_root / "original"
    orig_dir.mkdir(parents=True, exist_ok=True)

    # (اختياري) Google Drive
    service = None
    d_album = d_orig = d_thumb400 = d_disp1600 = d_big2048 = None
    if getattr(settings, "USE_GDRIVE", False):
        try:
            service = gdrive._service()
            root_id = settings.GDRIVE_ROOT_FOLDER_ID
            if root_id:
                d_albums   = gdrive.ensure_subfolder(service, root_id, "albums")
                d_album    = gdrive.ensure_subfolder(service, d_albums, str(album.id))
                d_orig     = gdrive.ensure_subfolder(service, d_album, "original")
                d_thumb    = gdrive.ensure_subfolder(service, d_album, "thumb")
                d_thumb400 = gdrive.ensure_subfolder(service, d_thumb, "400")
                d_disp     = gdrive.ensure_subfolder(service, d_album, "disp")
                d_disp1600 = gdrive.ensure_subfolder(service, d_disp, "1600")
                d_big      = gdrive.ensure_subfolder(service, d_album, "big")
                d_big2048  = gdrive.ensure_subfolder(service, d_big, "2048")
        except Exception as e:
            print("[gdrive] init failed:", e)
            service = None

    saved_assets = []
    max_order = max([a.sort_order or 0 for a in album.assets], default=0)

    for file in files:
        if not (file.content_type or "").startswith("image/"):
            raise HTTPException(status_code=400, detail="Only image files are allowed")

        filename = safe_filename(file.filename)
        original_path = orig_dir / filename

        if original_path.exists():
            ts = int(datetime.now().timestamp())
            original_path = original_path.with_name(f"{original_path.stem}-{ts}{original_path.suffix}")
            filename = original_path.name

        with open(original_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
        await file.close()

        stem = Path(filename).stem
        variants = make_variants(
            original_path=original_path,
            out_root=STORAGE_ROOT,
            album_id=album.id,
            filename_stem=stem,
        )

        try:
            lqip = thumbs.tiny_placeholder_base64(original_path)
        except Exception:
            lqip = None

        gfile_id = gthumb_id = None
        if service and d_album:
            try:
                with open(original_path, "rb") as fp:
                    gfile_id = gdrive.upload_bytes(
                        service, d_orig, filename,
                        file.content_type or "application/octet-stream",
                        fp.read(),
                    )

                def _up(rel: str, folder_id: str):
                    p = STORAGE_ROOT / rel
                    if p.exists():
                        mime = "image/webp" if p.suffix.lower() == ".webp" else "image/jpeg"
                        with open(p, "rb") as fp:
                            return gdrive.upload_bytes(service, folder_id, p.name, mime, fp.read())
                    return None

                tid_jpg  = _up(variants["thumb_jpg"], d_thumb400)
                _up(variants["thumb_webp"], d_thumb400)
                if tid_jpg:
                    gthumb_id = tid_jpg
                _up(variants["disp_jpg"], d_disp1600)
                _up(variants["disp_webp"], d_disp1600)
                _up(variants["big_jpg"], d_big2048)
                _up(variants["big_webp"], d_big2048)
            except Exception as e:
                print("[gdrive] upload failed:", e)

        filename_rel = (Path("albums") / str(album.id) / "original" / filename).as_posix()
        asset = models.Asset(
            album_id=album.id,
            filename=filename_rel,
            original_name=file.filename,
            mime_type=file.content_type,
            size=original_path.stat().st_size,
            gdrive_file_id=gfile_id,
            gdrive_thumb_id=gthumb_id,
        )
        asset.sort_order = max_order + 10
        max_order += 10
        try:
            asset.set_variants(variants)
        except Exception:
            pass
        asset.lqip = lqip

        db.add(asset)
        saved_assets.append(asset)

    db.commit()

    accept = (request.headers.get("accept") or "").lower()
    if "text/html" in accept:
        return RedirectResponse(url=f"/admin/albums/{album_id}", status_code=303)
    return {"ok": True, "uploaded": [a.id for a in saved_assets]}

# ---- Thumbs ----
@router.get("/thumb/{asset_id}")
def admin_thumb(asset_id: int, db: Session = Depends(get_db)):
    asset = db.get(models.Asset, asset_id)
    if not asset:
        raise HTTPException(404)

    if getattr(settings, "USE_GDRIVE", False) and getattr(asset, "gdrive_thumb_id", None):
        try:
            gen = gdrive.stream_via_requests(asset.gdrive_thumb_id, chunk_size=256 * 1024)
            return StreamingResponse(gen, media_type="image/jpeg")
        except Exception:
            pass

    stem = Path(str(asset.filename).replace("\\", "/")).stem
    thumb_dir = Path(settings.STORAGE_DIR) / "albums" / str(asset.album_id) / "thumb" / "400"
    jpg = thumb_dir / f"{stem}.jpg"
    webp = thumb_dir / f"{stem}.webp"

    if jpg.exists():
        return FileResponse(jpg, media_type="image/jpeg")
    if webp.exists():
        return FileResponse(webp, media_type="image/webp")

    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="400" height="260">'
           '<rect width="100%" height="100%" fill="#e2e8f0"/>'
           '<text x="50%" y="50%" dominant-baseline="middle" text-anchor="middle" '
           'font-family="Segoe UI, Roboto, sans-serif" font-size="16" fill="#64748b">No preview</text>'
           '</svg>')
    return Response(content=svg, media_type="image/svg+xml")

# ---- Auth ----
@router.get("/login", response_class=HTMLResponse)
def admin_login_form(request: Request):
    if is_admin(request):
        return RedirectResponse(url="/admin/albums/new", status_code=302)
    return templates.TemplateResponse(
        "admin_login.html",
        {"request": request, "site_title": settings.SITE_TITLE},
    )

@router.post("/login")
def admin_login(request: Request, password: str = Form(...)):
    if password == settings.ADMIN_PASSWORD:
        request.session["admin"] = True
        return RedirectResponse(url="/admin/albums/new", status_code=302)
    return RedirectResponse(url="/admin", status_code=302)

# ---- Share Links ----
@router.api_route("/albums/{album_id}/share", methods=["POST"])
def create_share(
    request: Request,
    album_id: int,
    expires_at: Optional[str] = Form(None),
    password: Optional[str] = Form(None),
    allow_zip: Optional[bool] = Form(True),
    db: Session = Depends(get_db),
):
    require_admin(request)
    album = db.get(models.Album, album_id)
    if not album:
        raise HTTPException(404, "Album not found")

    exp = None
    if expires_at:
        try:
            exp = datetime.fromisoformat(expires_at)
        except Exception:
            exp = None

    slug = slugify(album.title)[:20] + "-" + gen_slug(4)
    pwd_hash = hash_password(password) if password else None

    sl = models.ShareLink(
        album_id=album.id,
        slug=slug,
        expires_at=exp,
        password_hash=pwd_hash,
        allow_zip=bool(allow_zip),
    )
    db.add(sl)
    db.commit()
    db.refresh(sl)
    return RedirectResponse(url=f"/s/{sl.slug}", status_code=302)

@router.get("/albums/{album_id}/share", include_in_schema=False)
def create_share_get(album_id: int):
    return RedirectResponse(url=f"/admin/albums/{album_id}", status_code=302)

# ---- Asset ops ----
@router.post("/assets/{asset_id}/move")
def move_asset(
    request: Request,
    asset_id: int,
    direction: str = Form(...),  # up/down/top/bottom
    db: Session = Depends(get_db),
):
    require_admin(request)
    asset = db.get(models.Asset, asset_id)
    if not asset:
        raise HTTPException(404)
    album = db.get(models.Album, asset.album_id)

    assets = sorted(album.assets, key=lambda a: ((a.sort_order or 0), a.id))
    idx = next((i for i, x in enumerate(assets) if x.id == asset.id), None)
    if idx is None:
        raise HTTPException(404)

    if direction == "up":
        new_idx = max(0, idx - 1)
    elif direction == "down":
        new_idx = min(len(assets) - 1, idx + 1)
    elif direction == "top":
        new_idx = 0
    elif direction == "bottom":
        new_idx = len(assets) - 1
    else:
        raise HTTPException(400, "Invalid direction")

    if new_idx != idx:
        item = assets.pop(idx)
        assets.insert(new_idx, item)
        for i, it in enumerate(assets):
            it.sort_order = i * 10
        db.commit()

    return RedirectResponse(url=f"/admin/albums/{album.id}", status_code=303)

@router.post("/assets/{asset_id}/rotate")
def rotate_asset(
    request: Request,
    asset_id: int,
    dir: str = Form(...),  # 'cw' أو 'ccw'
    db: Session = Depends(get_db),
):
    require_admin(request)
    asset = db.get(models.Asset, asset_id)
    if not asset:
        raise HTTPException(404)

    base = Path(settings.STORAGE_DIR)
    f_rel = Path(str(asset.filename).replace("\\", "/"))
    orig = base / f_rel
    if not orig.exists():
        raise HTTPException(404, "Original file not found")

    stem = f_rel.stem
    for p in _variant_paths(asset.album_id, stem):
        try:
            if p.exists():
                p.unlink()
        except Exception:
            pass

    with Image.open(orig) as im:
        im = ImageOps.exif_transpose(im)
        angle = -90 if dir == "cw" else 90
        im = im.rotate(angle, expand=True)
        ext = orig.suffix.lower()
        if ext in [".jpg", ".jpeg", ".png", ".webp"]:
            im.save(orig)
        else:
            im.convert("RGB").save(orig.with_suffix(".jpg"), format="JPEG", quality=90, optimize=True)
            try:
                orig.unlink()
            except Exception:
                pass
            new_rel = (Path("albums") / str(asset.album_id) / "original" / f"{stem}.jpg").as_posix()
            asset.filename = new_rel
            orig = base / new_rel

    variants = make_variants(
        original_path=orig,
        out_root=base,
        album_id=asset.album_id,
        filename_stem=Path(asset.filename).stem,
    )
    try:
        asset.set_variants(variants)
    except Exception:
        pass
    try:
        asset.lqip = thumbs.tiny_placeholder_base64(orig)
    except Exception:
        asset.lqip = None

    db.commit()
    return RedirectResponse(url=f"/admin/albums/{asset.album_id}", status_code=303)

@router.post("/assets/{asset_id}/delete")
def delete_asset(request: Request, asset_id: int, db: Session = Depends(get_db)):
    require_admin(request)
    asset = db.get(models.Asset, asset_id)
    if not asset:
        raise HTTPException(404)
    album = db.get(models.Album, asset.album_id)

    base = Path(settings.STORAGE_DIR)
    f_rel = Path(str(asset.filename).replace("\\", "/"))
    orig = base / f_rel
    stem = f_rel.stem

    for p in _variant_paths(album.id, stem):
        try:
            if p.exists():
                p.unlink()
        except Exception:
            pass

    try:
        if orig.exists():
            orig.unlink()
    except Exception:
        pass

    if getattr(album, "cover_asset_id", None) == asset.id:
        album.cover_asset_id = None

    if getattr(settings, "USE_GDRIVE", False):
        try:
            if getattr(asset, "gdrive_file_id", None):
                gdrive.delete_file(asset.gdrive_file_id)
            if getattr(asset, "gdrive_thumb_id", None):
                gdrive.delete_file(asset.gdrive_thumb_id)
        except Exception as e:
            print("[gdrive] delete failed:", e)

    db.delete(asset)
    db.commit()
    return RedirectResponse(url=f"/admin/albums/{album.id}", status_code=303)

@router.post("/albums/{album_id}/cover/{asset_id}")
def set_cover(request: Request, album_id: int, asset_id: int, db: Session = Depends(get_db)):
    require_admin(request)
    album = db.get(models.Album, album_id)
    asset = db.get(models.Asset, asset_id)
    if not album or not asset or asset.album_id != album.id:
        raise HTTPException(404)
    album.cover_asset_id = asset.id
    db.commit()
    return RedirectResponse(url=f"/admin/albums/{album_id}", status_code=303)

@router.post("/albums/{album_id}/cover/clear")
def clear_cover(request: Request, album_id: int, db: Session = Depends(get_db)):
    require_admin(request)
    album = db.get(models.Album, album_id)
    if not album:
        raise HTTPException(404)
    album.cover_asset_id = None
    db.commit()
    return RedirectResponse(url=f"/admin/albums/{album_id}", status_code=303)

# ---- Videos ----
@router.post("/albums/{album_id}/videos/add")
def add_video(request: Request, album_id: int,
              provider: str = Form(...),
              video_id: str = Form(...),      # يقبل ID أو رابط كامل
              title: str | None = Form(None),
              db: Session = Depends(get_db)):
    require_admin(request)

    album = db.get(models.Album, album_id)
    if not album:
        raise HTTPException(404, "Album not found")

    provider = (provider or "").strip().lower()
    allowed = {"youtube", "vimeo", "cloudflare"}
    if provider not in allowed:
        raise HTTPException(400, f"Invalid provider. Must be one of: {', '.join(sorted(allowed))}")

    video_id = _extract_video_id(provider, video_id).strip()
    if not video_id:
        raise HTTPException(400, "Invalid video id")

    exists = (
        db.query(models.Video)
          .filter(models.Video.album_id == album_id,
                  models.Video.provider == provider,
                  models.Video.video_id == video_id)
          .first()
    )
    if exists:
        return RedirectResponse(url=f"/admin/albums/{album_id}", status_code=303)

    v = models.Video(
        album_id=album_id,
        provider=provider,
        video_id=video_id,
        title=(title or "").strip() or None,
    )
    db.add(v)
    db.commit()

    return RedirectResponse(url=f"/admin/albums/{album_id}", status_code=303)

@router.post("/videos/{video_id}/delete")
def delete_video(request: Request, video_id: int, db: Session = Depends(get_db)):
    require_admin(request)
    v = db.get(models.Video, video_id)
    if not v:
        raise HTTPException(404)
    album_id = v.album_id
    db.delete(v)
    db.commit()
    return RedirectResponse(url=f"/admin/albums/{album_id}", status_code=303)

# ---- HEAD helpers (لمنع أخطاء HEAD) ----
@router.head("/albums", include_in_schema=False)
@router.head("/albums/", include_in_schema=False)
def albums_head():
    return Response(status_code=200, headers={"Cache-Control": "no-store"})

@router.head("/theme", include_in_schema=False)
@router.head("/theme/", include_in_schema=False)
def theme_head():
    return Response(status_code=200, headers={"Cache-Control": "no-store"})

@router.head("/", include_in_schema=False)
def admin_root_head():
    return Response(status_code=200, headers={"Cache-Control": "no-store"})

@router.head("/albums/{album_id}", include_in_schema=False)
def view_album_head(album_id: int):
    return Response(status_code=200, headers={"Cache-Control": "no-store"})

# --- Edit album ---
@router.get("/albums/{album_id}/edit", response_class=HTMLResponse)
def edit_album_form(request: Request, album_id: int, db: Session = Depends(get_db)):
    require_admin(request)
    album = db.get(models.Album, album_id)
    if not album:
        raise HTTPException(404)
    return templates.TemplateResponse(
        "admin/edit_album.html",
        {
            "request": request,
            "site_title": settings.SITE_TITLE,
            "album": album,
        },
    )


@router.post("/albums/{album_id}/edit")
def edit_album_save(
    request: Request,
    album_id: int,
    title: str = Form(...),
    photographer: str | None = Form(None),
    photographer_url: str | None = Form(None),
    event_date: str | None = Form(None),
    db: Session = Depends(get_db),
):
    require_admin(request)
    album = db.get(models.Album, album_id)
    if not album:
        raise HTTPException(404)

    album.title = title.strip()
    album.photographer = (photographer or "").strip() or None
    album.photographer_url = (photographer_url or "").strip() or None
    album.event_date = _parse_dt(event_date)

    db.commit()
    return RedirectResponse(url=f"/admin/albums/{album_id}", status_code=303)
