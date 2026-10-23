from __future__ import annotations

import unittest

import httpx2
from openai import BadRequestError, NotFoundError

from agent_runtime.models.errors import (
    classify_provider_error,
    provider_error_message,
)


class ProviderErrorClassificationTests(unittest.TestCase):
    def test_missing_model_or_endpoint_has_actionable_guidance(self):
        response = httpx2.Response(404,request=httpx2.Request('POST','https://example.test/v1/chat/completions'))
        error = NotFoundError('model_not_found',response=response,body={'error':{'code':'model_not_found'}})
        self.assertEqual(classify_provider_error(error),'model_not_found')
        self.assertIn('模型标识',provider_error_message(error))
        self.assertIn('地址',provider_error_message(error))

    def test_context_overflow_has_a_specific_code_and_actionable_message(self) -> None:
        error = _bad_request(
            message="This model's maximum context length is 64000 tokens.",
            code="context_length_exceeded",
        )

        self.assertEqual(classify_provider_error(error), "context_length_exceeded")
        self.assertIn("上下文", provider_error_message(error))
        self.assertIn("清理上下文", provider_error_message(error))

    def test_other_bad_requests_do_not_claim_a_generic_runtime_failure(self) -> None:
        error = _bad_request(message="Unsupported request field.", code="invalid_argument")

        self.assertEqual(classify_provider_error(error), "invalid_request")
        self.assertIn("请求参数", provider_error_message(error))


def _bad_request(*, message: str, code: str) -> BadRequestError:
    body = {"error": {"message": message, "code": code}}
    response = httpx2.Response(
        400,
        request=httpx2.Request("POST", "https://example.test/v1/chat/completions"),
        json=body,
    )
    return BadRequestError(message, response=response, body=body)


if __name__ == "__main__":
    unittest.main()
