from __future__ import annotations

from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import mimetypes
import os
from pathlib import Path
import re
import time
from urllib.parse import parse_qs, unquote, urlparse
from uuid import uuid4

from .linker import process_brochure
from .logging_config import configure_logging
from .observability import (
    deep_health,
    local_health,
    metrics_snapshot,
    record_process_failed,
    record_process_finished,
    record_process_started,
    start_health_monitor,
)
from .progress import create_progress, get_progress


ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public"
logger = logging.getLogger(__name__)


class AppHandler(BaseHTTPRequestHandler):
    server_version = "PraktisBrochureLinker/2.0"

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/progress":
            request_id = str(parse_qs(parsed.query).get("requestId", [""])[0]).strip()
            progress = get_progress(request_id)
            if progress is None:
                self.send_json({"status": "pending", "requestId": request_id}, status=404)
            else:
                self.send_json(progress)
            return
        if parsed.path == "/api/health":
            self.send_json(local_health())
            return
        if parsed.path == "/api/health/deep":
            health = deep_health()
            self.send_json(health, status=200 if health.get("ok") else 503)
            return
        if parsed.path == "/api/metrics":
            self.send_json(metrics_snapshot())
            return

        self.serve_static(parsed.path)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/process":
            self.send_json({"error": "Not found"}, status=404)
            return

        request_id = uuid4().hex[:10]
        progress_reporter = None
        started = time.monotonic()
        meta: dict = {"client": self.client_address[0] if self.client_address else ""}
        try:
            fields, files = self.parse_multipart()
            pdf = files.get("pdf")
            if not pdf:
                self.send_json({"error": "Upload a PDF brochure first."}, status=400)
                return

            supplied_request_id = str(fields.get("requestId") or "").strip()
            if re.fullmatch(r"[A-Za-z0-9-]{8,64}", supplied_request_id):
                request_id = supplied_request_id

            meta.update(
                {
                    "file": pdf["filename"],
                    "size_bytes": len(pdf["data"]),
                    "mode": fields.get("mode", ""),
                    "page_mode": fields.get("pageMode", "all"),
                }
            )
            progress_reporter = create_progress(
                request_id,
                fields.get("mode", "") or "fallback_links",
                pdf["filename"],
            )
            record_process_started(request_id, meta)
            logger.info(
                "Process request started: request=%s client=%s pdf=%s size=%s mode=%s pageMode=%s",
                request_id,
                meta["client"],
                pdf["filename"],
                len(pdf["data"]),
                fields.get("mode", ""),
                fields.get("pageMode", "all"),
            )
            result = process_brochure(
                pdf_bytes=pdf["data"],
                pdf_name=pdf["filename"],
                mapping_bytes=files.get("mapping", {}).get("data"),
                mapping_name=files.get("mapping", {}).get("filename", ""),
                excel_bytes=files.get("excel", {}).get("data"),
                excel_name=files.get("excel", {}).get("filename", ""),
                options={
                    "mode": fields.get("mode", ""),
                    "liveLookup": fields.get("liveLookup") == "on",
                    "fallbackSearch": fields.get("fallbackSearch") == "on",
                    "debugBoxes": fields.get("debugBoxes") == "on",
                    "comparePrices": fields.get("comparePrices") == "on",
                    "pageMode": fields.get("pageMode", "all"),
                    "pageNumber": fields.get("pageNumber", ""),
                    "pageStart": fields.get("pageStart", ""),
                    "pageEnd": fields.get("pageEnd", ""),
                    "minDigits": fields.get("minDigits", "5"),
                    "maxDigits": fields.get("maxDigits", "12"),
                    "boxPadding": fields.get("boxPadding", "0"),
                },
                progress_callback=progress_reporter.update,
            )
            duration = time.monotonic() - started
            progress_reporter.finish()
            record_process_finished(request_id, meta, result, duration)
            logger.info(
                "Process request finished: request=%s duration=%.2fs rows=%s links=%s pages=%s",
                request_id,
                duration,
                len(result.get("rows") or []),
                result.get("summary", {}).get("linkedAnnotations"),
                result.get("summary", {}).get("pages"),
            )
            try:
                self.send_json(result)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError) as exc:
                logger.warning(
                    "Client disconnected before process result was delivered: request=%s duration=%.2fs error=%s",
                    request_id,
                    duration,
                    exc,
                )
        except Exception as exc:
            duration = time.monotonic() - started
            if progress_reporter is not None:
                progress_reporter.fail(exc)
            record_process_failed(request_id, meta, exc, duration)
            logger.exception("Process request failed: request=%s duration=%.2fs", request_id, duration)
            try:
                self.send_json({"error": str(exc)}, status=500)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError) as send_exc:
                logger.warning(
                    "Client disconnected before error response was delivered: request=%s duration=%.2fs error=%s",
                    request_id,
                    duration,
                    send_exc,
                )

    def parse_multipart(self):
        content_type = self.headers.get("Content-Type", "")
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        header = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8")
        message = BytesParser(policy=default).parsebytes(header + body)

        fields: dict[str, str] = {}
        files: dict[str, dict] = {}

        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if not name:
                continue
            filename = part.get_param("filename", header="content-disposition")
            payload = part.get_payload(decode=True) or b""
            if filename:
                files[name] = {"filename": filename, "data": payload}
            else:
                charset = part.get_content_charset() or "utf-8"
                fields[name] = payload.decode(charset, errors="replace")

        return fields, files

    def serve_static(self, request_path: str):
        if request_path in {"", "/"}:
            target = PUBLIC / "index.html"
        else:
            relative = unquote(request_path).lstrip("/")
            target = (PUBLIC / relative).resolve()
            if not str(target).startswith(str(PUBLIC.resolve())):
                self.send_error(403)
                return
            if not target.exists() or not target.is_file():
                target = PUBLIC / "index.html"

        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, payload: dict, status: int = 200):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        message = format % args
        if "GET /api/progress?" in message:
            logger.debug("%s - %s", self.address_string(), message)
            return
        logger.info("%s - %s", self.address_string(), message)


def run(host: str | None = None, port: int | None = None):
    configure_logging()
    start_health_monitor()
    host = host or os.environ.get("BROCHURE_HOST", "0.0.0.0")
    port = port or int(os.environ.get("BROCHURE_PORT", "5111"))
    server = ThreadingHTTPServer((host, port), AppHandler)
    logger.info("Praktis Brochure Linker listening on %s:%s", host, port)
    if host == "0.0.0.0":
        logger.info("Open on this computer: http://127.0.0.1:%s", port)
        logger.info("Open from another computer: http://<this-computer-ip>:%s", port)
    else:
        logger.info("Open: http://%s:%s", host, port)
    server.serve_forever()
