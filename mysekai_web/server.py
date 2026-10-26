import json
import logging
import os
import re
import socket
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .processing import InvalidUpload, process
from .storage import Store

ROOT = Path(__file__).resolve().parent.parent
UID_PATTERN = re.compile(r"[0-9]{6,25}")
TOKEN_PATTERN = re.compile(r"[0-9a-f]{64}")
REGIONS = {"cn", "jp", "tw", "en", "kr"}
LOGGER = logging.getLogger("mysekai")


@dataclass
class Settings:
    db_path: Path
    public_base_url: str = "http://127.0.0.1:3939"
    default_region: str = "cn"
    max_upload_bytes: int = 100_000_000
    image_width: int = 1024
    requests_per_minute: int = 120

    def __post_init__(self):
        self.public_base_url = self.public_base_url.rstrip("/")
        url = urlsplit(self.public_base_url)
        if (url.scheme not in {"http", "https"} or not url.netloc or url.username
                or url.password or url.query or url.fragment or url.path
                or re.search(r"[\s|]", self.public_base_url)):
            raise ValueError("PUBLIC_BASE_URL must be an http(s) origin without a path")
        if self.default_region not in REGIONS:
            raise ValueError("Unsupported API_REGION")
        if not 256 <= self.image_width <= 4096 or self.max_upload_bytes < 1:
            raise ValueError("Invalid image width or upload limit")
        if self.requests_per_minute < 1:
            raise ValueError("REQUESTS_PER_MINUTE must be positive")


class RateLimiter:
    def __init__(self, limit):
        self.limit = limit
        self.buckets = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, address):
        now = time.monotonic()
        with self.lock:
            # Expire inactive addresses, including on small installations.
            for key in list(self.buckets):
                if not self.buckets[key] or self.buckets[key][-1] <= now - 60:
                    del self.buckets[key]
            bucket = self.buckets[address]
            while bucket and bucket[0] <= now - 60:
                bucket.popleft()
            if len(bucket) >= self.limit:
                return False
            bucket.append(now)
            return True


