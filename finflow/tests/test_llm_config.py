"""src/llm.py의 Gemini 클라이언트 설정 회귀 테스트 (외부 호출 없음).

google-genai 3.x는 API 버전을 지정하지 않으면 v1을 쓰고,
gemini-flash-latest 별칭은 v1에서 404가 난다. 앱은 GOOGLE_GENAI_API_VERSION이
비어 있으면 v1beta를 쓴다.

HTTP 전송 계층(httpx / httpx2의 HTTPTransport)을 가짜 응답으로 바꿔
실제 요청은 보내지 않는다. 앱 코드는 수정 없이 그대로 실행된다.
"""

from __future__ import annotations

import json
from unittest import mock

import pytest

from src import llm

FAKE_KEY = "test-key-not-real"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("GOOGLE_GENAI_API_VERSION", "GEMINI_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    llm.load_gemini_client.cache_clear()
    yield
    llm.load_gemini_client.cache_clear()


def _transport_modules():
    import httpx

    modules = [httpx]
    try:
        import httpx2
    except ImportError:
        return modules
    return modules + [httpx2]


@pytest.fixture
def fake_gemini(monkeypatch):
    """전송 계층을 가로채 요청을 기록하고, 미리 정한 응답을 순서대로 돌려준다."""
    sent: list = []
    responses: list[tuple[int, dict]] = []

    for module in _transport_modules():
        def handle_request(self, request, _module=module):
            request.read()
            sent.append(request)
            status, body = responses.pop(0)
            return _module.Response(status, json=body, request=request)

        monkeypatch.setattr(module.HTTPTransport, "handle_request", handle_request)

    def run(*queued: tuple[int, dict]):
        responses.extend(queued)
        try:
            return llm.generate_answer("기준금리가 뭐야?"), sent
        except RuntimeError as error:
            return error, sent

    return run


def ok_body(text: str = "확인") -> dict:
    return {
        "candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1, "totalTokenCount": 2},
    }


def error_body(code: int, status: str) -> dict:
    return {"error": {"code": code, "status": status, "message": "mocked"}}


# ---------- 실제 동작: 요청 경로 ----------


def test_default_request_uses_v1beta(fake_gemini):
    answer, sent = fake_gemini((200, ok_body()))

    assert answer == "확인"
    assert len(sent) == 1
    assert sent[0].url.path == "/v1beta/models/gemini-flash-latest:generateContent"


@pytest.mark.parametrize("value", ["", "   "])
def test_empty_env_request_uses_v1beta(monkeypatch, fake_gemini, value):
    monkeypatch.setenv("GOOGLE_GENAI_API_VERSION", value)

    _, sent = fake_gemini((200, ok_body()))

    assert sent[0].url.path.startswith("/v1beta/")


def test_env_override_request_uses_given_version(monkeypatch, fake_gemini):
    monkeypatch.setenv("GOOGLE_GENAI_API_VERSION", "v1")

    _, sent = fake_gemini((200, ok_body()))

    assert sent[0].url.path.startswith("/v1/")


def test_key_sent_in_header_not_url(fake_gemini):
    _, sent = fake_gemini((200, ok_body()))

    assert FAKE_KEY not in str(sent[0].url)
    assert sent[0].headers["x-goog-api-key"] == FAKE_KEY
    assert json.loads(sent[0].content)["contents"][0]["parts"][0]["text"] == "기준금리가 뭐야?"


# ---------- 실제 동작: 재시도는 기존(SDK 기본)과 같아야 함 ----------


@pytest.mark.parametrize(
    ("code", "status"),
    [(503, "UNAVAILABLE"), (429, "RESOURCE_EXHAUSTED"), (402, "RESOURCE_EXHAUSTED"), (404, "NOT_FOUND")],
)
def test_error_sent_once_without_retry(fake_gemini, code, status):
    error, sent = fake_gemini(*[(code, error_body(code, status))] * 5)

    assert isinstance(error, RuntimeError)
    assert len(sent) == 1


# ---------- 설정 함수 ----------


def test_api_version_defaults():
    assert llm.get_api_version() == "v1beta"
    options = llm.build_http_options()
    assert options.api_version == "v1beta"
    assert options.retry_options is None


def test_client_receives_api_version(monkeypatch):
    monkeypatch.setenv("GOOGLE_GENAI_API_VERSION", "v1alpha")

    with mock.patch.object(llm.genai, "Client") as client_cls:
        llm.load_gemini_client()

    kwargs = client_cls.call_args.kwargs
    assert kwargs["api_key"] == FAKE_KEY
    assert kwargs["http_options"].api_version == "v1alpha"
