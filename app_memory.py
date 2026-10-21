import json
from datetime import datetime
from pathlib import Path

import config


MEMORY_PATH = Path(config.MEMORY_PATH)


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _empty_memory():
    return {
        "version": 1,
        "facts": [],
        "last_task": {},
        "updated_at": None,
    }


def load_memory():
    if not MEMORY_PATH.exists():
        return _empty_memory()

    try:
        with open(MEMORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return _empty_memory()
    except (OSError, json.JSONDecodeError):
        return _empty_memory()

    data.setdefault("version", 1)
    data.setdefault("facts", [])
    data.setdefault("last_task", {})
    data.setdefault("updated_at", None)
    return data


def save_memory(data):
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = _now()
    with open(MEMORY_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data


def _next_id(facts):
    ids = [item.get("id", 0) for item in facts if isinstance(item.get("id"), int)]
    return max(ids, default=0) + 1


def remember(text, category="note"):
    text = str(text).strip()
    if not text:
        raise ValueError("记忆内容不能为空")

    data = load_memory()
    item = {
        "id": _next_id(data["facts"]),
        "category": category,
        "text": text,
        "created_at": _now(),
    }
    data["facts"].append(item)
    save_memory(data)
    return item


def forget(memory_id):
    data = load_memory()
    before = len(data["facts"])
    data["facts"] = [item for item in data["facts"] if item.get("id") != memory_id]
    if len(data["facts"]) == before:
        return False
    save_memory(data)
    return True


def clear_facts():
    data = load_memory()
    data["facts"] = []
    save_memory(data)


def update_last_task(task):
    data = load_memory()
    data["last_task"] = {
        **task,
        "updated_at": _now(),
    }
    save_memory(data)


def list_facts():
    return load_memory()["facts"]


def get_last_task():
    return load_memory().get("last_task", {})


def memory_context():
    data = load_memory()
    lines = []

    facts = data.get("facts", [])
    if facts:
        lines.append("长期记忆:")
        for item in facts:
            category = item.get("category", "note")
            lines.append(f"- [{item.get('id')}] ({category}) {item.get('text', '')}")

    last_task = data.get("last_task", {})
    if last_task:
        lines.append("最近任务:")
        for key in [
            "product_name",
            "selling_points",
            "requirements",
            "model_size",
            "enable_titles",
            "output_dir",
        ]:
            if key in last_task:
                lines.append(f"- {key}: {last_task[key]}")

    return "\n".join(lines).strip()
