"""Turn photos into upright sRGB JPEGs that FFmpeg can use as stills.

FFmpeg ignores ICC profiles and cannot read every HEIC, so photos are normalized when
they are imported: HEIC goes through macOS `sips` (pillow-heif elsewhere), everything
else through Pillow. Orientation is baked into the pixels, wide-gamut colors become
sRGB, transparency is flattened on white and very large photos are scaled down.
"""

from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError

IMAGE_SUFFIXES = frozenset({".heic", ".heif", ".jpg", ".jpeg", ".png", ".webp"})
HEIF_SUFFIXES = frozenset({".heic", ".heif"})
MAX_EDGE = 4096
QUALITY = 92
SIPS_SRGB_PROFILE = "/System/Library/ColorSync/Profiles/sRGB Profile.icc"
_SRGB = ImageCms.createProfile("sRGB")
_SRGB_BYTES = ImageCms.ImageCmsProfile(_SRGB).tobytes()


class ImageNormalizeError(ValueError):
    """A photo that cannot be read or converted; the message is shown to the user."""


def is_image(path) -> bool:
    return Path(str(path)).suffix.lower() in IMAGE_SUFFIXES


def normalize_image(source, output_dir, *, max_edge: int = MAX_EDGE, runner: Callable = subprocess.run,
                    platform: str = sys.platform) -> Path:
    """Write the normalized JPEG into output_dir, named by content, and return its path."""
    source = Path(source)
    if not is_image(source):
        raise ImageNormalizeError(f"不支持的图片格式：{source.name}")
    try:
        content = source.read_bytes()
    except OSError as exc:
        raise ImageNormalizeError(f"无法读取图片：{source.name}") from exc
    digest = hashlib.sha256(content + f"|{max_edge}".encode()).hexdigest()[:12]
    output_dir = Path(output_dir)
    target = output_dir / f"{source.stem}_{digest}.jpg"
    if target.is_file():
        return target
    output_dir.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix=".normalize-", suffix=".jpg", dir=output_dir)
    os.close(handle)
    temporary = Path(name)
    try:
        if source.suffix.lower() in HEIF_SUFFIXES and platform == "darwin":
            _convert_with_sips(source, temporary, max_edge, runner)
        else:
            _convert_with_pillow(source, temporary, max_edge)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def _convert_with_sips(source: Path, target: Path, max_edge: int, runner: Callable) -> None:
    # sips applies the HEIC rotation itself; --matchTo converts Display P3 pixels to sRGB.
    probe = runner(["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(source)],
                   capture_output=True, text=True, check=False)
    size = [int(line.split(":")[1]) for line in str(probe.stdout).splitlines()
            if line.strip().startswith(("pixelWidth:", "pixelHeight:"))]
    if probe.returncode != 0 or len(size) != 2:
        raise ImageNormalizeError(f"无法读取图片：{source.name}")
    argv = ["sips", "-s", "format", "jpeg", "-s", "formatOptions", str(QUALITY)]
    if max(size) > max_edge:
        argv += ["-Z", str(max_edge)]
    argv += ["--matchTo", SIPS_SRGB_PROFILE, str(source), "--out", str(target)]
    done = runner(argv, capture_output=True, text=True, check=False)
    if done.returncode != 0 or not target.is_file() or target.stat().st_size == 0:
        raise ImageNormalizeError(f"图片转换失败：{source.name}")


def _convert_with_pillow(source: Path, target: Path, max_edge: int) -> None:
    if source.suffix.lower() in HEIF_SUFFIXES:
        try:
            from pillow_heif import register_heif_opener
        except ImportError:
            raise ImageNormalizeError("读取 HEIC 需要 pillow-heif，请运行安装环境脚本。") from None
        register_heif_opener()
    try:
        with Image.open(source) as opened:
            icc = opened.info.get("icc_profile")
            image = ImageOps.exif_transpose(opened)
            if image.mode in ("P", "LA", "PA"):
                image = image.convert("RGBA")
            alpha = image.getchannel("A") if image.mode == "RGBA" else None
            base = image if image.mode in ("RGB", "CMYK", "L") else image.convert("RGB")
            rgb = base.convert("RGB")
            if icc:
                try:
                    rgb = ImageCms.profileToProfile(base, ImageCms.ImageCmsProfile(io.BytesIO(icc)), _SRGB,
                                                    outputMode="RGB")
                except ImageCms.PyCMSError:
                    pass  # an unreadable profile: keep the pixels as they are
            if alpha is not None:
                flat = Image.new("RGB", rgb.size, (255, 255, 255))
                flat.paste(rgb, mask=alpha)
                rgb = flat
            rgb.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
            rgb.save(target, "JPEG", quality=QUALITY, icc_profile=_SRGB_BYTES)
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ImageNormalizeError(f"无法读取图片：{source.name}") from exc
