from __future__ import annotations

import shutil
import tempfile
import os
import re
import json
from html import unescape
from html.parser import HTMLParser
from urllib.request import Request, urlopen
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yt_dlp
from flask import Flask, after_this_request, jsonify, render_template, request, send_file
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024

MAX_URL_LENGTH = 2_048
DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "video-downloader"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _json_error(message: str, status: int = 400):
    return jsonify({"error": message}), status


def _get_payload() -> dict[str, Any] | None:
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else None


def _validate_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    url = value.strip()
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
    except ValueError:
        return None
    if (
        not url
        or len(url) > MAX_URL_LENGTH
        or parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or not hostname
        or hostname.lower() not in ALLOWED_HOSTS
    ):
        return None
    return url


def _validate_tool_url(url: str, page_key: Any) -> str | None:
    if page_key is None:
        return url
    if not isinstance(page_key, str) or page_key not in PAGE_CONFIG:
        return None

    path = urlparse(url).path.rstrip("/").lower()
    if page_key == "viewer":
        reserved = ("/p/", "/reel/", "/reels/", "/stories/", "/tv/")
        return url if not any(item in f"{path}/" for item in reserved) else None

    required_paths = {
        "reels": ("/reel/", "/reels/"),
        "story": ("/stories/",),
        "igtv": ("/tv/",),
        "photo": ("/p/",),
        "carousel": ("/p/",),
        "video": ("/p/", "/reel/", "/reels/", "/tv/"),
    }
    return url if any(item in f"{path}/" for item in required_paths[page_key]) else None


def _format_quality(video_format: dict[str, Any]) -> str:
    height = video_format.get("height")
    if isinstance(height, int) and height > 0:
        return f"{height}p"
    return str(video_format.get("format_note") or video_format.get("resolution") or "Available")


def _extract_original_image_url(html: str) -> str | None:
    """Find Instagram's original public display URL before its social thumbnail."""
    matches = re.findall(r'"display_url"\s*:\s*"([^"]+)"', html)
    for value in matches:
        try:
            candidate = json.loads(f'"{value}"')
        except json.JSONDecodeError:
            candidate = value.replace(r"\/", "/").replace(r"\u0026", "&")
        if _is_allowed_media_url(candidate):
            return candidate
    return None


def _extract_image_fallback(url: str) -> dict[str, Any] | None:
    """Read public Instagram Open Graph metadata for image-only posts."""
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
        },
    )
    with urlopen(request, timeout=15) as response:
        html = response.read(5_000_000).decode("utf-8", errors="replace")

    class MetadataParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.metadata: dict[str, str] = {}

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
            if tag.lower() != "meta":
                return
            attributes = {key.lower(): value or "" for key, value in attrs}
            key = attributes.get("property") or attributes.get("name")
            if key:
                self.metadata[key.lower()] = unescape(attributes.get("content", ""))

    parser = MetadataParser()
    parser.feed(html)

    image_url = _extract_original_image_url(html)
    image_url = image_url or parser.metadata.get("og:image") or parser.metadata.get("twitter:image")
    if not image_url:
        return None

    title = parser.metadata.get("og:title") or "Instagram photo"
    return {
        "title": title,
        "thumbnail": image_url,
        "formats": [
            {
                "format_id": "image",
                "ext": "jpg",
                "quality": "Available public image",
                "url": image_url,
            }
        ],
    }


LEGAL_EMAIL = "01.abhiiii@gmail.com"
BUSINESS_NAME = "Insta Video Downloader"
JURISDICTION = "India"
SITE_URL = os.environ.get(
    "SITE_URL",
    "https://clipfetch.in",
).rstrip("/")
ALLOWED_HOSTS = {"instagram.com", "www.instagram.com"}
ALLOWED_MEDIA_HOST_SUFFIXES = (".cdninstagram.com", ".fbcdn.net", ".instagram.com")


def _is_allowed_media_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
        hostname = parsed.hostname
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and bool(hostname)
        and (
            hostname.lower() in ALLOWED_HOSTS
            or hostname.lower().endswith(ALLOWED_MEDIA_HOST_SUFFIXES)
        )
    )