def module_text(base, uid, token):
    pattern = r"^https://mkcn-prod-public-60001-[12]\.dailygn\.com/api/user/\d+/mysekai\?isForceAllReloadOnlyMysekai=True(?:&|$)"
    return (
        "#!name=MySEKAI Maps\n#!desc=同步国服 MySEKAI 地图到自己的网站\n\n"
        "[Script]\n"
        f"mysekai-upload = type=http-response,pattern={pattern},requires-body=1,"
        "binary-body-mode=1,max-size=100000000,timeout=60,"
        f"script-path={base}/upload.js?v=2,argument={base}|{uid}|{token}|cn\n\n"
        "[MITM]\nhostname = %APPEND% mkcn-prod-public-60001-1.dailygn.com, "
        "mkcn-prod-public-60001-2.dailygn.com\n"
    )


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, settings):
        self.settings = settings
        self.store = Store(settings.db_path)
        self.limiter = RateLimiter(settings.requests_per_minute)
        self.upload_slots = threading.BoundedSemaphore(2)
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = "MySEKAI"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(60)

    def log_message(self, format, *args):
        # Never log URLs, request bodies or credentials.
        pass

    def send(self, status, content, content_type="application/json; charset=utf-8", filename=None):
        if isinstance(content, dict):
            content = json.dumps(content, ensure_ascii=False).encode()
        elif isinstance(content, str):
            content = content.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' blob:; script-src 'self'; style-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header("Connection", "close")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        if status == 429:
            self.send_header("Retry-After", "60")
        self.end_headers()
        self.close_connection = True
        try:
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def error(self, status, code, message):
        self.send(status, {"ok": False, "error": code, "message": message})

    def credentials(self):
        if not self.server.limiter.allow(self.client_address[0]):
            self.error(429, "rate_limited", "请求太频繁，请一分钟后再试。")
            return None
        header = self.headers.get("Authorization", "")
        token = header[7:] if header.startswith("Bearer ") else ""
        uid = self.headers.get("X-User-Id", "")
        if not TOKEN_PATTERN.fullmatch(token) or not UID_PATTERN.fullmatch(uid):
            self.error(401, "invalid_credentials", "请填写有效的 UID 和密码。")
            return None
        existing = self.server.store.metadata(token)
        if existing is not None and existing["uid"] != uid:
            self.error(401, "invalid_credentials", "UID 与上传凭据不一致。")
            return None
        # Existing snapshots already use SHA-256(uid_password) as their key.
        return uid, token

    def do_GET(self):
        path = urlsplit(self.path).path
        public = {
            "/": (ROOT / "static/index.html", "text/html; charset=utf-8"),
            "/app.js": (ROOT / "static/app.js", "text/javascript; charset=utf-8"),
            "/auth.js": (ROOT / "static/auth.js", "text/javascript; charset=utf-8"),
            "/style.css": (ROOT / "static/style.css", "text/css; charset=utf-8"),
            "/upload.js": (ROOT / "static/upload.js", "text/javascript; charset=utf-8"),
            "/preview-map.png": (ROOT / "assets/map/grassland.png", "image/png"),
        }
        if path == "/healthz":
            self.send(200, {"ok": True})
            return
        if path in public:
            file, mime = public[path]
            self.send(200, file.read_bytes(), mime)
            return
        if path == "/api/config":
            self.send(200, {"ok": True, "data": {"public_base_url": self.server.settings.public_base_url,
                      "max_upload_bytes": self.server.settings.max_upload_bytes}})
            return
        if path not in {"/api/maps", "/api/module"} and not re.fullmatch(r"/api/maps/[5-8]/image", path):
            self.error(404, "not_found", "接口不存在。")
            return
        auth = self.credentials()
        if not auth:
            return
        uid, key_hash = auth
        if path == "/api/module":
            self.send(200, module_text(self.server.settings.public_base_url, uid, key_hash),
                      "text/plain; charset=utf-8", "mysekai.module")
        elif path == "/api/maps":
            data = self.server.store.metadata(key_hash)
            if data is None:
                self.error(404, "no_data", "没有找到地图，请检查 UID 和密码，或先上传数据。")
            else:
                self.send(200, {"ok": True, "data": data})
        else:
            image = self.server.store.image(key_hash, int(path.split("/")[3]))
            if image is None:
                self.error(404, "no_data", "没有找到这张地图。")
            else:
                self.send(200, image, "image/png")

    def do_POST(self):
        if urlsplit(self.path).path != "/api/upload":
            self.error(404, "not_found", "接口不存在。")
            return
        auth = self.credentials()
        if not auth:
            return
        uid, key_hash = auth
        if self.headers.get("Transfer-Encoding"):
            self.error(400, "unsupported_encoding", "上传需要 Content-Length，不支持分块传输。")
            return
        try:
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or not lengths[0].isdigit():
                raise ValueError()
            length = int(lengths[0])
        except ValueError:
            self.error(411, "invalid_length", "请提供有效的 Content-Length。")
            return
        if length <= 0 or length > self.server.settings.max_upload_bytes:
            self.error(413 if length > 0 else 400, "invalid_size", "文件为空或超过上传大小限制。")
            return
        region = self.headers.get("X-Region", self.server.settings.default_region)
        if region not in REGIONS:
            self.error(400, "invalid_region", "不支持这个区服。")
            return
        if not self.server.upload_slots.acquire(blocking=False):
            self.error(503, "busy", "正在处理其他地图，请稍后重试。")
            return
        try:
            body = self.rfile.read(length)
            if len(body) != length:
                self.error(400, "incomplete_body", "上传中断，文件内容不完整。")
                return
            metadata, images = process(body, uid, region, self.headers.get("X-Original-Url", ""),
                                       ROOT / "assets", self.server.settings.image_width)
            self.server.store.save(key_hash, uid, body, metadata, images)
            self.send(200, {"ok": True, "data": metadata})
        except InvalidUpload as exc:
            self.error(422, "invalid_upload", str(exc))
        except (socket.timeout, TimeoutError):
            self.error(408, "upload_timeout", "上传超时，请重试。")
        except Exception as exc:
            LOGGER.error("Upload processing failed (%s)", type(exc).__name__)
            self.error(500, "processing_failed", "地图处理失败，原有数据已保留，请查看服务日志。")
        finally:
            self.server.upload_slots.release()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    port = int(os.getenv("PORT", "3939"))
    settings = Settings(
        db_path=Path(os.getenv("DATABASE_PATH", str(ROOT / "data/mysekai.sqlite3"))),
        public_base_url=os.getenv("PUBLIC_BASE_URL", f"http://127.0.0.1:{port}"),
        default_region=os.getenv("API_REGION", "cn"),
        max_upload_bytes=int(os.getenv("MAX_UPLOAD_BYTES", "100000000")),
        image_width=int(os.getenv("MAP_IMAGE_WIDTH", "1024")),
        requests_per_minute=int(os.getenv("REQUESTS_PER_MINUTE", "120")),
    )
    server = Server((os.getenv("HOST", "127.0.0.1"), port), settings)
    LOGGER.info("MySEKAI Maps listening on port %s", port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
