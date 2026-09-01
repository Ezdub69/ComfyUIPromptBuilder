"""Stateless HTTP client for a local OpenAI-compatible LLM server (LM Studio).

Every call is one-shot: system prompt + a single user message, no
conversation history. That statelessness is the whole point of the Krea2 tab
- see krea2_prompt_template.py for why. Stdlib-only (urllib) rather than
adding `requests` as a dependency - this project has exactly one dependency
(PySide6) today.
"""

import codecs
import json
import re
import urllib.request

TIMEOUT_SECONDS = 180
MODEL_LIST_TIMEOUT_SECONDS = 10
DEFAULT_BASE_URL = "http://localhost:1234"  # LM Studio's default local server address

_PROMPT_PREFIX = re.compile(r"^\s*Prompt:\s*", re.IGNORECASE)
_STRAY_TAG = re.compile(r"</?[A-Z_]+>\s*$")


_RELEVANT_MODEL_TYPES = {"llm", "vlm"}


def fetch_models(base_url):
    """Returns [{"id", "loaded", "context_length", "type"}, ...] for every
    chat/vision model the server knows about (embedding models are filtered
    out - never a valid target here). Tries LM Studio's newer native REST
    API first (/api/v1/models, LM Studio 0.4.0+), which reports loaded
    instances (with their actual running context length) and an explicit
    vision capability flag per model - the most reliable source, and the
    same API unload_all_models() needs. Falls back to the older LM Studio
    native API (/api/v0/models, state == "loaded") for pre-0.4.0 servers,
    then to the plain OpenAI-compatible /v1/models list (bare ids, no
    load-state at all) for any other server."""
    try:
        return _fetch_models_lmstudio_v1(base_url)
    except Exception:
        pass
    try:
        return _fetch_models_lmstudio_v0(base_url)
    except Exception:
        return _fetch_models_openai(base_url)


def _fetch_models_lmstudio_v1(base_url):
    url = base_url.rstrip("/") + "/api/v1/models"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=MODEL_LIST_TIMEOUT_SECONDS) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    models = [m for m in data.get("models", []) if m.get("type") == "llm"]
    if not models:
        raise ValueError("no chat/vision models reported")
    result = []
    for m in models:
        instances = m.get("loaded_instances") or []
        loaded = bool(instances)
        context_length = instances[0]["config"]["context_length"] if loaded else m.get("max_context_length")
        is_vision = bool((m.get("capabilities") or {}).get("vision"))
        result.append({
            "id": m["key"],
            "loaded": loaded,
            "context_length": context_length,
            "type": "vlm" if is_vision else "llm",
        })
    return result


def _fetch_models_lmstudio_v0(base_url):
    url = base_url.rstrip("/") + "/api/v0/models"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=MODEL_LIST_TIMEOUT_SECONDS) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    models = [m for m in data.get("data", []) if m.get("type") in _RELEVANT_MODEL_TYPES]
    if not models:
        raise ValueError("no chat/vision models reported")
    return [
        {
            "id": m["id"],
            "loaded": m.get("state") == "loaded",
            "context_length": m.get("loaded_context_length") or m.get("max_context_length"),
            "type": m.get("type"),  # "vlm" or "llm" - lets callers tell vision-capable models apart
        }
        for m in models
    ]


def _fetch_models_openai(base_url):
    url = base_url.rstrip("/") + "/v1/models"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=MODEL_LIST_TIMEOUT_SECONDS) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return [
        {"id": m["id"], "loaded": None, "context_length": None, "type": None}
        for m in data.get("data", [])
    ]