@app.errorhandler(RequestEntityTooLarge)
def handle_request_too_large(_error):
    return _json_error("Request payload is too large.", 413)


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=()",
    )
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' https: data:; "
        "style-src 'self'; script-src 'self'; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; "
        "form-action 'self'",
    )
    if request.is_secure:
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains",
        )
    return response


PAGE_CONFIG = {
    "video": {
        "path": "/",
        "label": "Video Downloader",
        "description": "Download Instagram videos in high quality.",
        "placeholder": "Insert an Instagram video link",
        "icon": "▶",
    },
    "photo": {
        "path": "/photo",
        "label": "Photo Downloader",
        "description": "Save Instagram photos and posts in seconds.",
        "placeholder": "Insert an Instagram photo link",
        "icon": "▧",
    },
    "reels": {
        "path": "/reels",
        "label": "Reels Downloader",
        "description": "Download Instagram Reels for offline viewing.",
        "placeholder": "Insert an Instagram Reel link",
        "icon": "◌",
    },
    "story": {
        "path": "/story",
        "label": "Story Downloader",
        "description": "Save public Instagram Stories with one click.",
        "placeholder": "Insert an Instagram Story link",
        "icon": "◉",
    },
    "viewer": {
        "path": "/viewer",
        "label": "Profile Viewer",
        "description": "Explore public Instagram profile media.",
        "placeholder": "Insert a public profile link",
        "icon": "◍",
    },
    "igtv": {
        "path": "/igtv",
        "label": "IGTV Downloader",
        "description": "Download long-form Instagram videos.",
        "placeholder": "Insert an IGTV link",
        "icon": "▣",
    },
    "carousel": {
        "path": "/carousel",
        "label": "Carousel Downloader",
        "description": "Save photos and videos from carousel posts.",
        "placeholder": "Insert a carousel post link",
        "icon": "▤",
    },
}


@app.route("/")
def home():
    return render_template("index.html", page=PAGE_CONFIG["video"], page_key="video", page_config=PAGE_CONFIG)


@app.get("/<page_key>")
def downloader_page(page_key: str):
    page = PAGE_CONFIG.get(page_key)
    if not page:
        return render_template("info.html", title="Page not found", heading="Page not found", content="The page you requested does not exist."), 404
    return render_template("index.html", page=page, page_key=page_key, page_config=PAGE_CONFIG)


@app.get("/sitemap.xml")
def sitemap():
    routes = [
        "/",
        "/photo",
        "/reels",
        "/story",
        "/viewer",
        "/igtv",
        "/carousel",
        "/faq",
        "/about",
        "/privacy",
        "/cookies",
        "/terms",
        "/copyright",
        "/contact",
    ]
    entries = "\n".join(
        f"  <url><loc>{SITE_URL}{route}</loc></url>" for route in routes
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{entries}\n"
        "</urlset>\n"
    )
    response = app.response_class(xml, mimetype="application/xml")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.get("/robots.txt")
def robots():
    body = f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}/sitemap.xml\n"
    return app.response_class(body, mimetype="text/plain")


@app.get("/faq")
def faq():
    return render_template(
        "info.html",
        title=f"FAQ | {BUSINESS_NAME}",
        heading="Frequently asked questions",
        faqs=[
            ("Is my link stored?", "No. Links are processed in real time and temporary files are removed after delivery."),
            ("What links are supported?", "Public links supported by yt-dlp can be processed. Private or login-protected content is not accessed."),
            ("Where is my file saved?", "Your browser saves the file to its normal Downloads folder."),
            ("Why did a download fail?", "The post may be private, unavailable, rate-limited, or unsupported by the source platform."),
        ],
    )


@app.get("/about")
def about():
    return render_template(
        "info.html",
        title=f"About | {BUSINESS_NAME}",
        heading=f"About {BUSINESS_NAME}",
        content=f"{BUSINESS_NAME} is an original utility for saving public media that you are allowed to use. It does not host or index downloaded content.",
    )


