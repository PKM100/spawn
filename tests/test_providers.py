import importlib.util
import json
import unittest
import urllib.error
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from test_spawn import model

from spawn.contracts import Call
from spawn.copilot_provider import CopilotProvider, normalize_billing
from spawn.providers import HTTPProvider, NoRedirect, ProviderError, Response


class FakeHTTP:
    def __init__(self, data):
        self.data = json.dumps(data).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, size):
        return self.data[:size]


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.call = Call(("method",), '{"goal":"test"}', {}, 750, 3000, 0, 1, 0, 1)

    def test_copilot_credits_and_legacy_requests_keep_distinct_units(self):
        cost, billing = normalize_billing(
            SimpleNamespace(total_nano_aiu=2_000_000_000, ai_credits_status="complete")
        )
        self.assertAlmostEqual(cost, 0.02)
        self.assertEqual(billing["ai_credits"], 2)
        cost, billing = normalize_billing(
            SimpleNamespace(total_premium_request_cost=0.25)
        )
        self.assertAlmostEqual(cost, 0.01)
        self.assertEqual(billing["premium_requests"], 0.25)

    def test_partial_or_missing_copilot_billing_is_not_free(self):
        for metrics in (
            SimpleNamespace(),
            SimpleNamespace(total_nano_aiu=0, ai_credits_status="partial"),
            SimpleNamespace(total_nano_aiu=0, ai_credits_status="unavailable"),
            SimpleNamespace(total_premium_request_cost=0),
        ):
            self.assertIsNone(normalize_billing(metrics)[0])

    def test_copilot_requires_conservative_per_call_reservation(self):
        with self.assertRaisesRegex(ValueError, "per_call_usd_reserve"):
            model(provider="copilot")
        self.assertEqual(
            model(provider="copilot", per_call_usd_reserve=0.30).provider, "copilot"
        )

    def test_reported_cost_must_be_finite_and_nonnegative(self):
        for cost in (-1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                Response("", 1, 1, reported_cost_usd=cost)

    @unittest.skipUnless(importlib.util.find_spec("copilot"), "Optional Copilot SDK")
    def test_copilot_sessions_are_isolated_tool_free_and_metered(self):
        usage = SimpleNamespace(
            input_tokens=12, output_tokens=7, to_dict=lambda: {"inputTokens": 12}
        )
        metrics = SimpleNamespace(
            total_nano_aiu=1_000_000,
            ai_credits_status="complete",
            model_metrics={"test-model": SimpleNamespace(usage=usage)},
        )
        session = MagicMock()
        session.__aenter__.return_value = session
        session.send_and_wait = AsyncMock(
            return_value=SimpleNamespace(
                data=SimpleNamespace(content='{"findings":[]}')
            )
        )
        session.rpc.usage.get_metrics = AsyncMock(return_value=metrics)
        client = MagicMock()
        client.__aenter__.return_value = client
        client.create_session = AsyncMock(return_value=session)
        client_type = MagicMock(return_value=client)
        with patch(
            "spawn.copilot_provider._sdk", return_value=(client_type, "connection")
        ):
            response = CopilotProvider().complete(
                model(provider="copilot", per_call_usd_reserve=0.30), self.call, 10
            )
        options = client.create_session.call_args.kwargs
        self.assertEqual(options["available_tools"], [])
        self.assertFalse(options["enable_config_discovery"])
        self.assertFalse(options["enable_file_hooks"])
        self.assertFalse(options["enable_skills"])
        self.assertFalse(options["enable_session_store"])
        self.assertEqual(options["infinite_sessions"], {"enabled": False})
        self.assertEqual(
            type(options["on_permission_request"](None, None)).__name__,
            "PermissionDecisionReject",
        )
        self.assertEqual(client_type.call_args.kwargs["mode"], "empty")
        self.assertEqual(client_type.call_args.kwargs["env"]["SPAWN_ACTIVE"], "1")
        self.assertAlmostEqual(response.reported_cost_usd, 0.00001)
        self.assertEqual(response.input_tokens, 12)

    def test_openai_request_uses_configured_limit_role_and_bearer_auth(self):
        profile = model(
            api_key_env="SPAWN_TEST_KEY",
            output_limit_field="max_tokens",
            system_role="developer",
        )
        data = {
            "choices": [
                {"message": {"content": '{"findings":[]}'}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 40, "completion_tokens": 10},
        }
        with (
            patch.dict("os.environ", {"SPAWN_TEST_KEY": "test-key-not-a-secret"}),
            patch(
                "urllib.request.OpenerDirector.open", return_value=FakeHTTP(data)
            ) as opened,
        ):
            response = HTTPProvider().complete(profile, self.call, 7)
        request = opened.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(
            request.full_url, "https://example.invalid/v1/chat/completions"
        )
        self.assertEqual(
            request.get_header("Authorization"), "Bearer test-key-not-a-secret"
        )
        self.assertEqual(payload["max_tokens"], 750)
        self.assertEqual(payload["messages"][0]["role"], "developer")
        self.assertEqual(opened.call_args.kwargs["timeout"], 7)
        self.assertEqual(response.input_tokens, 40)

    def test_anthropic_request_uses_messages_protocol_and_separate_system(self):
        profile = model(provider="anthropic", api_key_env="SPAWN_TEST_KEY")
        data = {
            "content": [{"type": "text", "text": '{"findings":[]}'}],
            "usage": {"input_tokens": 40, "output_tokens": 10},
        }
        with (
            patch.dict("os.environ", {"SPAWN_TEST_KEY": "test-key-not-a-secret"}),
            patch(
                "urllib.request.OpenerDirector.open", return_value=FakeHTTP(data)
            ) as opened,
        ):
            response = HTTPProvider().complete(profile, self.call, 7)
        request = opened.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, "https://example.invalid/v1/messages")
        self.assertEqual(request.get_header("X-api-key"), "test-key-not-a-secret")
        self.assertIn("system", payload)
        self.assertEqual(payload["max_tokens"], 750)
        self.assertEqual(response.output_tokens, 10)

    def test_http_errors_do_not_leak_response_bodies_or_retry(self):
        error = urllib.error.HTTPError(
            "https://example.invalid/v1", 401, "sensitive body", {}, None
        )
        with patch("urllib.request.OpenerDirector.open", side_effect=error) as opened:
            with self.assertRaisesRegex(ProviderError, "HTTP 401") as context:
                HTTPProvider().complete(model(), self.call, 1)
        self.assertNotIn("sensitive body", str(context.exception))
        self.assertEqual(opened.call_count, 1)

    def test_redirect_handler_never_forwards_a_request(self):
        self.assertIsNone(
            NoRedirect().redirect_request(
                None, None, 302, "", {}, "https://other.invalid"
            )
        )

    def test_plain_http_is_only_allowed_on_explicit_loopback(self):
        for endpoint in (
            "http://example.invalid/v1",
            "https://user:pass@example.invalid/v1",
        ):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                replace(model(), base_url=endpoint)
        self.assertEqual(
            replace(model(), base_url="http://127.0.0.1:8080/v1").provider, "openai"
        )

    def test_invalid_provider_payloads_are_normalized(self):
        for data in ([], {"usage": [], "choices": []}, {"choices": []}):
            with self.subTest(data=data):
                with patch(
                    "urllib.request.OpenerDirector.open", return_value=FakeHTTP(data)
                ):
                    with self.assertRaises(ProviderError):
                        HTTPProvider().complete(model(), self.call, 1)


if __name__ == "__main__":
    unittest.main()
