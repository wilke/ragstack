"""Bounded, polite client for the shared mango LLM endpoint.

`http://mango.cels.anl.gov:8004/v1/chat/completions`, OpenAI-compatible, no key.

Three empirical facts about this server that the client encodes:

1. The served model is ``Qwen/Qwen3.6-35B-A3B`` (``max_model_len`` 131072). The repo's
   ``docs/model-registry.md`` and ``/rag/config/unified.models.json`` still name
   ``RedHatAI/Llama-4-Scout-17B-16E-Instruct-FP8-dynamic``; sending that id fails. The
   served id is read from ``/v1/models`` at import and asserted, so a silent model swap
   under this harness becomes a loud error rather than a quiet change of generator.
2. **It is a reasoning model, and the reasoning tokens are not surfaced.** They are billed
   in ``completion_tokens`` but appear in neither ``content`` nor ``reasoning_content``.
   A two-word answer costs ~150 tokens; a summary of a 1,500-token passage cost 3,085.
   So an empty ``content`` with ``finish_reason == "length"`` means *budget exhausted*,
   not refusal — :func:`chat` retries such a call once at double the budget before
   giving up, and the retry is counted.
3. Thinking can be switched off with ``chat_template_kwargs={"enable_thinking": false}``,
   which took the same paraphrase from 16.3 s / 3,085 tokens to 0.4 s / 67 tokens. It is
   off for the mechanical stage (paraphrase) and on for the two judgement stages.

Politeness: mango is shared. ``MAX_INFLIGHT`` caps concurrent requests; every failure
backs off rather than retrying immediately. Tokens and elapsed are accumulated so the
run can report its own cost.
"""
from __future__ import annotations

import json
import queue
import threading
import time
import urllib.error
import urllib.request

BASE = "http://mango.cels.anl.gov:8004"
URL = BASE + "/v1/chat/completions"
MAX_INFLIGHT = 4          # start <=4 concurrent; a shared host


def served_model() -> str:
    with urllib.request.urlopen(BASE + "/v1/models", timeout=30) as r:
        return json.load(r)["data"][0]["id"]


MODEL = served_model()
EXPECTED = "Qwen/Qwen3.6-35B-A3B"
if MODEL != EXPECTED:
    raise SystemExit(
        f"mango is serving {MODEL!r}, not the {EXPECTED!r} this run was calibrated "
        f"against. Refusing to run: the generator identity is part of the result."
    )


class Mango:
    def __init__(self, inflight: int = MAX_INFLIGHT):
        self.slots: queue.Queue = queue.Queue()
        for _ in range(inflight):
            self.slots.put(1)
        self.lock = threading.Lock()
        self.requests = 0
        self.retries = 0
        self.failures = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.seconds = 0.0
        self.t_start = time.time()

    def _post(self, payload: dict, timeout: int) -> dict:
        body = json.dumps(payload).encode()
        req = urllib.request.Request(URL, body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)

    def chat(self, prompt: str, *, max_tokens: int = 3000, think: bool = True,
             temperature: float = 0.3, timeout: int = 900) -> dict:
        """One completion. Returns {"text", "ok", "usage", "finish", "elapsed", "note"}."""
        payload = {
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if not think:
            payload["chat_template_kwargs"] = {"enable_thinking": False}

        self.slots.get()
        try:
            budget = max_tokens
            note = ""
            for attempt in range(5):
                payload["max_tokens"] = budget
                try:
                    t0 = time.time()
                    r = self._post(payload, timeout)
                    dt = time.time() - t0
                    ch = r["choices"][0]
                    msg = ch["message"]
                    text = (msg.get("content") or "").strip()
                    if not text:
                        text = (msg.get("reasoning_content") or "").strip()
                    with self.lock:
                        self.requests += 1
                        self.prompt_tokens += r["usage"]["prompt_tokens"]
                        self.completion_tokens += r["usage"]["completion_tokens"]
                        self.seconds += dt
                    if not text and ch.get("finish_reason") == "length" and attempt < 3:
                        # Budget exhausted by invisible reasoning tokens, not a refusal.
                        budget *= 2
                        note = f"budget doubled to {budget}"
                        with self.lock:
                            self.retries += 1
                        continue
                    return {"text": text, "ok": bool(text), "usage": r["usage"],
                            "finish": ch.get("finish_reason"), "elapsed": round(dt, 2),
                            "note": note}
                except (urllib.error.URLError, TimeoutError, OSError,
                        json.JSONDecodeError, KeyError) as e:
                    with self.lock:
                        self.retries += 1
                    if attempt == 4:
                        with self.lock:
                            self.failures += 1
                        return {"text": "", "ok": False, "usage": {}, "finish": "error",
                                "elapsed": 0.0, "note": f"{type(e).__name__}: {e}"}
                    time.sleep(3 * (attempt + 1))       # back off, never hammer
            with self.lock:
                self.failures += 1
            return {"text": "", "ok": False, "usage": {}, "finish": "budget",
                    "elapsed": 0.0, "note": note}
        finally:
            self.slots.put(1)

    def stats(self) -> dict:
        return {
            "model": MODEL, "requests": self.requests, "retries": self.retries,
            "failures": self.failures, "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "llm_seconds": round(self.seconds, 1),
            "wall_seconds": round(time.time() - self.t_start, 1),
        }