@app.get("/privacy")
def privacy():
    return render_template(
        "info.html",
        title=f"Privacy Policy | {BUSINESS_NAME}",
        heading="Privacy policy",
        updated="September 10, 2026",
        sections=[
            ("1. Who we are", [f"{BUSINESS_NAME} is an online utility for processing public media URLs. This policy explains what information is handled when you use this website. For privacy questions, contact {LEGAL_EMAIL}."]),
            ("2. Information we process", ["When you submit a URL, our server processes that URL and sends it to the media extraction service used by the application. We do not require an account, name, phone number, or password.", "Technical information such as IP address, browser type, request time, and error logs may be processed by your hosting provider or security tooling to operate and protect the service."]),
            ("3. How we use information", ["We use submitted URLs only to retrieve media information and provide the requested download. We may use limited technical logs to prevent abuse, troubleshoot failures, maintain availability, and comply with legal obligations."]),
            ("4. Retention and deletion", ["Temporary downloaded files are removed after the response is sent or when a failed request is cleaned up. We do not intentionally create a history of submitted URLs. Hosting, access, or security logs may remain for the period configured by the hosting provider."]),
            ("5. Sharing", ["We do not sell submitted URLs or personal information. Information may be processed by hosting, infrastructure, security, advertising, analytics, or media-extraction providers only as necessary to operate the service, or when required by law."]),
            ("6. Your choices and rights", ["Depending on your location, you may have rights to access, correct, delete, restrict, or object to processing of personal information. Contact the operator using the published contact address to make a request."]),
            ("7. Children", ["This service is not directed to children. Do not submit personal information belonging to a child."]),
            ("8. Changes", [f"We may update this policy when the service or applicable requirements change. The effective date at the top of this page will be updated when material changes are made. Privacy requests may be sent to {LEGAL_EMAIL}."]),
        ],
    )


@app.get("/terms")
def terms():
    return render_template(
        "info.html",
        title=f"Terms and Conditions | {BUSINESS_NAME}",
        heading="Terms and conditions",
        updated="September 10, 2026",
        sections=[
            ("1. Acceptance", [f"By accessing {BUSINESS_NAME} or using its downloader tools, you agree to these terms. If you do not agree, do not use the service."]),
            ("2. Permitted use", ["You may use the service only for public content that you own or are legally authorized to download. You must comply with copyright law, privacy law, the source platform's terms, and any applicable local regulations."]),
            ("3. Prohibited use", ["Do not use the service to infringe copyright, bypass access controls, download private or restricted content, harass people, distribute malware, automate abusive traffic, or interfere with the service."]),
            ("4. Your responsibility", [f"You are solely responsible for the URLs you submit, the content you download, and how you use it. {BUSINESS_NAME} does not grant you ownership or a license to downloaded content."]),
            ("5. Service availability", ["The service is provided on an availability basis. Features may change, be limited, or be discontinued without notice. We do not guarantee that every URL or format will be supported."]),
            ("6. Third-party services", [f"Media extraction depends on third-party platforms and open-source tools. {BUSINESS_NAME} does not control their availability, policies, content, or changes."]),
            ("7. Intellectual property", [f"{BUSINESS_NAME}'s original name, design, and software are owned by their respective operator. Third-party trademarks and media remain the property of their owners."]),
            ("8. Disclaimer and limitation", [f"To the maximum extent allowed by law, the service is provided without warranties. {BUSINESS_NAME} is not liable for loss, interruption, unavailable content, or misuse of downloaded media. Nothing here limits rights that cannot legally be excluded."]),
            ("9. Indemnity", ["You agree to defend and hold the operator harmless from claims arising from your unlawful use of the service or violation of these terms."]),
            ("10. Governing law and contact", [f"These terms are governed by the applicable laws of {JURISDICTION}, subject to mandatory consumer protections. These terms may be updated by publishing a revised version. Questions or legal notices should be sent to {LEGAL_EMAIL}."]),
        ],
    )


