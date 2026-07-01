from pathlib import Path

import config
from download_models import download_model


MODEL_LIST = ["tiny", "base", "small", "medium", "large-v3"]


def main():
    models_dir = Path(config.MODELS_DIR).expanduser()
    models_dir.mkdir(parents=True, exist_ok=True)

    for model_size in MODEL_LIST:
        download_model(model_size, models_dir)

    print("\n所有模型下载完成")


if __name__ == "__main__":
    main()
