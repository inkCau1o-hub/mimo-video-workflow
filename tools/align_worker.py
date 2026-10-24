"""Forced-alignment worker run by alignment.py in its own Python environment (WhisperX + PyTorch).

It imports nothing from this repository. One request aligns several voiceovers with a single
model load:

    stdin   {"model": "<local wav2vec2 folder>", "items": [{"audio": "...", "text": "<spoken text>", "duration": 2.3}]}
    stdout  {"items": [{"chars": [["一", 0.05, 0.21], ...]} or {"error": "..."}]}

Each item returns one entry per character of its text; a character without a time has null times.
"""

import json
import sys


def main():
    request = json.load(sys.stdin)
    output, sys.stdout = sys.stdout, sys.stderr  # libraries may print; only the answer goes to stdout
    try:
        import whisperx

        model, metadata = whisperx.load_align_model(language_code="zh", device="cpu", model_name=request["model"])
        items = []
        for item in request["items"]:
            try:
                audio = whisperx.load_audio(item["audio"])
                aligned = whisperx.align([{"start": 0.0, "end": float(item["duration"]), "text": item["text"]}],
                                         model, metadata, audio, "cpu", return_char_alignments=True)
                chars = [[char["char"], char.get("start"), char.get("end")]
                         for segment in aligned["segments"] for char in segment.get("chars", [])]
                items.append({"chars": chars})
            except Exception as exc:  # noqa: BLE001 - one bad voiceover must not lose the others
                items.append({"error": f"{type(exc).__name__}: {exc}"})
    finally:
        sys.stdout = output
    json.dump({"items": items}, sys.stdout, ensure_ascii=False, default=lambda value: value.item())


if __name__ == "__main__":
    main()
