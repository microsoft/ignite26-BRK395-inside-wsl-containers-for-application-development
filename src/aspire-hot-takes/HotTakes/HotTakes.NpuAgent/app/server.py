"""Hot Take Bot brain: an LLM on the Intel NPU, served over HTTP from a Linux container.

Pure standard library + OpenVINO GenAI. Loads MODEL on the NPU (falls back to CPU if the
NPU is unavailable or fails to initialize), then serves:
  GET  /health    200 once the model is ready, 503 while loading (drives the Aspire dashboard)
  GET  /status    {state, device, deviceName, model, ...}
  POST /generate  {"existing": [...], "topic": "..."} -> {"text", "device", "totalMs", ...}
"""
import json
import os
import random
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import openvino as ov
import openvino_genai as genai

MODEL_DIR = os.environ.get("MODEL_DIR", "/models")
MODEL = os.environ.get("MODEL", "Qwen3-8B-int4-cw-ov")
DEVICES = [d.strip().upper() for d in os.environ.get("DEVICES", "NPU,CPU").split(",") if d.strip()]
MAX_NEW_TOKENS = int(os.environ.get("MAX_NEW_TOKENS", "48"))
PORT = int(os.environ.get("PORT", "8080"))
CACHE_DIR = os.path.join(MODEL_DIR, ".cache")

TOPICS = [
    "code reviews", "unit tests", "microservices", "Kubernetes", "AI coding assistants", "Git",
    "pair programming", "documentation", "meetings", "standups", "TypeScript", "regex",
    "monorepos", "dependency upgrades", "on-call", "naming variables", "comments in code",
    "the cloud", "containers", "keyboard shortcuts", "Vim vs Emacs", "Stack Overflow",
    "estimates", "technical debt", "open source", "CSS", "SQL", "logging", "10x engineers",
    "refactoring", "agile", "the terminal", "dark patterns in UX", "PowerShell", "Linux on Windows",
]

SYSTEM = (
    "You write short, funny, and very friendly and non-controversial hot takes for a developer conference voting board. "
    "A hot take is a bold one-sentence opinion that developers would argue about. But keep it nice and civil."
    "Reply with ONLY the hot take: one punchy sentence, at most 12 words, no quotes, no hashtags, no emoji, no preamble."
)

core = ov.Core()
state = {"state": "starting", "device": None, "deviceName": None, "model": MODEL,
         "detail": "", "availableDevices": [], "fallbackReason": "",
         "loadSeconds": None, "generated": 0, "lastMs": None}
pipe = None
sampling = True
gen_lock = threading.Lock()


def log(msg):
    # Aspire already prefixes each console line with the resource name.
    print(msg, flush=True)


def device_name(dev):
    try:
        return core.get_property(dev, "FULL_DEVICE_NAME")
    except Exception:
        return dev


def pretty_model(name):
    return re.sub(r"-(int4|int8|fp16)(-cw)?-ov$", "", name, flags=re.I)


def load():
    global pipe
    path = os.path.join(MODEL_DIR, MODEL)
    available = core.available_devices
    state["availableDevices"] = available
    if "NPU" not in DEVICES:
        state["fallbackReason"] = "NPU is not selected in DEVICES."
    log(f"devices visible in container: {available}")
    if not os.path.isdir(path):
        state.update(state="error", detail=f"model not found at {path} - run demo-prep.ps1")
        log(state["detail"])
        return
    for dev in DEVICES:
        if dev not in available:
            log(f"{dev} not visible in this container - skipping")
            if dev == "NPU":
                state["fallbackReason"] = "NPU is not visible in WSLc; check --gpus all and the Windows NPU driver."
            continue
        cfg = {"CACHE_DIR": CACHE_DIR}
        if dev == "NPU":
            # NPU pipelines are statically shaped: cap prompt and response sizes.
            cfg.update(MAX_PROMPT_LEN=1024, MIN_RESPONSE_LEN=max(64, MAX_NEW_TOKENS))
        state.update(state="loading", device=dev, deviceName=device_name(dev),
                     detail=f"compiling {pretty_model(MODEL)} for {dev} (cached after first run)")
        log(state["detail"])
        try:
            started = time.time()
            candidate = genai.LLMPipeline(path, dev, **cfg)
            state["detail"] = "warming up"
            candidate.generate(chat_prompt(candidate, "Say hi in three words."),
                               genai.GenerationConfig(max_new_tokens=8))
            pipe = candidate
            state.update(state="ready", detail="", loadSeconds=round(time.time() - started, 1))
            log(f"ready: {pretty_model(MODEL)} on {dev} ({state['deviceName']}) in {state['loadSeconds']}s")
            return
        except Exception as exc:
            log(f"{dev} failed: {exc}")
            message = str(exc).strip() or type(exc).__name__
            reason = f"{dev} failed: {message.splitlines()[-1][:200]}"
            if dev == "NPU":
                if "zeCommandQueueCreate" in message:
                    reason += " Check the Windows NPU driver and WSL NPU libraries."
                state["fallbackReason"] = reason
            state.update(detail=reason)
    state.update(state="error", device=None, deviceName=None,
                 detail=state["detail"] or f"none of {DEVICES} are visible - did you pass --gpus all?")
    log(state["detail"])


