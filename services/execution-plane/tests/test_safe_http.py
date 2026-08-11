import pathlib
import sys
import threading
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.safe_http import HttpPolicy, SafeHttpExecutor, UnsafeHttpRequest


def public_resolver(host, port, type=None):
    return [(2, 1, 6, "", ("93.184.216.34", port))]


class FakeStream:
    def get_extra_info(self, name):
        return ("93.184.216.34", 443) if name == "server_addr" else None


class FakeResponse:
    status_code = 200
    content = b'{"ok":true}'
    encoding = "utf-8"
    headers = {"content-type": "application/json"}
    extensions = {"network_stream": FakeStream()}
    def iter_bytes(self):
        yield self.content
    def close(self):
        pass


class FakeClient:
    def __init__(self, timeout, follow_redirects):
        self.closed = False
    def build_request(self, *args, **kwargs):
        self.request_args = (args, kwargs)
        return object()
    def send(self, request, stream):
        return FakeResponse()
    def close(self):
        self.closed = True


class SafeHttpTest(unittest.TestCase):
    def test_default_policy_denies_every_host(self):
        executor = SafeHttpExecutor(policy=HttpPolicy(allowed_hosts=()), resolver=public_resolver, client_factory=FakeClient)
        with self.assertRaisesRegex(UnsafeHttpRequest, "not allowlisted"):
            executor.execute({"url": "https://example.com"})

    def test_allowlisted_public_host_is_checked_before_and_after_request(self):
        calls = []
        def resolver(*args, **kwargs):
            calls.append(args[0])
            return public_resolver(*args, **kwargs)
        executor = SafeHttpExecutor(
            policy=HttpPolicy(allowed_hosts=("example.com",)), resolver=resolver, client_factory=FakeClient
        )
        result = executor.execute({"url": "https://example.com/data", "headers": {"Accept": "application/json"}})
        self.assertEqual({"ok": True}, result["body"])
        self.assertEqual(2, calls.count("example.com"))

    def test_private_dns_result_is_rejected_even_when_host_is_allowlisted(self):
        resolver = lambda host, port, type=None: [(2, 1, 6, "", ("127.0.0.1", port))]
        executor = SafeHttpExecutor(policy=HttpPolicy(allowed_hosts=("internal.test",)), resolver=resolver)
        with self.assertRaisesRegex(UnsafeHttpRequest, "not public"):
            executor.execute({"url": "https://internal.test"})

    def test_credentials_redirects_and_sensitive_headers_are_rejected(self):
        executor = SafeHttpExecutor(
            policy=HttpPolicy(allowed_hosts=("example.com",)), resolver=public_resolver, client_factory=FakeClient
        )
        for request in [
            {"url": "https://user:pass@example.com"},
            {"url": "https://example.com", "headers": {"Authorization": "secret"}},
        ]:
            with self.assertRaises(UnsafeHttpRequest):
                executor.execute(request)

    def test_response_body_limit_is_enforced_while_streaming(self):
        class LargeResponse(FakeResponse):
            headers = {"content-type": "text/plain"}
            def iter_bytes(self):
                yield b"1234"
                yield b"5678"
        class LargeClient(FakeClient):
            def send(self, request, stream): return LargeResponse()
        executor = SafeHttpExecutor(
            policy=HttpPolicy(allowed_hosts=("example.com",), max_response_bytes=6),
            resolver=public_resolver,
            client_factory=LargeClient,
        )
        with self.assertRaisesRegex(UnsafeHttpRequest, "size limit"):
            executor.execute({"url": "https://example.com"})

    def test_blocking_request_is_actively_closed_on_cancellation(self):
        closed = threading.Event()
        cancel = threading.Event()
        class BlockingClient(FakeClient):
            def send(self, request, stream):
                closed.wait(2)
                raise RuntimeError("closed")
            def close(self):
                closed.set()
        executor = SafeHttpExecutor(
            policy=HttpPolicy(allowed_hosts=("example.com",)), resolver=public_resolver, client_factory=BlockingClient
        )
        timer = threading.Timer(0.05, cancel.set)
        timer.start()
        started = time.monotonic()
        try:
            with self.assertRaisesRegex(RuntimeError, "TASK_CANCELLED"):
                executor.execute({"url": "https://example.com"}, cancel.is_set)
        finally:
            timer.cancel()
        self.assertTrue(closed.is_set())
        self.assertLess(time.monotonic() - started, 0.6)

    def test_sensitive_header_is_allowed_only_when_marked_as_trusted(self):
        executor = SafeHttpExecutor(
            policy=HttpPolicy(allowed_hosts=("example.com",)), resolver=public_resolver, client_factory=FakeClient
        )
        result = executor.execute({
            "url": "https://example.com",
            "headers": {"Authorization": "Bearer secret"},
            "trusted_header_names": ["Authorization"],
        })
        self.assertEqual(200, result["status_code"])

    def test_non_success_status_fails_without_exposing_response_body(self):
        class UnauthorizedResponse(FakeResponse):
            status_code = 401
            content = b'{"token":"must-not-escape"}'
        class UnauthorizedClient(FakeClient):
            def send(self, request, stream): return UnauthorizedResponse()
        executor = SafeHttpExecutor(
            policy=HttpPolicy(allowed_hosts=("example.com",)), resolver=public_resolver, client_factory=UnauthorizedClient
        )
        with self.assertRaisesRegex(UnsafeHttpRequest, "non-success status: 401") as failure:
            executor.execute({"url": "https://example.com"})
        self.assertNotIn("must-not-escape", str(failure.exception))

    def test_only_transient_http_status_is_marked_retryable(self):
        class StatusClient(FakeClient):
            status_code = 503
            def send(self, request, stream):
                response = FakeResponse()
                response.status_code = self.status_code
                return response
        executor = SafeHttpExecutor(
            policy=HttpPolicy(allowed_hosts=("example.com",)), resolver=public_resolver, client_factory=StatusClient
        )
        with self.assertRaises(UnsafeHttpRequest) as transient:
            executor.execute({"url": "https://example.com"})
        self.assertTrue(transient.exception.retryable)
        self.assertEqual(503, transient.exception.status_code)

        StatusClient.status_code = 401
        with self.assertRaises(UnsafeHttpRequest) as permanent:
            executor.execute({"url": "https://example.com"})
        self.assertFalse(permanent.exception.retryable)


if __name__ == "__main__":
    unittest.main()
