from __future__ import annotations

import ipaddress
import json
import os
import socket
import threading
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit

import httpx


class UnsafeHttpRequest(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False, status_code: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


@dataclass(frozen=True)
class HttpPolicy:
    allowed_hosts: tuple[str, ...]
    allow_http: bool = False
    allow_private_networks: bool = False
    max_response_bytes: int = 1_048_576
    max_timeout_seconds: float = 30.0

    @classmethod
    def from_env(cls) -> "HttpPolicy":
        hosts = tuple(
            item.strip().lower().rstrip(".")
            for item in os.getenv("WORKFLOW_HTTP_ALLOWED_HOSTS", "").split(",")
            if item.strip()
        )
        return cls(
            allowed_hosts=hosts,
            allow_http=_env_bool("WORKFLOW_HTTP_ALLOW_HTTP"),
            allow_private_networks=_env_bool("WORKFLOW_HTTP_ALLOW_PRIVATE_NETWORKS"),
            max_response_bytes=max(1024, min(int(os.getenv("WORKFLOW_HTTP_MAX_RESPONSE_BYTES", "1048576")), 5_242_880)),
            max_timeout_seconds=max(1.0, min(float(os.getenv("WORKFLOW_HTTP_MAX_TIMEOUT_SECONDS", "30")), 60.0)),
        )


class SafeHttpExecutor:
    SAFE_HEADERS = {"accept", "content-type", "user-agent", "x-request-id", "x-correlation-id", "idempotency-key"}
    METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}

    def __init__(
        self,
        policy: HttpPolicy | None = None,
        resolver: Callable[..., list] | None = None,
        client_factory=None,
    ) -> None:
        self.policy = policy or HttpPolicy.from_env()
        self.resolver = resolver or socket.getaddrinfo
        self.client_factory = client_factory or httpx.Client

    def execute(self, request: dict, should_cancel: Callable[[], bool] | None = None) -> dict:
        method = str(request.get("method") or "GET").upper()
        if method not in self.METHODS:
            raise UnsafeHttpRequest(f"HTTP method is not allowed: {method}")
        url = str(request.get("url") or "").strip()
        parsed = self._validate_url(url)
        self._validate_resolved_host(parsed.hostname or "", parsed.port)
        trusted_headers = {
            str(name).strip().lower() for name in (request.get("trusted_header_names") or []) if str(name).strip()
        }
        headers = self._validate_headers(request.get("headers") or {}, trusted_headers)
        timeout = max(1.0, min(float(request.get("timeout_seconds") or 10.0), self.policy.max_timeout_seconds))
        self._raise_if_cancelled(should_cancel)

        client = self.client_factory(timeout=timeout, follow_redirects=False)
        stopped = threading.Event()

        def watch_cancel() -> None:
            while not stopped.wait(0.05):
                if should_cancel and should_cancel():
                    client.close()
                    return

        watcher = threading.Thread(target=watch_cancel, daemon=True)
        watcher.start()
        try:
            outbound = client.build_request(
                method,
                url,
                headers=headers,
                params=request.get("query") or None,
                json=request.get("body") if "body" in request else None,
            )
            response = client.send(outbound, stream=True)
            self._raise_if_cancelled(should_cancel)
            if 300 <= response.status_code < 400:
                raise UnsafeHttpRequest("HTTP redirects are disabled for workflow tools")
            self._validate_resolved_host(parsed.hostname or "", parsed.port)
            self._validate_peer(response)
            try:
                declared_length = int(response.headers.get("content-length") or 0)
            except (TypeError, ValueError) as exc:
                raise UnsafeHttpRequest("HTTP response contains an invalid content length") from exc
            if declared_length > self.policy.max_response_bytes:
                raise UnsafeHttpRequest("HTTP response exceeds the configured size limit")
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_bytes():
                self._raise_if_cancelled(should_cancel)
                size += len(chunk)
                if size > self.policy.max_response_bytes:
                    raise UnsafeHttpRequest("HTTP response exceeds the configured size limit")
                chunks.append(chunk)
            raw = b"".join(chunks)
            content_type = str(response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
            text = raw.decode(response.encoding or "utf-8", errors="replace")
            if content_type == "application/json" or content_type.endswith("+json"):
                try:
                    body = json.loads(text)
                except json.JSONDecodeError:
                    body = text
            else:
                body = text
            if not 200 <= response.status_code < 300:
                status_code = int(response.status_code)
                raise UnsafeHttpRequest(
                    f"HTTP tool received non-success status: {status_code}",
                    retryable=status_code in {408, 425, 429, 500, 502, 503, 504},
                    status_code=status_code,
                )
            return {
                "status_code": int(response.status_code),
                "content_type": content_type,
                "body": body,
                "url": _redacted_url(url),
                "mcp_session_id": str(response.headers.get("mcp-session-id") or ""),
            }
        except Exception:
            self._raise_if_cancelled(should_cancel)
            raise
        finally:
            stopped.set()
            if "response" in locals():
                response.close()
            client.close()
            watcher.join(timeout=0.2)

    def _validate_url(self, url: str):
        parsed = urlsplit(url)
        if parsed.scheme not in ({"https", "http"} if self.policy.allow_http else {"https"}):
            raise UnsafeHttpRequest("only explicitly permitted HTTP(S) URLs are allowed")
        if not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.fragment:
            raise UnsafeHttpRequest("URL must have a host and cannot contain credentials or a fragment")
        host = parsed.hostname.lower().rstrip(".")
        if not any(_host_matches(host, pattern) for pattern in self.policy.allowed_hosts):
            raise UnsafeHttpRequest(f"HTTP host is not allowlisted: {host}")
        return parsed

    def _validate_resolved_host(self, host: str, port: int | None) -> None:
        try:
            records = self.resolver(host, port or 443, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise UnsafeHttpRequest("HTTP host could not be resolved") from exc
        addresses = {record[4][0] for record in records if record and len(record) > 4 and record[4]}
        if not addresses:
            raise UnsafeHttpRequest("HTTP host resolved without an address")
        for address in addresses:
            self._validate_ip(address)

    def _validate_peer(self, response) -> None:
        stream = response.extensions.get("network_stream") if hasattr(response, "extensions") else None
        peer = stream.get_extra_info("server_addr") if stream is not None else None
        if peer is None and stream is not None:
            peer = stream.get_extra_info("peername")
        if peer:
            address = peer[0] if isinstance(peer, (tuple, list)) else str(peer)
            self._validate_ip(address)

    def _validate_ip(self, address: str) -> None:
        try:
            ip = ipaddress.ip_address(str(address).split("%", 1)[0])
        except ValueError as exc:
            raise UnsafeHttpRequest("HTTP host resolved to an invalid IP address") from exc
        unsafe = not ip.is_global
        if unsafe and not self.policy.allow_private_networks:
            raise UnsafeHttpRequest(f"HTTP destination address is not public: {ip}")

    def _validate_headers(self, headers, trusted_headers: set[str] | None = None) -> dict[str, str]:
        if not isinstance(headers, dict):
            raise UnsafeHttpRequest("HTTP headers must be an object")
        cleaned: dict[str, str] = {}
        for raw_name, raw_value in headers.items():
            name = str(raw_name).strip()
            normalized = name.lower()
            if normalized not in self.SAFE_HEADERS and normalized not in (trusted_headers or set()):
                raise UnsafeHttpRequest(f"HTTP header is not allowed: {name}")
            value = str(raw_value)
            if "\r" in value or "\n" in value:
                raise UnsafeHttpRequest("HTTP header values cannot contain line breaks")
            cleaned[name] = value
        return cleaned

    @staticmethod
    def _raise_if_cancelled(should_cancel) -> None:
        if should_cancel and should_cancel():
            raise RuntimeError("TASK_CANCELLED")


def _host_matches(host: str, pattern: str) -> bool:
    if pattern.startswith("*."):
        suffix = pattern[1:]
        return host.endswith(suffix) and host != suffix[1:]
    return host == pattern


def _redacted_url(url: str) -> str:
    parsed = urlsplit(url)
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{parsed.hostname or ''}{port}{parsed.path}"


def _env_bool(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}
