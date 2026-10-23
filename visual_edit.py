"""Validated per-format framing and duration-preserving visual transitions."""

from collections.abc import Mapping
from copy import deepcopy
import math

MOTIONS = ("push", "pull", "none")
DEFAULT_MOTION = {"kind": "push", "zoom": 1.08}


def _number(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name}必须在 {low} 到 {high} 之间。")
    return float(value)


def validate_visual_edit(edit):
    result = deepcopy(dict(edit))
    if "framing" in result:
        framing = result["framing"]
        if not isinstance(framing, Mapping) or set(framing) - {"9:16", "16:9"}:
            raise ValueError("构图仅支持横屏 16:9 和竖屏 9:16。")
        for ratio, value in framing.items():
            if not isinstance(value, Mapping) or set(value) - {"fit", "crop_x", "crop_y"}:
                raise ValueError("画幅构图设置无效。")
            if "fit" in value and value["fit"] not in ("cover", "contain"):
                raise ValueError("画幅填充方式无效。")
            for key in ("crop_x", "crop_y"):
                if key in value:
                    _number(value[key], "主体位置", 0, 1)
    if "transition" in result:
        transition = result["transition"]
        if not isinstance(transition, Mapping) or set(transition) - {"kind", "duration_seconds"}:
            raise ValueError("转场设置无效。")
        if transition.get("kind", "cut") not in ("cut", "fade_black"):
            raise ValueError("转场仅支持直接切换或淡黑切换。")
        if "duration_seconds" in transition:
            _number(transition["duration_seconds"], "单侧淡变时长", .05, 1)
    if "motion" in result:
        motion = result["motion"]
        if not isinstance(motion, Mapping) or set(motion) - {"kind", "zoom"}:
            raise ValueError("图片运动设置无效。")
        if motion.get("kind", "push") not in MOTIONS:
            raise ValueError("图片运动仅支持推近、拉远或静止。")
        if "zoom" in motion:
            _number(motion["zoom"], "推拉幅度", 1.01, 1.5)
    return result


def image_motion(edit):
    """Push or pull for a still photo; photos without a setting push in gently."""
    return {**DEFAULT_MOTION, **((edit or {}).get("motion") or {})}


def resolve_framing(edit, ratio=None):
    return {**{key: edit.get(key, default) for key, default in (("fit", "cover"), ("crop_x", .5), ("crop_y", .5))},
            **edit.get("framing", {}).get(ratio, {})}


def transition_windows(rows):
    incoming, outgoing = [0.] * len(rows), [0.] * len(rows)
    warnings = []
    for index in range(len(rows) - 1):
        setting = (rows[index].get("edit") or {}).get("transition", {})
        if setting.get("kind", "cut") == "cut":
            continue
        requested = setting.get("duration_seconds", .3)
        duration = min(requested, rows[index]["duration"] / 2, rows[index + 1]["duration"] / 2)
        if duration + 1e-6 < requested:
            warnings.append(f"镜头 {index + 1}：单侧淡变由 {requested:.2f} 秒缩短为 {duration:.2f} 秒。")
        outgoing[index], incoming[index + 1] = duration, duration
    return incoming, outgoing, warnings
