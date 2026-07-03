from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

from .project_store import ProjectStore
from .workflow_api import WorkflowBackend


RouteResult = Tuple[int, Dict[str, Any]]


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

    def dispatch(self, method: str) -> RouteResult:
        path = urlparse(self.path).path.strip("/")
        parts = [part for part in path.split("/") if part]
        payload = self.read_json_body() if method in {"POST", "PATCH", "DELETE"} else {}

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
            if method == "POST" and subpath == ["reference", "videos"]:
                return HTTPStatus.OK, self.backend.set_reference_videos(project_id, payload)
            if method == "POST" and subpath == ["reference", "text"]:
                return HTTPStatus.OK, self.backend.set_reference_text(project_id, payload)
            if method == "POST" and subpath == ["storyboard", "analyze"]:
                return HTTPStatus.ACCEPTED, self.backend.analyze_storyboard(project_id, payload)
            if method == "POST" and subpath == ["materials", "tag"]:
                return HTTPStatus.ACCEPTED, self.backend.tag_materials(project_id, payload)
            if method == "POST" and subpath == ["voiceovers", "generate"]:
                return HTTPStatus.ACCEPTED, self.backend.generate_voiceovers(project_id, payload)
            if method == "POST" and subpath == ["assembly", "plan"]:
                return HTTPStatus.ACCEPTED, self.backend.build_assembly_plan(project_id, payload)
            if method == "POST" and subpath == ["legacy", "generate"]:
                return HTTPStatus.ACCEPTED, self.backend.legacy_generate(project_id, payload)

        raise KeyError(f"route not found: {method} /{path}")

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

    def _write_headers(self, content_length: Optional[int] = None) -> None:
        origin = self.headers.get("Origin", "")
        allowed_origins = {
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        }
        self.send_header("Content-Type", "application/json; charset=utf-8")
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
    server = ThreadingHTTPServer((host, int(port)), Handler)
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