def chat_prompt(p, user):
    """Render the chat template ourselves so Qwen3-style models can be told not to 'think'."""
    history = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
    tok = p.get_tokenizer()
    try:
        return tok.apply_chat_template(history, add_generation_prompt=True,
                                       extra_context={"enable_thinking": False})
    except TypeError:
        return tok.apply_chat_template(history, add_generation_prompt=True)


def clean(text):
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    text = text.replace("<think>", "").replace("</think>", "")
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    if not lines:
        return ""
    line = re.sub(r"^(hot take:|\d+[.)]|[-*•])\s*", "", lines[0], flags=re.I)
    line = line.strip().strip('"“”\'').strip()
    words = line.split()
    if len(words) > 22:
        line = " ".join(words[:22]).rstrip(",;:") + "…"
    return line[:160]


def generate_once(existing, topic):
    global sampling
    avoid = "\n".join(f"- {t}" for t in existing[-20:])
    user = f"Write one brand-new hot take about {topic}."
    if avoid:
        user += f"\nIt must be different from these existing takes:\n{avoid}"
    prompt = chat_prompt(pipe, user)
    cfg = genai.GenerationConfig(max_new_tokens=MAX_NEW_TOKENS, apply_chat_template=False)
    if sampling:
        # A fresh seed per call; the default seed is fixed, which makes every sample identical.
        cfg.do_sample, cfg.temperature, cfg.top_p, cfg.top_k = True, 0.9, 0.95, 50
        cfg.rng_seed = random.randrange(1, 2**31)
    first = {}

    def on_text(_chunk):
        first.setdefault("t", time.perf_counter())
        return False

    started = time.perf_counter()
    try:
        # List input returns DecodedResults (with perf metrics) instead of a plain str.
        res = pipe.generate([prompt], cfg, on_text)
    except Exception as exc:
        if not sampling:
            raise
        log(f"sampling not supported on {state['device']} ({exc}); falling back to greedy decoding")
        sampling = False
        return generate_once(existing, topic)
    total = time.perf_counter() - started
    raw = res.texts[0]
    return {
        "text": clean(raw), "raw": raw, "topic": topic,
        "device": state["device"], "deviceName": state["deviceName"],
        "model": pretty_model(MODEL),
        "ttftMs": round(((first.get("t") or started + total) - started) * 1000),
        "totalMs": round(total * 1000), "tokens": res.perf_metrics.get_num_generated_tokens(),
    }


def generate(existing, topic):
    seen = {t.strip().lower() for t in existing}
    for attempt in range(3):
        result = generate_once(existing, topic or random.choice(TOPICS))
        if result["text"] and result["text"].strip().lower() not in seen:
            return result
        log(f"attempt {attempt + 1} produced an empty/duplicate take, retrying")
    return result


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            ok = state["state"] == "ready"
            return self.send_json({"status": "Healthy" if ok else state["state"]}, 200 if ok else 503)
        if self.path == "/status":
            return self.send_json({**state, "modelName": pretty_model(MODEL), "busy": gen_lock.locked()})
        return self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path != "/generate":
            return self.send_json({"error": "not found"}, 404)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self.send_json({"error": "invalid JSON"}, 400)
        if pipe is None:
            return self.send_json({"error": f"model not ready ({state['state']})", **state}, 503)
        with gen_lock:
            try:
                result = generate([str(t) for t in body.get("existing", [])], str(body.get("topic") or ""))
            except Exception as exc:
                log(f"generate failed: {exc}")
                return self.send_json({"error": str(exc)[:300]}, 500)
        state.update(generated=state["generated"] + 1, lastMs=result["totalMs"])
        log(f"generated on {result['device']} in {result['totalMs']} ms "
            f"(first token {result['ttftMs']} ms, {result['tokens']} tokens): {result['text']!r}")
        return self.send_json(result)


if __name__ == "__main__":
    threading.Thread(target=load, daemon=True).start()
    log(f"model={MODEL} devices={DEVICES} listening on :{PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
