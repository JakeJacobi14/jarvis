#!/usr/bin/env python3
"""
Benchmark Jarvis LLM + TTS latency with fixed prompts (STT skipped).

Publishes canned text to jarvis/stt/text, waits for brain → TTS to finish,
and prints per-prompt splits plus averages. Save/compare runs as you swap
local Ollama / Kokoro for APIs.

Requires broker + brain + TTS already running (./start.sh).
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import paho.mqtt.client as mqtt

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT_DIR = ROOT / "logs" / "benchmarks"

# Fixed prompts — same order every run so A/B comparisons are meaningful.
PROMPTS = [
    "What time is it roughly?",
    "Tell me a one sentence joke.",
    "What's the capital of France?",
    "Should I have coffee or tea?",
    "Summarize gravity in one sentence.",
]

QUIET_AFTER_DONE = 0.85  # seconds of silence after TTS done before ending turn
TURN_TIMEOUT = 90.0
MQTT_HOST = "localhost"
MQTT_PORT = 1883


@dataclass
class TurnResult:
    index: int
    prompt: str
    reply_sentences: list[str]
    t_first_sentence: float | None  # publish → first brain sentence
    t_llm_stream: float | None  # publish → last brain sentence
    t_tts_after_first: float | None  # first sentence → final TTS done
    t_e2e: float | None  # publish → final TTS done
    sentence_count: int = 0
    error: str | None = None


@dataclass
class BenchReport:
    label: str
    started_at: str
    prompts: list[str]
    turns: list[TurnResult] = field(default_factory=list)
    averages: dict[str, float | None] = field(default_factory=dict)


class TurnWatcher:
    """Correlate MQTT brain/TTS traffic for one injected prompt."""

    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    def reset(self):
        with self._lock:
            self.t0: float | None = None
            self.sentences: list[tuple[float, str]] = []
            self.done_at: float | None = None
            self.last_event: float = time.perf_counter()
            self._armed = False

    def arm(self, t0: float):
        with self._lock:
            self.t0 = t0
            self.sentences = []
            self.done_at = None
            self.last_event = t0
            self._armed = True

    def on_brain(self, text: str):
        with self._lock:
            if not self._armed or self.t0 is None:
                return
            now = time.perf_counter()
            self.sentences.append((now, text))
            self.done_at = None  # more speech may follow
            self.last_event = now

    def on_tts_done(self):
        with self._lock:
            if not self._armed or self.t0 is None:
                return
            if not self.sentences:
                return
            now = time.perf_counter()
            self.done_at = now
            self.last_event = now

    def snapshot(
        self,
    ) -> tuple[float | None, float, list[tuple[float, str]], float | None]:
        with self._lock:
            if not self._armed:
                return None, self.last_event, [], None
            return (
                self.t0,
                self.last_event,
                list(self.sentences),
                self.done_at,
            )


def avg(values: list[float | None]) -> float | None:
    nums = [v for v in values if v is not None]
    if not nums:
        return None
    return statistics.mean(nums)


def fmt(seconds: float | None) -> str:
    if seconds is None:
        return "  —  "
    return f"{seconds:5.2f}s"


def print_table(report: BenchReport) -> None:
    print()
    print("=" * 72)
    print(f"  Jarvis bench · {report.label} · {report.started_at}")
    print("=" * 72)
    print(
        f"{'#':>2}  {'1st sent':>8}  {'LLM done':>8}  {'TTS tail':>8}  "
        f"{'E2E':>8}  prompt"
    )
    print("-" * 72)
    for t in report.turns:
        if t.error:
            print(f"{t.index:>2}  ERROR: {t.error}  ← {t.prompt}")
            continue
        print(
            f"{t.index:>2}  {fmt(t.t_first_sentence)}  {fmt(t.t_llm_stream)}  "
            f"{fmt(t.t_tts_after_first)}  {fmt(t.t_e2e)}  {t.prompt}"
        )
        if t.reply_sentences:
            preview = " ".join(t.reply_sentences)
            if len(preview) > 60:
                preview = preview[:57] + "..."
            print(f"    → {preview}")
    print("-" * 72)
    a = report.averages
    print(
        f"{'avg':>2}  {fmt(a.get('t_first_sentence'))}  {fmt(a.get('t_llm_stream'))}  "
        f"{fmt(a.get('t_tts_after_first'))}  {fmt(a.get('e2e'))}"
    )
    ok = [t for t in report.turns if not t.error and t.t_e2e is not None]
    if ok:
        total = sum(t.t_e2e for t in ok if t.t_e2e is not None)
        print(f"\n  turns ok: {len(ok)}/{len(report.turns)}  ·  sum E2E: {total:.2f}s")
    print()
    print("  Splits:")
    print("    1st sent  = publish → first brain sentence (LLM time-to-first)")
    print("    LLM done  = publish → last brain sentence (stream finish≈)")
    print("    TTS tail  = first sentence → final TTS done (synth+play remainder)")
    print("    E2E       = publish → final TTS done (full spoken reply)")
    print("=" * 72)


def print_compare(a: BenchReport, b: BenchReport) -> None:
    keys = [
        ("t_first_sentence", "1st sent"),
        ("t_llm_stream", "LLM done"),
        ("t_tts_after_first", "TTS tail"),
        ("e2e", "E2E"),
    ]
    print()
    print("=" * 72)
    print(f"  Compare · {a.label}  →  {b.label}")
    print("=" * 72)
    print(f"{'metric':<10}  {a.label:>12}  {b.label:>12}  {'delta':>10}  {'speedup':>8}")
    print("-" * 72)
    for key, name in keys:
        av = a.averages.get(key)
        bv = b.averages.get(key)
        if av is None or bv is None or av <= 0:
            print(f"{name:<10}  {fmt(av)}  {fmt(bv)}  {'—':>10}  {'—':>8}")
            continue
        delta = bv - av
        speedup = av / bv if bv > 0 else float("inf")
        sign = "+" if delta > 0 else ""
        print(
            f"{name:<10}  {fmt(av)}  {fmt(bv)}  {sign}{delta:7.2f}s  {speedup:7.2f}x"
        )
    print("=" * 72)
    print("  (speedup > 1 means B is faster)")
    print()


def load_report(path: Path) -> BenchReport:
    data = json.loads(path.read_text(encoding="utf-8"))
    turns = [TurnResult(**t) for t in data["turns"]]
    return BenchReport(
        label=data["label"],
        started_at=data["started_at"],
        prompts=data["prompts"],
        turns=turns,
        averages=data.get("averages", {}),
    )


def run_benchmark(label: str, n_prompts: int, out_dir: Path) -> BenchReport:
    prompts = PROMPTS[:n_prompts]
    watcher = TurnWatcher()
    connected = threading.Event()

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"jarvis-bench-{int(time.time())}",
    )

    def on_connect(c, userdata, flags, reason_code, properties=None):
        c.subscribe("jarvis/brain/response")
        c.subscribe("jarvis/tts/response")
        connected.set()

    def on_message(c, userdata, msg):
        payload = msg.payload.decode().strip()
        if msg.topic == "jarvis/brain/response":
            if payload:
                watcher.on_brain(payload)
        elif msg.topic == "jarvis/tts/response" and payload == "done":
            watcher.on_tts_done()

    client.on_connect = on_connect
    client.on_message = on_message

    try:
        client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
    except Exception as e:
        print(f"ERROR: cannot connect to MQTT at {MQTT_HOST}:{MQTT_PORT}: {e}", file=sys.stderr)
        print("Start the stack first: ./start.sh", file=sys.stderr)
        sys.exit(1)

    client.loop_start()
    if not connected.wait(5):
        print("ERROR: MQTT connect timed out", file=sys.stderr)
        sys.exit(1)

    # Give subscriptions a moment; clear any leftover speech.
    time.sleep(0.3)
    client.publish("jarvis/tts/interrupt", "stop")
    time.sleep(0.4)

    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report = BenchReport(label=label, started_at=started_at, prompts=list(prompts))

    print(f"Running {len(prompts)} prompts (label={label!r})...")
    print("Brain memory is reset between prompts for consistency.\n")

    for i, prompt in enumerate(prompts, start=1):
        print(f"[{i}/{len(prompts)}] {prompt!r}")
        client.publish("jarvis/brain/reset", "1")
        time.sleep(0.15)
        client.publish("jarvis/tts/interrupt", "stop")
        time.sleep(0.25)

        watcher.arm(time.perf_counter())
        client.publish("jarvis/stt/text", prompt)

        turn = TurnResult(
            index=i,
            prompt=prompt,
            reply_sentences=[],
            t_first_sentence=None,
            t_llm_stream=None,
            t_tts_after_first=None,
            t_e2e=None,
        )

        deadline = time.perf_counter() + TURN_TIMEOUT
        try:
            while True:
                now = time.perf_counter()
                if now > deadline:
                    raise TimeoutError(
                        f"no complete turn within {TURN_TIMEOUT:.0f}s "
                        "(is brain + TTS running?)"
                    )
                t0, last_event, sentences, done_at = watcher.snapshot()
                if (
                    t0 is not None
                    and sentences
                    and done_at is not None
                    and (now - last_event) >= QUIET_AFTER_DONE
                ):
                    first_t = sentences[0][0]
                    last_t = sentences[-1][0]
                    turn.reply_sentences = [s for _, s in sentences]
                    turn.sentence_count = len(sentences)
                    turn.t_first_sentence = first_t - t0
                    turn.t_llm_stream = last_t - t0
                    turn.t_tts_after_first = done_at - first_t
                    turn.t_e2e = done_at - t0
                    break
                time.sleep(0.05)
        except TimeoutError as e:
            turn.error = str(e)
            print(f"  !! {e}")
        else:
            print(
                f"  1st={fmt(turn.t_first_sentence).strip()}  "
                f"llm={fmt(turn.t_llm_stream).strip()}  "
                f"tts={fmt(turn.t_tts_after_first).strip()}  "
                f"e2e={fmt(turn.t_e2e).strip()}  "
                f"({turn.sentence_count} sent)"
            )

        report.turns.append(turn)
        # Brief pause so TTS interrupt / STT follow-up settle.
        time.sleep(0.5)

    report.averages = {
        "t_first_sentence": avg([t.t_first_sentence for t in report.turns]),
        "t_llm_stream": avg([t.t_llm_stream for t in report.turns]),
        "t_tts_after_first": avg([t.t_tts_after_first for t in report.turns]),
        "e2e": avg([t.t_e2e for t in report.turns]),
    }

    client.publish("jarvis/tts/interrupt", "stop")
    client.loop_stop()
    client.disconnect()

    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_label = "".join(c if c.isalnum() or c in "-_" else "-" for c in label)
    out_path = out_dir / f"{stamp}_{safe_label}.json"
    out_path.write_text(
        json.dumps(
            {
                "label": report.label,
                "started_at": report.started_at,
                "prompts": report.prompts,
                "turns": [asdict(t) for t in report.turns],
                "averages": report.averages,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print_table(report)
    print(f"Saved: {out_path}")
    return report


def main():
    parser = argparse.ArgumentParser(
        description="Benchmark Jarvis LLM+TTS with fixed prompts (skips STT)."
    )
    parser.add_argument(
        "--label",
        default="baseline",
        help="Name this run (e.g. local, api-llm, api-llm-tts)",
    )
    parser.add_argument(
        "--prompts",
        type=int,
        default=5,
        choices=range(1, len(PROMPTS) + 1),
        metavar="N",
        help=f"How many fixed prompts to run (1–{len(PROMPTS)}, default 5)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help="Where to write JSON results",
    )
    parser.add_argument(
        "--compare",
        nargs=2,
        metavar=("A.json", "B.json"),
        help="Compare two saved bench JSON files instead of running",
    )
    args = parser.parse_args()

    if args.compare:
        a = load_report(Path(args.compare[0]))
        b = load_report(Path(args.compare[1]))
        print_table(a)
        print_table(b)
        print_compare(a, b)
        return

    run_benchmark(args.label, args.prompts, args.out_dir)


if __name__ == "__main__":
    main()