@app.get("/contact")
def contact():
    return render_template(
        "info.html",
        title=f"Contact | {BUSINESS_NAME}",
        heading="Contact",
        updated="September 10, 2026",
        sections=[
            ("Get in touch", [f"For support, copyright notices, privacy requests, or legal inquiries, email {LEGAL_EMAIL}."]),
            ("What to include", ["Include the relevant page or URL, a clear description of your request, and enough information for the operator to respond. Do not send passwords or sensitive personal information."]),
        ],
    )


@app.get("/cookies")
def cookies():
    return render_template(
        "info.html",
        title=f"Cookie Policy | {BUSINESS_NAME}",
        heading="Cookie and tracking policy",
        updated="September 10, 2026",
        sections=[
            ("1. Essential storage", ["The service does not require an account. Your browser may store normal preferences such as theme state using local browser storage."]),
            ("2. Analytics and advertising", ["This website may use Google Analytics to understand aggregate traffic and Google AdSense or similar advertising services to display ads. These providers may use cookies or similar technologies according to their own policies."]),
            ("3. Hosting and security", ["Our hosting provider may process technical request data, including IP address and timestamps, for security, rate limiting, and reliability."]),
            ("4. Your controls", ["You can clear browser storage, block cookies, or use browser privacy controls. You may also manage personalized advertising through Google's ad settings. Blocking essential browser features may affect page functionality."]),
        ],
    )


@app.get("/copyright")
def copyright_policy():
    return render_template(
        "info.html",
        title=f"Copyright Policy | {BUSINESS_NAME}",
        heading="Copyright and takedown policy",
        updated="September 10, 2026",
        sections=[
            ("1. No content hosting", [f"{BUSINESS_NAME} does not host a searchable library of downloaded media. Downloads are generated on demand and temporary files are removed after delivery."]),
            ("2. Rights holder notice", [f"If you believe a URL is being used through this service to infringe your rights, send a notice to {LEGAL_EMAIL} with your identity, the copyrighted work, the URL, your good-faith statement, and a statement that the information is accurate."]),
            ("3. Response", ["We may investigate valid notices, restrict abusive URLs, and take other reasonable action. We cannot remove content hosted by the original platform; contact that platform for removal from its servers."]),
        ],
    )


@app.post("/api/info")
def get_info():
    payload = _get_payload()
    url = _validate_url(payload.get("url") if payload else None)
    page_key = payload.get("page_key") if payload else None
    if not url:
        return _json_error("Please provide a valid video URL.")
    if payload and "page_key" in payload and not _validate_tool_url(url, page_key):
        return _json_error("This link does not match the selected downloader.")

    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        if page_key == "story":
            return _json_error(
                "This story is unavailable to the downloader. It may be expired, "
                "private, login-required, or temporarily blocked by Instagram.",
                422,
            )
        try:
            fallback = _extract_image_fallback(url)
        except Exception:
            fallback = None
        if fallback:
            return jsonify(fallback)
        return _json_error(f"Unable to fetch media details: {exc}", 422)
    except Exception:
        app.logger.exception("Unexpected error while extracting video information")
        return _json_error("An unexpected error occurred while fetching video details.", 500)

    formats = []
    for video_format in info.get("formats", []):
        direct_url = video_format.get("url")
        format_id = video_format.get("format_id")
        video_codec = video_format.get("vcodec")
        if not direct_url or not format_id or video_codec in {None, "none"}:
            continue
        formats.append(
            {
                "format_id": str(format_id),
                "ext": str(video_format.get("ext") or "bin"),
                "quality": _format_quality(video_format),
                "url": direct_url,
            }
        )

    # Some Instagram photo/carousel URLs are accepted by yt-dlp but return no
    # formats. Try the OG image path in that case instead of showing a dead
    # "No downloadable formats" result.
    if not formats and page_key != "story":
        try:
            fallback = _extract_image_fallback(url)
        except Exception:
            fallback = None
        if fallback:
            return jsonify(fallback)
        return _json_error(
            "No downloadable video formats were found. The post may be private, "
            "unavailable, or temporarily blocked by Instagram.",
            422,
        )

    return jsonify(
        {
            "title": info.get("title") or "Untitled video",
            "thumbnail": info.get("thumbnail") or "",
            "formats": formats,
        }
    )


