from __future__ import annotations

import argparse
import json
import mimetypes
from email.parser import BytesParser
from email.policy import default as default_email_policy
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import TCPServer
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from .project_store import ProjectStore
from .workflow_api import WorkflowBackend


RouteResult = Tuple[int, Dict[str, Any]]


class LocalThreadingHTTPServer(ThreadingHTTPServer):
    def server_bind(self) -> None:
        TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = int(port)


class WorkflowRequestHandler(BaseHTTPRequestHandler):
    backend: WorkflowBackend

    server_version = "MiMoWorkflowBackend/0.1"

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._write_headers()
        self.end_headers()

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_PATCH(self) -> None:
        self._handle("PATCH")

    def do_DELETE(self) -> None:
        self._handle("DELETE")

    def log_message(self, format: str, *args: Any) -> None:
        if getattr(self.server, "quiet", False):
            return
        super().log_message(format, *args)

    def _handle(self, method: str) -> None:
        try:
            if self._serve_project_media(method):
                return
            status, payload = self.dispatch(method)
        except KeyError as exc:
            status, payload = HTTPStatus.NOT_FOUND, {"error": str(exc)}
        except ValueError as exc:
            status, payload = HTTPStatus.BAD_REQUEST, {"error": str(exc)}
        except Exception as exc:
            status, payload = HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)}

        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self._write_headers(content_length=len(body))
        self.end_headers()
        self.wfile.write(body)

    def _serve_project_media(self, method: str) -> bool:
        parsed = urlparse(self.path)
        path = parsed.path.strip("/")
        parts = [part for part in path.split("/") if part]
        if method != "GET" or len(parts) != 3 or parts[0] != "projects" or parts[2] != "media":
            return False

        media_path = (parse_qs(parsed.query).get("path") or [""])[0]
        file_path = self.backend.resolve_project_media(parts[1], media_path)
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self._write_headers(content_length=file_path.stat().st_size, content_type=content_type)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with file_path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)
        return True

    def dispatch(self, method: str) -> RouteResult:
        path = urlparse(self.path).path.strip("/")
        parts = [part for part in path.split("/") if part]
        payload = self.read_request_body() if method in {"POST", "PATCH", "DELETE"} else {}

        if method == "GET" and parts == ["health"]:
            return HTTPStatus.OK, {"ok": True, "service": "mimo-workflow-backend"}

        if method == "GET" and parts == ["projects"]:
            return HTTPStatus.OK, self.backend.list_projects()

        if method == "POST" and parts == ["projects"]:
            return HTTPStatus.CREATED, self.backend.create_project(payload)

        if method == "POST" and parts == ["agent", "command"]:
            return HTTPStatus.ACCEPTED, self.backend.agent_command(payload)

        if method == "GET" and len(parts) == 2 and parts[0] == "jobs":
            return HTTPStatus.OK, {"job": self.backend.get_job(parts[1])}

        if method == "POST" and len(parts) == 3 and parts[0] == "jobs" and parts[2] == "cancel":
            return HTTPStatus.OK, {"job": self.backend.cancel_job(parts[1])}

        if len(parts) == 2 and parts[0] == "projects":
            project_id = parts[1]
            if method == "PATCH":
                return HTTPStatus.OK, self.backend.update_project(project_id, payload)
            if method == "DELETE":
                return HTTPStatus.OK, self.backend.delete_project(project_id)

        if len(parts) >= 3 and parts[0] == "projects":
            project_id = parts[1]
            subpath = parts[2:]
            if method == "GET" and subpath == ["status"]:
                return HTTPStatus.OK, self.backend.get_status(project_id)
            if method == "GET" and subpath == ["snapshot"]:
                return HTTPStatus.OK, self.backend.get_project(project_id)
            if method == "POST" and subpath == ["activities"]:
                return HTTPStatus.CREATED, self.backend.create_activity(project_id, payload)
            if method == "PATCH" and len(subpath) == 2 and subpath[0] == "activities":
                return HTTPStatus.OK, self.backend.update_activity(project_id, subpath[1], payload)
            if method == "POST" and len(subpath) == 3 and subpath[0] == "activities" and subpath[2] == "select":
                return HTTPStatus.OK, self.backend.select_activity(project_id, subpath[1])
            if method == "POST" and subpath == ["reference", "transcribe"]:
                return HTTPStatus.ACCEPTED, self.backend.transcribe_reference(project_id, payload)
            if method == "POST" and subpath == ["reference", "upload-videos"]:
                return HTTPStatus.OK, self.backend.upload_reference_videos(project_id, payload.get("files") or [])
            if method == "POST" and subpath == ["reference", "validate-videos"]:
                return HTTPStatus.OK, self.backend.validate_reference_videos(project_id, payload)
            if method == "POST" and subpath == ["reference", "videos"]:
                return HTTPStatus.OK, self.backend.set_reference_videos(project_id, payload)
            if method == "POST" and subpath == ["reference", "text"]:
                return HTTPStatus.OK, self.backend.set_reference_text(project_id, payload)
            if method == "POST" and subpath == ["copy", "rewrite"]:
                return HTTPStatus.ACCEPTED, self.backend.rewrite_copy(project_id, payload)
            if method == "POST" and subpath == ["copy", "titles"]:
                return HTTPStatus.ACCEPTED, self.backend.generate_copy_titles(project_id, payload)
            if method == "POST" and subpath == ["storyboard", "analyze"]:
                return HTTPStatus.ACCEPTED, self.backend.analyze_storyboard(project_id, payload)
            if method == "POST" and subpath == ["materials", "tag"]:
                return HTTPStatus.ACCEPTED, self.backend.tag_materials(project_id, payload)
            if method == "POST" and subpath == ["voiceovers", "generate"]:
                return HTTPStatus.ACCEPTED, self.backend.generate_voiceovers(project_id, payload)
            if method == "POST" and subpath == ["voiceovers", "regenerate"]:
                return HTTPStatus.ACCEPTED, self.backend.regenerate_voiceover(project_id, payload)
            if method == "POST" and subpath == ["assembly", "plan"]:
                return HTTPStatus.ACCEPTED, self.backend.build_assembly_plan(project_id, payload)
            if method == "POST" and subpath == ["legacy", "generate"]:
                return HTTPStatus.ACCEPTED, self.backend.legacy_generate(project_id, payload)

        raise KeyError(f"route not found: {method} /{path}")

    def read_request_body(self) -> Dict[str, Any]:
        content_type = self.headers.get("Content-Type", "")
        if content_type.lower().startswith("multipart/form-data"):
            return self.read_multipart_body(content_type)
        return self.read_json_body()

    def read_json_body(self) -> Dict[str, Any]:
        content_length = int(self.headers.get("Content-Length") or 0)
        if content_length <= 0:
            return {}
        raw = self.rfile.read(content_length)
        if not raw.strip():
            return {}
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        return data

    def read_multipart_body(self, content_type: str) -> Dict[str, Any]:
        content_length = int(self.headers.get("Content-Length") or 0)
        if content_length <= 0:
            return {"files": []}

        raw = self.rfile.read(content_length)
        message = BytesParser(policy=default_email_policy).parsebytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + raw
        )
        files = []
        fields: Dict[str, Any] = {}
        for part in message.iter_parts():
            if part.get_content_disposition() != "form-data":
                continue
            name = part.get_param("name", header="content-disposition") or ""
            filename = part.get_filename()
            content = part.get_payload(decode=True) or b""
            if filename:
                files.append(
                    {
                        "field": name,
                        "filename": filename,
                        "content_type": part.get_content_type(),
                        "content": content,
                    }
                )
            elif name:
                fields[name] = content.decode(part.get_content_charset() or "utf-8", errors="replace")
        fields["files"] = files
        return fields

    def _write_headers(self, content_length: Optional[int] = None, content_type: str = "application/json; charset=utf-8") -> None:
        origin = self.headers.get("Origin", "")
        allowed_origins = {
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        }
        self.send_header("Content-Type", content_type)
        self.send_header(
            "Access-Control-Allow-Origin",
            origin if origin in allowed_origins else "http://127.0.0.1:5173",
        )
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        if content_length is not None:
            self.send_header("Content-Length", str(content_length))


def create_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    projects_dir: Optional[str | Path] = None,
    run_inline_jobs: bool = False,
    quiet: bool = False,
) -> ThreadingHTTPServer:
    backend = WorkflowBackend(
        store=ProjectStore(projects_dir),
        run_inline_jobs=run_inline_jobs,
    )

    class Handler(WorkflowRequestHandler):
        pass

    Handler.backend = backend
    server = LocalThreadingHTTPServer((host, int(port)), Handler)
    server.quiet = quiet
    server.backend = backend
    return server


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local MiMo workflow backend API.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--projects-dir", default="")
    parser.add_argument("--inline-jobs", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    server = create_server(
        host=args.host,
        port=args.port,
        projects_dir=args.projects_dir or None,
        run_inline_jobs=args.inline_jobs,
        quiet=args.quiet,
    )
    print(f"MiMo workflow backend listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
