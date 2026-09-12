from __future__ import annotations

import os
import re
import shutil
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import yt_dlp
from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024

MAX_URL_LENGTH = 2048
DOWNLOAD_DIR = Path(__file__).resolve().parent / "downloads"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
SITE_URL = os.environ.get("SITE_URL", "https://clipfetch.in").rstrip("/")
BUSINESS_NAME = "ClipFetch"
LEGAL_EMAIL = "01.abhiiii@gmail.com"
JURISDICTION = "India"
ALLOWED_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
REQUESTS: dict[str, deque[float]] = defaultdict(deque)
RATE_WINDOW = 60
RATE_LIMIT = 12
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{6,20}$")

PAGE_CONFIG = {
    "video": {"path": "/", "label": "YouTube Video Downloader", "description": "Save public YouTube videos in crisp MP4 quality.", "placeholder": "Paste a YouTube video link", "icon": "▶"},
    "shorts": {"path": "/shorts", "label": "YouTube Shorts Downloader", "description": "Download public YouTube Shorts for offline viewing.", "placeholder": "Paste a YouTube Shorts link", "icon": "✦"},
}


def _json_error(message: str, status: int = 400):
    return jsonify({"error": message}), status


def _payload() -> dict[str, Any]:
    value = request.get_json(silent=True)
    return value if isinstance(value, dict) else {}


def _video_id(url: str) -> str | None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if host == "youtu.be":
        candidate = parsed.path.strip("/").split("/")[0]
    else:
        candidate = parse_qs(parsed.query).get("v", [""])[0]
        if not candidate and parsed.path.lower().startswith("/shorts/"):
            candidate = parsed.path.split("/")[2] if len(parsed.path.split("/")) > 2 else ""
    return candidate if VIDEO_ID.fullmatch(candidate) else None


def _validate_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    url = value.strip()
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().rstrip(".")
    except ValueError:
        return None
    if (not url or len(url) > MAX_URL_LENGTH or parsed.scheme not in {"http", "https"}
            or parsed.username or parsed.password or host not in ALLOWED_HOSTS
            or not _video_id(url)):
        return None
    return url


def _validate_page(url: str, page_key: Any) -> bool:
    if not isinstance(page_key, str) or page_key not in PAGE_CONFIG:
        return False
    path = urlparse(url).path.lower()
    return (page_key == "shorts" and "/shorts/" in path) or (page_key == "video" and "/shorts/" not in path)


def _rate_limited() -> bool:
    key = request.remote_addr or "unknown"
    now = time.monotonic()
    entries = REQUESTS[key]
    while entries and now - entries[0] > RATE_WINDOW:
        entries.popleft()
    if len(entries) >= RATE_LIMIT:
        return True
    entries.append(now)
    return False


def _format_quality(item: dict[str, Any]) -> str:
    height = item.get("height")
    return f"{height}p" if isinstance(height, int) and height > 0 else str(item.get("format_note") or "Available")


@app.errorhandler(RequestEntityTooLarge)
def too_large(_error):
    return _json_error("Request payload is too large.", 413)