@app.post("/api/download")
def download_video():
    payload = _get_payload()
    url = _validate_url(payload.get("url") if payload else None)
    format_id = payload.get("format_id") if payload else None
    page_key = payload.get("page_key") if payload else None
    if not url:
        return _json_error("Please provide a valid video URL.")
    if payload and "page_key" in payload and not _validate_tool_url(url, page_key):
        return _json_error("This link does not match the selected downloader.")
    if page_key == "story" and format_id == "image":
        return _json_error(
            "This story does not expose a downloadable image. It may be expired, "
            "private, login-required, or temporarily blocked by Instagram.",
            422,
        )
    if not isinstance(format_id, str) or not format_id.strip() or len(format_id) > 200:
        return _json_error("Please select a valid format.")
    if format_id.strip() != "image" and not re.fullmatch(
        r"[A-Za-z0-9._,+-]+",
        format_id.strip(),
    ):
        return _json_error("Please select a valid format.")

    if format_id.strip() == "image":
        try:
            image_info = _extract_image_fallback(url)
            if not image_info:
                return _json_error("No downloadable image was found.", 422)
            image_url = image_info["formats"][0]["url"]
            if not _is_allowed_media_url(image_url):
                return _json_error("The media source is not trusted.", 422)
            file_descriptor, temporary_path = tempfile.mkstemp(
                suffix=".jpg",
                dir=DOWNLOAD_DIR,
            )
            os.close(file_descriptor)
            temporary_file = Path(temporary_path)
            with urlopen(Request(image_url, headers={"User-Agent": "Mozilla/5.0"}), timeout=30) as source:
                temporary_file.write_bytes(source.read())
        except Exception:
            app.logger.exception("Unexpected error while downloading image")
            return _json_error("Unable to download the image.", 422)

        @after_this_request
        def remove_image(response):
            try:
                temporary_file.unlink(missing_ok=True)
            except OSError:
                app.logger.warning("Could not remove temporary image: %s", temporary_file)
            return response

        return send_file(
            temporary_file,
            as_attachment=True,
            download_name="instagram-image.jpg",
            mimetype="image/jpeg",
            max_age=0,
        )

    temporary_dir = Path(tempfile.mkdtemp(prefix="download-", dir=DOWNLOAD_DIR))
    output_template = str(temporary_dir / "video.%(ext)s")
    options = {
        "format": f"{format_id.strip()}+bestaudio/{format_id.strip()}/best",
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "restrictfilenames": True,
        "merge_output_format": "mp4",
    }

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
            prepared_path = Path(ydl.prepare_filename(info))
        downloaded_files = [
            path for path in temporary_dir.iterdir()
            if path.is_file() and path.stat().st_size > 0
        ]
        downloaded_path = max(
            downloaded_files,
            key=lambda path: path.stat().st_size,
            default=prepared_path,
        )
        if not downloaded_path.is_file():
            shutil.rmtree(temporary_dir, ignore_errors=True)
            return _json_error("The downloaded file could not be found.", 500)
    except yt_dlp.utils.DownloadError as exc:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        return _json_error(f"Unable to download the video: {exc}", 422)
    except Exception:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        app.logger.exception("Unexpected error while downloading video")
        return _json_error("An unexpected error occurred while downloading the video.", 500)

    @after_this_request
    def remove_temporary_file(response):
        try:
            downloaded_path.unlink(missing_ok=True)
            temporary_dir.rmdir()
        except OSError:
            shutil.rmtree(temporary_dir, ignore_errors=True)
            app.logger.warning("Could not remove temporary download directory: %s", temporary_dir)
        return response

    filename = secure_filename(info.get("title") or "video") or "video"
    extension = downloaded_path.suffix.lstrip(".") or "bin"
    return send_file(
        downloaded_path,
        as_attachment=True,
        download_name=f"{filename}.{extension}",
        max_age=0,
    )


if __name__ == "__main__":
    app.run(
        debug=os.environ.get("FLASK_DEBUG") == "1",
        host="127.0.0.1",
        port=5000,
    )
