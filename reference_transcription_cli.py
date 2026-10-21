import argparse
import json
import os
import re
import sys
import traceback
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from audio_extractor import AudioExtractor
from speech_to_text import SpeechToText


def safe_name(path):
    name = Path(path).stem
    return re.sub(r"[^\w\u4e00-\u9fff-]+", "_", name).strip("_") or "video"


def write_result(path, payload):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description="Run reference transcription outside the Tk GUI process.")
    parser.add_argument("--video", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--model-size", required=True)
    parser.add_argument("--language", default="zh")
    parser.add_argument("--result-json", required=True)
    args = parser.parse_args()

    try:
        os.makedirs(args.output_dir, exist_ok=True)
        video_name = safe_name(args.video)
        audio_path = AudioExtractor(output_dir=args.output_dir).extract_audio(
            args.video,
            f"audio_{video_name}.wav",
        )
        transcript = SpeechToText(
            model_size=args.model_size,
            output_dir=args.output_dir,
        ).transcribe(audio_path, language=args.language)
        write_result(
            args.result_json,
            {
                "ok": True,
                "video": args.video,
                "video_name": video_name,
                "audio_path": audio_path,
                "transcript": transcript,
            },
        )
        return 0
    except Exception as exc:
        write_result(
            args.result_json,
            {
                "ok": False,
                "video": args.video,
                "error": str(exc),
                "traceback": traceback.format_exc(),
            },
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