def unload_all_models(base_url):
    """Unloads every currently-loaded model instance via LM Studio's v1 REST
    API. Returns the list of instance ids that were unloaded (empty if
    nothing was loaded). Raises if the server doesn't support the v1 API
    (LM Studio pre-0.4.0) - callers should surface that plainly rather than
    silently doing nothing, since there's no equivalent unload call on the
    older APIs to fall back to."""
    list_url = base_url.rstrip("/") + "/api/v1/models"
    req = urllib.request.Request(list_url)
    with urllib.request.urlopen(req, timeout=MODEL_LIST_TIMEOUT_SECONDS) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    instance_ids = [
        instance["id"]
        for m in data.get("models", [])
        for instance in (m.get("loaded_instances") or [])
    ]
    unload_url = base_url.rstrip("/") + "/api/v1/models/unload"
    for instance_id in instance_ids:
        req = urllib.request.Request(
            unload_url,
            data=json.dumps({"instance_id": instance_id}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            resp.read()
    return instance_ids


def load_model(base_url, model_key):
    """Explicitly loads one model via LM Studio's v1 REST API (the same
    mechanism JIT loading uses internally, just triggered on demand instead
    of by an inference request) - used by the LM Studio tab's "Load
    Assigned Models" button so a user can warm up their assigned model(s)
    right after restarting the app, without waiting for the first real
    message to trigger a JIT load. Raises if the server doesn't support the
    v1 API (LM Studio pre-0.4.0)."""
    url = base_url.rstrip("/") + "/api/v1/models/load"
    req = urllib.request.Request(
        url,
        data=json.dumps({"model": model_key}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        json.loads(resp.read().decode("utf-8"))


def _parse_response(text):
    """krea2_prompt_template.py asks for a bare 'Prompt: ...' with nothing
    else. Strip that prefix, and strip a trailing stray XML-style tag if the
    model echoes one from the system prompt's own tag structure (observed
    occasionally, e.g. a trailing '</NO_CENSORSHIP>')."""
    text = _PROMPT_PREFIX.sub("", text.strip()).strip()
    text = _STRAY_TAG.sub("", text).strip()
    return text


class Aborted(Exception):
    """Raised out of stream_chat_completion when should_abort() returns True."""


def _extract_delta_text(parsed):
    choices = parsed.get("choices") or []
    if not choices:
        return ""
    choice = choices[0]
    delta = choice.get("delta")
    if delta:
        content = delta.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, str):
                    parts.append(part)
                elif isinstance(part, dict) and part.get("type") == "text":
                    parts.append(part.get("text", ""))
            return "".join(parts)
    message = choice.get("message")
    if message and isinstance(message.get("content"), str):
        return message["content"]
    return ""


def stream_chat_completion(base_url, messages, model=None, temperature=0.7, top_p=0.9,
                            max_tokens=1024, presence_penalty=None, top_k=None,
                            on_delta=None, should_abort=None, timeout=90):
    """Streaming chat completion for the Krea 2 Assistant tab: multi-turn
    history, optional multimodal (image) content, incremental text delivered
    via on_delta(chunk) as it arrives - same SSE line protocol LM Studio's
    OpenAI-compatible endpoint uses everywhere else.

    should_abort, if given, is a zero-arg callable checked between chunks so
    a Stop button can interrupt generation - checked once per read, so
    cancellation lands on the next token boundary rather than instantly if
    the model has gone quiet mid-generation.

    Returns the full accumulated text. Raises Aborted if should_abort() ever
    returns True; raises normally (urllib/HTTP errors) for actual failures."""
    url = base_url.rstrip("/") + "/v1/chat/completions"
    payload = {
        "messages": messages,
        "temperature": temperature,
        "top_p": top_p,
        "max_tokens": max_tokens,
        "stream": True,
    }
    if model and model != "Unknown":
        payload["model"] = model
    if presence_penalty:
        payload["presence_penalty"] = presence_penalty
    if top_k:
        payload["top_k"] = top_k

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    full_text = []
    # A multi-byte UTF-8 character (e.g. an em dash) can land split across
    # two separate 4096-byte reads - decoding each raw chunk independently
    # would silently mangle it into a replacement character. An incremental
    # decoder holds onto a dangling partial byte sequence until the rest of
    # it arrives in the next chunk, instead of decoding each read in
    # isolation.
    decoder = codecs.getincrementaldecoder("utf-8")(errors="ignore")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        buffer = ""
        while True:
            if should_abort and should_abort():
                raise Aborted()
            chunk = resp.read(4096)
            if not chunk:
                break
            buffer += decoder.decode(chunk)
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                data_str = line[len("data:"):].strip()
                if data_str == "[DONE]":
                    return "".join(full_text)
                try:
                    parsed = json.loads(data_str)
                except json.JSONDecodeError:
                    continue  # partial JSON chunk - completes next read
                text_chunk = _extract_delta_text(parsed)
                if text_chunk:
                    full_text.append(text_chunk)
                    if on_delta:
                        on_delta(text_chunk)
    return "".join(full_text)


def complete(base_url, model, system_prompt, user_message, max_tokens=1536):
    """One-shot chat completion: system prompt + a single user message, no
    history. Returns the stripped response text. Raises on any
    network/HTTP/parse error - caller is responsible for surfacing it.
    Used both for the full Krea2 assembly call and for krea2_tab.py's
    per-field "Vary" calls - same mechanics, different system prompt."""
    url = base_url.rstrip("/") + "/v1/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.7,
        "max_tokens": max_tokens,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    content = data["choices"][0]["message"]["content"]
    return _parse_response(content)
