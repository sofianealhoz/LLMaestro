import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import TINY_PNG, spec

from llmaestro.errors import (
    AuthError,
    BadResponse,
    ContextTooLarge,
    ProviderError,
    ProviderUnavailable,
    RateLimited,
)
from llmaestro.messages import user
from llmaestro.providers.ollama import Ollama
from llmaestro.providers.openai_compat import OpenAICompatible
from llmaestro.transport import Response


def error_body(message: str) -> str:
    return json.dumps({"error": {"message": message, "type": "invalid_request_error"}})


def image_file() -> str:
    path = Path(tempfile.mkdtemp()) / "pixel.png"
    path.write_bytes(TINY_PNG)
    return str(path)


class StatusMapping(unittest.TestCase):
    def raises(self, status, body="", headers=None):
        from llmaestro.providers.base import raise_for_status

        with self.assertRaises(ProviderError) as caught:
            raise_for_status("p", Response(status, body, headers or {}))
        return caught.exception

    def test_a_success_raises_nothing(self):
        from llmaestro.providers.base import raise_for_status

        self.assertIsNone(raise_for_status("p", Response(200, "{}")))

    def test_429_becomes_rate_limited_and_carries_retry_after(self):
        error = self.raises(429, error_body("slow down"), {"retry-after": "12"})

        self.assertIsInstance(error, RateLimited)
        self.assertEqual(12.0, error.retry_after)
        self.assertTrue(error.retryable)
        self.assertIn("slow down", str(error))

    def test_a_missing_retry_after_is_simply_unknown(self):
        self.assertIsNone(self.raises(429, error_body("slow down")).retry_after)

    def test_401_and_403_become_auth_errors_that_disable_the_provider(self):
        for status in (401, 403):
            error = self.raises(status, error_body("bad key"))
            self.assertIsInstance(error, AuthError)
            self.assertFalse(error.retryable)
            self.assertEqual(3600.0, error.disable_for)

    def test_a_context_complaint_becomes_context_too_large(self):
        error = self.raises(400, error_body("This model's maximum context length is 8192 tokens"))

        self.assertIsInstance(error, ContextTooLarge)
        self.assertFalse(error.retryable, "retrying the same prompt cannot help")
        self.assertEqual(0.0, error.disable_for, "the provider itself is healthy")

    def test_413_is_a_context_problem_too(self):
        self.assertIsInstance(self.raises(413, "too big"), ContextTooLarge)

    def test_a_plain_400_stays_a_plain_error(self):
        error = self.raises(400, error_body("unknown parameter"))

        self.assertIs(type(error), ProviderError)
        self.assertFalse(error.retryable)

    def test_server_errors_are_worth_retrying(self):
        for status in (500, 502, 503):
            error = self.raises(status, "gateway blew up")
            self.assertIsInstance(error, ProviderUnavailable)
            self.assertTrue(error.retryable)

    def test_a_body_that_is_not_json_still_yields_a_message(self):
        self.assertIn("gateway blew up", str(self.raises(503, "gateway blew up")))


class OpenAIShape(unittest.TestCase):
    def setUp(self):
        self.provider = OpenAICompatible(
            spec("groq", kind="openai_compat", api_key="secret", model="small")
        )

    def call(self, response):
        with mock.patch(
            "llmaestro.providers.openai_compat.post_json", return_value=response
        ) as sent:
            completion = self.provider.complete([user("hello")])
        return completion, sent

    def test_a_normal_answer_is_parsed_with_its_usage(self):
        body = json.dumps(
            {
                "model": "small-actual",
                "choices": [{"message": {"content": "bonjour"}}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 3},
            }
        )

        completion, sent = self.call(Response(200, body))

        self.assertEqual("bonjour", completion.text)
        self.assertEqual("groq", completion.provider)
        self.assertEqual("small-actual", completion.model)
        self.assertEqual(14, completion.tokens)
        url = sent.call_args.args[0]
        self.assertTrue(url.endswith("/chat/completions"), url)

    def test_a_payload_missing_the_answer_is_a_bad_response(self):
        with self.assertRaises(BadResponse):
            self.call(Response(200, json.dumps({"choices": []})))

    def test_a_refusal_is_translated(self):
        with self.assertRaises(RateLimited):
            self.call(Response(429, error_body("slow down")))

    def test_the_key_travels_as_a_bearer_token(self):
        _, sent = self.call(
            Response(200, json.dumps({"choices": [{"message": {"content": "ok"}}]}))
        )

        headers = sent.call_args.args[2]
        self.assertEqual("Bearer secret", headers["authorization"])

    def test_the_served_models_can_be_listed(self):
        body = json.dumps({"data": [{"id": "zai-glm-4.7"}, {"id": "gpt-oss-120b"}]})

        with mock.patch(
            "llmaestro.providers.openai_compat.get", return_value=Response(200, body)
        ):
            self.assertEqual(["gpt-oss-120b", "zai-glm-4.7"], self.provider.models())

    def test_listing_models_gives_up_quietly(self):
        with mock.patch(
            "llmaestro.providers.openai_compat.get", return_value=Response(403, "blocked")
        ):
            self.assertIsNone(self.provider.models())

        with mock.patch(
            "llmaestro.providers.openai_compat.get",
            side_effect=ProviderUnavailable("groq", "offline"),
        ):
            self.assertIsNone(self.provider.models())

    def test_an_image_becomes_a_data_uri_block(self):
        wire = self.provider._wire(user("what is this", [image_file()]))

        self.assertEqual("user", wire["role"])
        kinds = [block["type"] for block in wire["content"]]
        self.assertEqual(["text", "image_url"], kinds)
        self.assertTrue(
            wire["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
        )

    def test_a_text_only_message_stays_a_plain_string(self):
        self.assertEqual(
            {"role": "user", "content": "hello"}, self.provider._wire(user("hello"))
        )


class OllamaShape(unittest.TestCase):
    def setUp(self):
        self.provider = Ollama(spec("ollama", kind="ollama", base_url="http://localhost:11434"))

    def test_a_local_answer_is_parsed(self):
        body = json.dumps(
            {
                "model": "qwen2.5-coder:7b",
                "message": {"role": "assistant", "content": "bonjour"},
                "prompt_eval_count": 9,
                "eval_count": 5,
            }
        )

        with mock.patch(
            "llmaestro.providers.ollama.post_json", return_value=Response(200, body)
        ) as sent:
            completion = self.provider.complete([user("hello")])

        self.assertEqual("bonjour", completion.text)
        self.assertEqual(14, completion.tokens)
        self.assertTrue(sent.call_args.args[0].endswith("/api/chat"))

    def test_images_travel_as_bare_base64(self):
        wire = self.provider._wire(user("what is this", [image_file()]))

        self.assertEqual("what is this", wire["content"])
        self.assertEqual(1, len(wire["images"]))
        self.assertFalse(wire["images"][0].startswith("data:"), "Ollama wants raw base64")

    def test_an_unreachable_daemon_is_reported_not_raised(self):
        with mock.patch(
            "llmaestro.providers.ollama.get",
            side_effect=ProviderUnavailable("ollama", "connection refused"),
        ):
            self.assertFalse(self.provider.available())


if __name__ == "__main__":
    unittest.main()
