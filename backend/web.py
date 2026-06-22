from __future__ import annotations

from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
from urllib.parse import unquote, urlparse

from .linker import process_brochure


ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public"


class AppHandler(BaseHTTPRequestHandler):
    server_version = "PraktisBrochureLinker/2.0"

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self.send_json({"ok": True, "backend": "python"})
            return

        self.serve_static(parsed.path)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/process":
            self.send_json({"error": "Not found"}, status=404)
            return

        try:
            fields, files = self.parse_multipart()
            pdf = files.get("pdf")
            if not pdf:
                self.send_json({"error": "Upload a PDF brochure first."}, status=400)
                return

            result = process_brochure(
                pdf_bytes=pdf["data"],
                pdf_name=pdf["filename"],
                mapping_bytes=files.get("mapping", {}).get("data"),
                mapping_name=files.get("mapping", {}).get("filename", ""),
                options={
                    "liveLookup": fields.get("liveLookup") == "on",
                    "fallbackSearch": fields.get("fallbackSearch") == "on",
                    "debugBoxes": fields.get("debugBoxes") == "on",
                    "comparePrices": fields.get("comparePrices") == "on",
                    "minDigits": fields.get("minDigits", "5"),
                    "maxDigits": fields.get("maxDigits", "12"),
                    "boxPadding": fields.get("boxPadding", "0"),
                },
            )
            self.send_json(result)
        except Exception as exc:
            self.send_json({"error": str(exc)}, status=500)

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
        print("%s - %s" % (self.address_string(), format % args))


def run(host: str = "127.0.0.1", port: int = 5174):
    server = ThreadingHTTPServer((host, port), AppHandler)
    print(f"Praktis Brochure Linker running at http://{host}:{port}")
    server.serve_forever()
