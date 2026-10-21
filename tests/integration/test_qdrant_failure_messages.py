"""What an operator reads when Qdrant refuses a request, does not answer or answers with an error."""
import httpx
import pytest
from httpx import Headers
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from app.repositories.qdrant.client import describe_failure, describe_request_failure


def unexpected(status: int, content: bytes = b"", reason: str = "Reason") -> UnexpectedResponse:
    return UnexpectedResponse(status, reason, content, Headers())


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_key_says_which_setting_to_check(status):
    message = describe_request_failure(unexpected(status, b"Invalid API key or JWT"))

    assert message == f"Qdrant refused the request (HTTP {status}): check QDRANT_API_KEY"


@pytest.mark.parametrize(
    ("content", "reason", "detail"),
    [
        (b'{"status": {"error": "Service internal\\nerror"}}', "Internal Server Error", "Service internal error"),
        (b"<html>bad gateway</html>", "Bad Gateway", "Bad Gateway"),
        (b'{"status": "ok"}', "Conflict", "Conflict"),
        (b"{}", "Not Found", "Not Found"),
        (b'{"status": {"error": "' + b"x" * 500 + b'"}}', "Bad Request", "x" * 200),
    ],
)
def test_any_other_answer_is_reported_with_the_text_qdrant_gave(content, reason, detail):
    message = describe_request_failure(unexpected(500, content, reason))

    assert message == f"Qdrant answered HTTP 500: {detail}"


def test_a_node_that_does_not_answer_says_what_to_check():
    message = describe_request_failure(ResponseHandlingException(httpx.ConnectError("refused")))

    assert message == "Qdrant cannot be reached or did not answer in time (ConnectError); check QDRANT_URL and that the node runs"


def test_describe_failure_covers_the_errors_of_the_qdrant_client():
    assert describe_failure(unexpected(401)) == "Qdrant refused the request (HTTP 401): check QDRANT_API_KEY"
    assert describe_failure(ResponseHandlingException(httpx.ReadTimeout("slow"))).startswith("Qdrant cannot be reached")


def test_describe_failure_leaves_every_other_error_to_the_caller():
    assert describe_failure(KeyError("bug")) is None


def test_a_plain_http_error_is_not_taken_for_a_qdrant_error():
    """It may come from the gateway, the rerank service or the introspection endpoint: ``service_failures`` names it."""
    assert describe_failure(httpx.ConnectTimeout("slow")) is None
