import os
import re
from typing import List, Optional, Literal

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl

app = FastAPI(title="Social Media Downloader API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -------------------- Models --------------------
class AnalyzeRequest(BaseModel):
    url: HttpUrl


class DownloadOption(BaseModel):
    type: Literal["mp4", "mp3", "image"]
    quality: Optional[str] = None
    url: HttpUrl
    note: Optional[str] = None


class FetchResponse(BaseModel):
    platform: Literal["youtube", "instagram"]
    title: Optional[str] = None
    thumbnail: Optional[str] = None
    downloads: List[DownloadOption] = []
    info: Optional[str] = None


# -------------------- Helpers --------------------
YOUTUBE_REGEX = re.compile(
    r"^(?:https?:\/\/)?(?:www\.|m\.)?(?:youtube\.com\/(?:watch\?v=|embed\/|shorts\/)|youtu\.be\/)([\w-]{11})"
)
INSTAGRAM_REGEX = re.compile(r"^(?:https?:\/\/)?(?:www\.)?instagram\.com\/")


def detect_platform(url: str) -> Optional[str]:
    if YOUTUBE_REGEX.search(url):
        return "youtube"
    if INSTAGRAM_REGEX.search(url):
        return "instagram"
    return None


def extract_youtube_id(url: str) -> Optional[str]:
    m = YOUTUBE_REGEX.search(url)
    return m.group(1) if m else None


# -------------------- Routes --------------------
@app.get("/")
def read_root():
    return {"message": "Social Media Downloader Backend is running"}


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest):
    platform = detect_platform(str(req.url))
    return {"platform": platform, "valid": platform is not None}


@app.post("/api/fetch", response_model=FetchResponse)
def fetch(req: AnalyzeRequest):
    url = str(req.url)
    platform = detect_platform(url)
    if platform is None:
        raise HTTPException(status_code=400, detail="Unsupported or invalid URL. Only YouTube and Instagram are supported.")

    rapidapi_key = os.getenv("RAPIDAPI_KEY") or os.getenv("RAPID_API_KEY")

    if platform == "youtube":
        vid = extract_youtube_id(url)
        if not vid:
            raise HTTPException(status_code=400, detail="Could not parse YouTube video ID.")

        title = None
        thumbnail = None
        # Try to get metadata using oEmbed (no API key required)
        try:
            oembed = requests.get(
                "https://www.youtube.com/oembed",
                params={"url": url, "format": "json"},
                timeout=10,
            )
            if oembed.ok:
                data = oembed.json()
                title = data.get("title")
                thumbnail = data.get("thumbnail_url")
        except Exception:
            pass

        downloads: List[DownloadOption] = []
        info = None

        # If RapidAPI key is present, try to fetch downloadable links
        if rapidapi_key:
            headers = {
                "X-RapidAPI-Key": rapidapi_key,
                "X-RapidAPI-Host": "ytstream-download-youtube-videos.p.rapidapi.com",
            }
            try:
                r = requests.get(
                    "https://ytstream-download-youtube-videos.p.rapidapi.com/dl",
                    params={"id": vid},
                    headers=headers,
                    timeout=20,
                )
                if r.ok:
                    j = r.json()
                    # Common shapes: {title, thumbnail, formats: [{quality, url, type}]}
                    title = title or j.get("title")
                    thumbnail = thumbnail or j.get("thumbnail")
                    fmts = j.get("formats") or j.get("formats_list") or []
                    for f in fmts:
                        f_type = f.get("type") or f.get("mimeType", "").split("/")[0]
                        if "mp4" in str(f.get("url", "")) or "video" in str(f.get("mimeType", "")):
                            downloads.append(
                                DownloadOption(type="mp4", quality=f.get("quality") or f.get("qualityLabel"), url=f.get("url"))
                            )
                else:
                    info = f"RapidAPI YouTube fetch failed: {r.status_code}"
            except Exception as e:
                info = f"RapidAPI YouTube error: {str(e)[:120]}"

            # Try MP3 via youtube-mp36
            try:
                headers2 = {
                    "X-RapidAPI-Key": rapidapi_key,
                    "X-RapidAPI-Host": "youtube-mp36.p.rapidapi.com",
                }
                r2 = requests.get(
                    "https://youtube-mp36.p.rapidapi.com/dl", params={"id": vid}, headers=headers2, timeout=20
                )
                if r2.ok:
                    j2 = r2.json()
                    link = j2.get("link") or j2.get("url")
                    if link:
                        downloads.append(DownloadOption(type="mp3", quality="128kbps", url=link))
                else:
                    info = (info + "; " if info else "") + f"MP3 fetch failed: {r2.status_code}"
            except Exception as e:
                info = (info + "; " if info else "") + f"MP3 error: {str(e)[:120]}"
        else:
            info = "RAPIDAPI_KEY not set. Showing metadata only."

        return FetchResponse(platform="youtube", title=title, thumbnail=thumbnail, downloads=downloads, info=info)

    # Instagram handling
    title = None
    thumbnail = None
    downloads: List[DownloadOption] = []
    info = None

    if rapidapi_key:
        try:
            headers = {
                "X-RapidAPI-Key": rapidapi_key,
                "X-RapidAPI-Host": "instagram-downloader-download-instagram-videos-stories.p.rapidapi.com",
            }
            r = requests.get(
                "https://instagram-downloader-download-instagram-videos-stories.p.rapidapi.com/index",
                params={"url": url},
                headers=headers,
                timeout=20,
            )
            if r.ok:
                j = r.json()
                # Response can contain media array or single link
                # Try common fields
                title = j.get("title") or "Instagram Media"
                thumb = j.get("thumbnail") or j.get("display_url") or j.get("thumb")
                if isinstance(thumb, str):
                    thumbnail = thumb
                media_list = j.get("media") or j.get("result") or j.get("links") or []
                if isinstance(media_list, dict):
                    media_list = [media_list]
                for m in media_list:
                    link = m.get("url") or m.get("link") or m.get("video") or m.get("image")
                    if not link:
                        continue
                    mtype = "mp4" if ("video" in str(m.get("type", "")).lower() or str(m.get("is_video", False)).lower() == "true") else "image"
                    qual = m.get("quality") or m.get("resolution")
                    downloads.append(DownloadOption(type=mtype, quality=qual, url=link))
            else:
                info = f"RapidAPI Instagram fetch failed: {r.status_code}"
        except Exception as e:
            info = f"RapidAPI Instagram error: {str(e)[:120]}"
    else:
        info = "RAPIDAPI_KEY not set. Unable to generate Instagram download links."

    return FetchResponse(platform="instagram", title=title, thumbnail=thumbnail, downloads=downloads, info=info)


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": [],
    }

    try:
        from database import db  # type: ignore

        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = db.name if hasattr(db, "name") else "✅ Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"
    except ImportError:
        response["database"] = "❌ Database module not found (run enable-database first)"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    import os as _os

    response["database_url"] = "✅ Set" if _os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if _os.getenv("DATABASE_NAME") else "❌ Not Set"

    return response


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
