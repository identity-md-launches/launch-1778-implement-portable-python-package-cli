import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from adseg_auto.input import Frame
from adseg_auto.providers import HTTPConfig, HTTPProvider, ProviderError
from adseg_auto.util import file_hash


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.responses, self.requests = [], []
        responses, requests = self.responses, self.requests
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                status, body, headers = responses.pop(0)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *args):
                pass
        self.server = ThreadingHTTPServer(("127.0.0.1",0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.endpoint = "http://127.0.0.1:%d/infer" % self.server.server_port
        self.secret = "local-http-unit-test-redaction-marker"
        self.env_patch = patch.dict("os.environ", {"ADSEG_UNITTEST_CREDENTIAL":self.secret})
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def config(self, **kwargs):
        data = dict(endpoint=self.endpoint, model="local-fixture", credential_env="ADSEG_UNITTEST_CREDENTIAL",
                    max_calls=3, max_cost=3, max_request_cost=1, allow_insecure_localhost=True)
        data.update(kwargs)
        return HTTPConfig(**data)

    def enqueue(self, status, data, **headers):
        self.responses.append((status, json.dumps(data).encode() if not isinstance(data,bytes) else data, headers))

    def answer(self, cost=None):
        data = {"choices":[{"message":{"content":json.dumps({"decision":"unknown","reason":self.secret,"evidence_frame_ids":[]})}}]}
        if cost is not None:
            data["usage"] = {"cost":cost}
        return data

    def test_429_retry_receipts_unknown_cost_and_redaction(self):
        self.enqueue(429, {"error":self.secret})
        self.enqueue(200, self.answer(0.2))
        provider = HTTPProvider(self.config(max_calls=2))
        response = provider.judge({"synthetic":True}, [])
        self.assertEqual(response["reason"], "[REDACTED]")
        self.assertEqual([r["status"] for r in provider.receipts], [429,200])
        self.assertEqual(provider.unknown_cost_attempts, 1)
        self.assertAlmostEqual(provider.charged_or_reserved,1.2)
        self.assertNotIn(self.secret,json.dumps(provider.receipts))
        with self.assertRaisesRegex(ProviderError,"HTTP_BUDGET_EXHAUSTED"):
            provider.judge({"synthetic":True}, [])
        self.assertEqual(len(self.requests),2)

    def test_malformed_response_is_retained_without_secret_body(self):
        self.enqueue(200, ("not JSON " + self.secret).encode())
        provider = HTTPProvider(self.config())
        with self.assertRaisesRegex(ProviderError,"HTTP_MALFORMED_RESPONSE"):
            provider.judge({},[])
        self.assertEqual(provider.unknown_cost_attempts,1)
        self.assertTrue(provider.receipts[0]["response_sha256"])
        self.assertNotIn(self.secret,json.dumps(provider.receipts))

    def test_budget_reserves_unknown_cost_and_bounds_retries(self):
        self.enqueue(503,{"error":"synthetic overload"})
        provider = HTTPProvider(self.config(max_cost=1,max_retries=3))
        with self.assertRaisesRegex(ProviderError,"HTTP_BUDGET_EXHAUSTED"):
            provider.judge({},[])
        self.assertEqual(provider.calls,1)
        self.assertEqual(provider.charged_or_reserved,1)
        self.assertEqual(provider.receipts[0]["status"],503)

    def test_multimodal_payload_and_json_wire_format(self):
        self.enqueue(200,{"response":{"decision":"unknown","reason":"synthetic","evidence_frame_ids":[]}})
        provider = HTTPProvider(self.config(wire_format="json"))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "test.ppm"
            path.write_bytes(b"P6\n2 2\n255\n" + bytes((40,50,60))*4)
            frame = Frame("f",0,0,path,file_hash(path))
            provider.judge({"synthetic":True},[frame])
        payload = self.requests[0]
        urls = [r["image_url"]["url"] for r in payload["multimodal_content"] if r["type"] == "image_url"]
        self.assertEqual(len(urls),1)
        self.assertTrue(urls[0].startswith("data:image/png;base64,"))
        self.assertNotIn(self.secret,json.dumps(payload))

    def test_redirect_does_not_forward_credentials(self):
        self.enqueue(302,{"redirect":"no"},Location=self.endpoint + "/redirect")
        provider = HTTPProvider(self.config())
        with self.assertRaisesRegex(ProviderError,"HTTP_STATUS_302"):
            provider.judge({},[])
        self.assertEqual(len(self.requests),1)

    def test_explicit_live_configuration_is_required(self):
        for kwargs in ({"max_calls":0},{"max_cost":0},{"max_request_cost":0},{"max_retries":4},
                       {"endpoint":"https://user:password@host/infer"},{"endpoint":"https://host/infer?key=secret"},
                       {"allow_insecure_localhost":False}):
            with self.assertRaises(ValueError):
                HTTPProvider(self.config(**kwargs))
        with patch.dict("os.environ", {"ADSEG_UNITTEST_CREDENTIAL":""}):
            with self.assertRaisesRegex(ValueError,"credential environment variable is missing"):
                HTTPProvider(self.config())


if __name__ == "__main__":
    unittest.main()
