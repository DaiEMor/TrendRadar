#!/usr/bin/env python3
"""Local-only TrendRadar report server and persistent configuration API."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import tempfile
import threading
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

import yaml


MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_BACKUPS = 20
FILE_MAP = {
    "config": "config.yaml",
    "frequency": "frequency_words.txt",
    "timeline": "timeline.yaml",
}


def utc_iso(timestamp: float | None = None) -> str:
    value = datetime.fromtimestamp(timestamp, tz=timezone.utc) if timestamp else datetime.now(timezone.utc)
    return value.isoformat(timespec="seconds")


def file_revision(config_dir: Path) -> str:
    digest = hashlib.sha256()
    for key, filename in FILE_MAP.items():
        path = config_dir / filename
        digest.update(key.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def validate_files(files: dict[str, object]) -> dict[str, str]:
    if set(files) != set(FILE_MAP):
        raise ValueError("必须同时提交 config、frequency 和 timeline 三个配置文件")

    validated: dict[str, str] = {}
    for key in FILE_MAP:
        content = files.get(key)
        if not isinstance(content, str):
            raise ValueError(f"{FILE_MAP[key]} 必须是文本")
        if "\x00" in content:
            raise ValueError(f"{FILE_MAP[key]} 包含非法空字节")
        if not content.strip():
            raise ValueError(f"{FILE_MAP[key]} 不能为空")
        validated[key] = content if content.endswith("\n") else content + "\n"

    for key in ("config", "timeline"):
        try:
            document = yaml.safe_load(validated[key])
        except yaml.YAMLError as exc:
            raise ValueError(f"{FILE_MAP[key]} YAML 语法错误：{exc}") from exc
        if not isinstance(document, dict):
            raise ValueError(f"{FILE_MAP[key]} 顶层必须是 YAML 映射")

    config_document = yaml.safe_load(validated["config"])
    required_sections = {"app", "platforms", "rss", "report", "notification", "storage"}
    missing = sorted(required_sections - set(config_document))
    if missing:
        raise ValueError("config.yaml 缺少必要模块：" + ", ".join(missing))

    return validated


def create_backup(config_dir: Path, backup_root: Path) -> Path:
    backup_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = backup_root / f"{stamp}-{secrets.token_hex(3)}"
    backup_dir.mkdir()
    for filename in FILE_MAP.values():
        shutil.copy2(config_dir / filename, backup_dir / filename)

    backups = sorted((path for path in backup_root.iterdir() if path.is_dir()), key=lambda path: path.name)
    for expired in backups[:-MAX_BACKUPS]:
        shutil.rmtree(expired)
    return backup_dir


def atomic_write(path: Path, content: str) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


class ConfigServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], handler: type[SimpleHTTPRequestHandler], project_dir: Path):
        super().__init__(address, handler)
        self.project_dir = project_dir
        self.config_dir = project_dir / "config"
        self.output_dir = project_dir / "output"
        self.docs_dir = project_dir / "docs"
        self.backup_root = project_dir / "config" / "backups"
        self.config_token = secrets.token_urlsafe(32)
        self.config_lock = threading.Lock()


class ConfigRequestHandler(SimpleHTTPRequestHandler):
    server: ConfigServer

    def _json_response(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _allowed_host(self) -> bool:
        host = self.headers.get("Host", "").split(":", 1)[0].strip("[]").lower()
        return host in {"127.0.0.1", "localhost", "::1"}

    def _read_current_files(self) -> dict[str, dict[str, str]]:
        result: dict[str, dict[str, str]] = {}
        for key, filename in FILE_MAP.items():
            path = self.server.config_dir / filename
            result[key] = {
                "name": filename,
                "content": path.read_text(encoding="utf-8"),
                "modified_at": utc_iso(path.stat().st_mtime),
            }
        return result

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/api/config":
            if not self._allowed_host():
                self._json_response(HTTPStatus.FORBIDDEN, {"ok": False, "error": "仅允许本机访问"})
                return
            try:
                with self.server.config_lock:
                    payload = {
                        "ok": True,
                        "files": self._read_current_files(),
                        "revision": file_revision(self.server.config_dir),
                        "token": self.server.config_token,
                    }
                self._json_response(HTTPStatus.OK, payload)
            except OSError as exc:
                self._json_response(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})
            return

        if path == "/config":
            self.send_response(HTTPStatus.PERMANENT_REDIRECT)
            self.send_header("Location", "/config/")
            self.end_headers()
            return

        super().do_GET()

    def do_POST(self) -> None:
        if urlsplit(self.path).path != "/api/config":
            self._json_response(HTTPStatus.NOT_FOUND, {"ok": False, "error": "未知接口"})
            return
        if not self._allowed_host():
            self._json_response(HTTPStatus.FORBIDDEN, {"ok": False, "error": "仅允许本机访问"})
            return
        if self.headers.get("X-Config-Token") != self.server.config_token:
            self._json_response(HTTPStatus.FORBIDDEN, {"ok": False, "error": "页面令牌已失效，请刷新配置页"})
            return
        if not self.headers.get("Content-Type", "").lower().startswith("application/json"):
            self._json_response(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"ok": False, "error": "仅接受 JSON"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = 0
        if content_length <= 0 or content_length > MAX_REQUEST_BYTES:
            self._json_response(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"ok": False, "error": "请求大小不合法"})
            return

        try:
            payload = json.loads(self.rfile.read(content_length))
            if not isinstance(payload, dict):
                raise ValueError("请求体必须是 JSON 对象")
            revision = payload.get("revision")
            files = payload.get("files")
            if not isinstance(revision, str) or not isinstance(files, dict):
                raise ValueError("请求缺少 revision 或 files")
            validated = validate_files(files)
        except (json.JSONDecodeError, ValueError) as exc:
            self._json_response(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            return

        with self.server.config_lock:
            current_revision = file_revision(self.server.config_dir)
            if revision != current_revision:
                self._json_response(
                    HTTPStatus.CONFLICT,
                    {"ok": False, "error": "配置已被其他窗口或程序修改，请刷新页面后再编辑"},
                )
                return

            backup_dir: Path | None = None
            try:
                backup_dir = create_backup(self.server.config_dir, self.server.backup_root)
                for key, filename in FILE_MAP.items():
                    atomic_write(self.server.config_dir / filename, validated[key])
                new_revision = file_revision(self.server.config_dir)
            except OSError as exc:
                if backup_dir:
                    for filename in FILE_MAP.values():
                        backup_file = backup_dir / filename
                        if backup_file.exists():
                            shutil.copy2(backup_file, self.server.config_dir / filename)
                self._json_response(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": f"保存失败：{exc}"})
                return

        self._json_response(
            HTTPStatus.OK,
            {
                "ok": True,
                "saved_at": utc_iso(),
                "revision": new_revision,
                "backup": str(backup_dir.relative_to(self.server.project_dir)),
            },
        )

    def translate_path(self, path: str) -> str:
        request_path = unquote(urlsplit(path).path)
        if request_path.startswith("/config/"):
            root = self.server.docs_dir
            relative = request_path.removeprefix("/config/") or "index.html"
        else:
            root = self.server.output_dir
            relative = request_path.lstrip("/") or "index.html"

        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            return str(root / "__not_found__")
        return str(candidate)

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        if urlsplit(self.path).path in {"/", "/index.html"} or self.path.startswith("/config/"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, format_string: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {self.address_string()} {format_string % args}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve TrendRadar reports and a persistent local configuration editor")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--project-dir", type=Path, default=Path(__file__).resolve().parents[1])
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    project_dir = args.project_dir.expanduser().resolve()
    for required in (project_dir / "config", project_dir / "output", project_dir / "docs"):
        if not required.is_dir():
            raise SystemExit(f"缺少必要目录：{required}")

    server = ConfigServer((args.host, args.port), ConfigRequestHandler, project_dir)
    print(f"TrendRadar local server: http://{args.host}:{args.port}", flush=True)
    print(f"Config editor: http://{args.host}:{args.port}/config/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
