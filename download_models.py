import argparse
from pathlib import Path

from huggingface_hub import snapshot_download

import config


def download_model(model_size, models_dir):
    repo_id = f"Systran/faster-whisper-{model_size}"
    local_path = models_dir / f"faster-whisper-{model_size}"

    print(f"\n{'=' * 50}")
    print(f"下载模型: faster-whisper-{model_size}")
    print(f"保存到: {local_path}")
    print(f"{'=' * 50}")

    local_path.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=repo_id,
        local_dir=local_path,
        local_dir_use_symlinks=False,
    )
    print(f"完成: faster-whisper-{model_size}")


def main():
    parser = argparse.ArgumentParser(description="下载 faster-whisper 本地模型")
    parser.add_argument(
        "models",
        nargs="*",
        default=["tiny", "base"],
        help="要下载的模型，例如 tiny base small medium large-v3",
    )
    parser.add_argument(
        "--models-dir",
        default=config.MODELS_DIR,
        help="模型保存目录，默认读取 config.MODELS_DIR",
    )
    args = parser.parse_args()

    models_dir = Path(args.models_dir).expanduser()
    models_dir.mkdir(parents=True, exist_ok=True)

    for model_size in args.models:
        download_model(model_size, models_dir)

    print("\n全部模型下载完成")


if __name__ == "__main__":
    main()
