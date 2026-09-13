"""HTTP client for Yandex Cloud's "Responses"-style completion endpoint
(https://ai.api.cloud.yandex.net/v1/responses), which mirrors OpenAI's
Responses API request/response shape (instructions/input/max_output_tokens
in, an "output" list or "output_text" convenience field out).

This was built and initially tested in a network-sandboxed environment
where outbound access to ai.api.cloud.yandex.net is blocked by policy, so
the request/response handling could only be tested against mocks there.
Confirmed working end-to-end against the real endpoint on the user's own
machine afterward (see docs/REVERSE_ENGINEERING.md §16) -- the one
failure hit along the way (a TLS handshake timeout) turned out to be a
VPN/corporate proxy stalling the connection to this specific host, not a
bug in the request/response handling here; see the URLError branch below
for the resulting hint.

Uses only the standard library (urllib) -- no new dependency for one
optional feature.
"""
from __future__ import annotations

import contextlib
import json
import urllib.error
import urllib.request

ENDPOINT = "https://ai.api.cloud.yandex.net/v1/responses"
DEFAULT_TIMEOUT_S = 45


class AIRequestError(RuntimeError):
    """Network/HTTP failure calling the AI endpoint -- message is already
    human-readable Russian, safe to show directly in the UI/report."""


class AIResponseError(RuntimeError):
    """The call succeeded but the response body didn't match any known
    shape -- message includes the top-level JSON keys seen, for
    diagnosability, but never the full body (could be large or, in a
    future response shape, carry something not meant for display)."""


def request_completion(
    *,
    api_key: str,
    folder_id: str,
    model: str,
    instructions: str,
    input_text: str,
    temperature: float = 0.3,
    max_output_tokens: int = 1500,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> str:
    """Returns the model's output text, or raises AIRequestError /
    AIResponseError with a Russian message safe to surface to the user."""
    body = json.dumps(
        {
            "model": model,
            "instructions": instructions,
            "input": input_text,
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        ENDPOINT,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Api-Key {api_key}",
            "OpenAI-Project": folder_id,
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        detail = ""
        with contextlib.suppress(Exception):  # best-effort only, never let this mask the real error
            detail = e.read().decode("utf-8", errors="replace")[:500]
        raise AIRequestError(f"сервис вернул ошибку HTTP {e.code}{': ' + detail if detail else ''}") from e
    except urllib.error.URLError as e:
        hint = ""
        if "handshake" in str(e.reason).lower() or "timed out" in str(e.reason).lower():
            # Confirmed in the field: a VPN/corporate proxy stalling the
            # TLS handshake to this specific host is the most common cause
            # of exactly this error (TCP connects, but the TLS negotiation
            # itself never completes) -- point at that first rather than
            # a generic "network problem".
            hint = " — если включён VPN или корпоративный прокси, попробуйте отключить его или проверить, не блокирует ли он ai.api.cloud.yandex.net"
        raise AIRequestError(f"не удалось подключиться к сервису ИИ ({e.reason}){hint}") from e
    except TimeoutError as e:
        raise AIRequestError(f"сервис ИИ не ответил за {timeout_s} сек.") from e

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise AIResponseError("сервис вернул ответ, который не удалось разобрать как JSON") from e

    return _extract_text(data)


def _extract_text(data: dict) -> str:
    """Defensive parsing across a few known/likely response shapes -- see
    the module docstring for why this can't be pinned down to exactly one
    without a live test."""
    # 1) OpenAI Responses API convenience field, if this endpoint sets it.
    text = data.get("output_text")
    if isinstance(text, str) and text.strip():
        return text.strip()

    # 2) OpenAI Responses API's actual structure: output -> [ {type:
    #    "message", content: [ {type: "output_text", text: "..."} ] } ]
    output = data.get("output")
    if isinstance(output, list):
        chunks = []
        for item in output:
            if not isinstance(item, dict):
                continue
            for part in item.get("content", []) or []:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    chunks.append(part["text"])
        if chunks:
            return "\n\n".join(c.strip() for c in chunks if c.strip())

    # 3) Classic chat-completions shape, in case this endpoint diverges
    #    from the Responses API despite its URL.
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            return message["content"].strip()

    raise AIResponseError(f"неожиданный формат ответа сервиса (поля верхнего уровня: {sorted(data.keys())})")