@app.after_request
def security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("Content-Security-Policy", "default-src 'self'; img-src 'self' https: data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
    return response


@app.route("/")
def home():
    return render_template("index.html", page=PAGE_CONFIG["video"], page_key="video", page_config=PAGE_CONFIG)


@app.get("/<page_key>")
def downloader_page(page_key: str):
    page = PAGE_CONFIG.get(page_key)
    if not page:
        return render_template("info.html", title="Page not found", heading="Page not found", content="This page does not exist."), 404
    return render_template("index.html", page=page, page_key=page_key, page_config=PAGE_CONFIG)


@app.get("/sitemap.xml")
def sitemap():
    routes = ["/", "/shorts", "/faq", "/about", "/privacy", "/cookies", "/terms", "/copyright", "/contact"]
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "".join(f"  <url><loc>{SITE_URL}{route}</loc></url>\n" for route in routes) + "</urlset>\n"
    return app.response_class(xml, mimetype="application/xml")


@app.get("/robots.txt")
def robots():
    return app.response_class(f"User-agent: *\nAllow: /\nDisallow: /api/\nSitemap: {SITE_URL}/sitemap.xml\n", mimetype="text/plain")


INFO = {
    "faq": ("Frequently asked questions", [("Is ClipFetch free?", "Yes. ClipFetch is a simple utility for public YouTube videos and Shorts."), ("What links work?", "Only public, individual YouTube and YouTube Shorts links are accepted. Private, members-only, age-restricted, or login-protected content is not accessed."), ("Are my links stored?", "No. Links are processed for the request and temporary files are removed after delivery."), ("Why did a download fail?", "The video may be unavailable, restricted, rate-limited, or unsupported by YouTube or the selected format.")]),
    "about": ("About ClipFetch", f"ClipFetch helps you save public YouTube videos and Shorts that you own or have permission to use. It does not host a media library, bypass access controls, or use cookies/private sessions."),
    "privacy": ("Privacy policy", f"We process submitted URLs only to provide the requested result. Temporary files are removed after delivery. Basic technical request data may be processed by hosting and abuse-prevention systems. Contact {LEGAL_EMAIL} for privacy questions."),
    "cookies": ("Cookie policy", "ClipFetch does not require account cookies or tracking cookies. Your browser may retain local preferences such as the theme. Hosting and security providers may process standard request logs."),
    "terms": ("Terms and conditions", "Use ClipFetch only for public content you own or are legally allowed to save. Do not infringe copyright, bypass access controls, submit private links, automate abusive traffic, or interfere with the service. You are responsible for the URLs and files you use."),
    "copyright": ("Copyright policy", f"ClipFetch does not host downloaded media. For rights-holder concerns or takedown requests, contact {LEGAL_EMAIL} with the relevant URL and proof of rights."),
    "contact": ("Contact ClipFetch", f"For support, privacy, copyright, or legal requests, email {LEGAL_EMAIL}. Do not send passwords or sensitive personal information."),
}


for _name in INFO:
    def _make_route(name):
        def route():
            heading, content = INFO[name]
            return render_template("info.html", title=f"{heading} | ClipFetch", heading=heading, content=content, faqs=content if name == "faq" else None)
        route.__name__ = f"info_{name}"
        return route
    app.add_url_rule(f"/{_name}", endpoint=f"info_{_name}", view_func=_make_route(_name))


@app.post("/api/info")
def get_info():
    if _rate_limited():
        return _json_error("Too many requests. Please wait a minute and try again.", 429)
    data = _payload()
    url = _validate_url(data.get("url"))
    page_key = data.get("page_key", "video")
    if not url:
        return _json_error("Please provide a valid public YouTube URL.")
    if not _validate_page(url, page_key):
        return _json_error("Choose the matching Video or Shorts tool for this link.")
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError:
        return _json_error("We couldn't read that YouTube video. It may be private, restricted, unavailable, or rate-limited.", 422)
    except Exception:
        app.logger.exception("metadata extraction failed")
        return _json_error("Something went wrong while reading the video. Please try again.", 500)
    formats = []
    for item in info.get("formats", []):
        fid, vcodec, ext = item.get("format_id"), item.get("vcodec"), item.get("ext")
        height = item.get("height")
        if not fid or vcodec in {None, "none"} or ext not in {"mp4", "webm", "mkv"} or not isinstance(height, int) or height < 144 or height > 2160:
            continue
        formats.append({"format_id": str(fid), "ext": str(ext), "quality": _format_quality(item), "has_audio": item.get("acodec") not in {None, "none"}})
    unique = {item["quality"]: item for item in formats}
    if not unique:
        return _json_error("No safe downloadable formats were found for this video.", 422)
    return jsonify({"title": info.get("title") or "YouTube video", "thumbnail": info.get("thumbnail") or "", "formats": list(unique.values())})


@app.post("/api/download")
def download_video():
    if _rate_limited():
        return _json_error("Too many requests. Please wait a minute and try again.", 429)
    data = _payload()
    url, format_id = _validate_url(data.get("url")), data.get("format_id")
    if not url or not isinstance(format_id, str) or not re.fullmatch(r"[\w.-]{1,30}", format_id):
        return _json_error("Invalid video or format selection.")
    temp_dir = DOWNLOAD_DIR / f"job-{os.urandom(8).hex()}"
    temp_dir.mkdir()
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True, "format": f"{format_id}+bestaudio/{format_id}", "outtmpl": str(temp_dir / "clip.%(ext)s"), "merge_output_format": "mp4", "restrictfilenames": True}) as ydl:
            info = ydl.extract_info(url, download=True)
            prepared = Path(ydl.prepare_filename(info))
        files = [p for p in temp_dir.iterdir() if p.is_file() and p.stat().st_size]
        result = max(files, key=lambda p: p.stat().st_size, default=prepared)
        if not result.is_file():
            raise FileNotFoundError
    except yt_dlp.utils.DownloadError:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return _json_error("The download could not be completed. Check that the video is public and try again.", 422)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        app.logger.exception("download failed")
        return _json_error("Something went wrong while preparing the download.", 500)
    name = secure_filename(info.get("title") or "clip") or "clip"
    response = send_file(result, as_attachment=True, download_name=f"{name}.mp4", mimetype="video/mp4", max_age=0)
    response.call_on_close(lambda: shutil.rmtree(temp_dir, ignore_errors=True))
    return response


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1", host="127.0.0.1", port=5000)
