"""Pure-data help catalog and tutorial state machine; no Qt, hardware or network IO."""
from __future__ import annotations

from dataclasses import dataclass, field
from html import escape
import json
from pathlib import Path
from typing import Any, Mapping


REQUIRED_FIELDS = ("title", "category", "summary", "steps", "explanation", "example", "pitfalls")
GATES = frozenset(("read", "quiz", "event", "predicate"))


class CatalogError(ValueError):
    """Catalog or lesson schema is invalid."""


class Catalog:
    def __init__(self, entries: Mapping[str, dict], courses: list[dict]):
        self.entries = dict(entries)
        self.courses = {c["id"]: c for c in courses}
        self.validate()

    @classmethod
    def load(cls, folder: Path | str) -> "Catalog":
        folder = Path(folder)
        entries = json.loads((folder / "catalog.json").read_text(encoding="utf-8"))["entries"]
        courses = json.loads((folder / "courses.json").read_text(encoding="utf-8"))["courses"]
        return cls(entries, courses)

    def validate(self):
        if not self.entries:
            raise CatalogError("Empty help catalog")
        for key, entry in self.entries.items():
            for name in REQUIRED_FIELDS:
                if not entry.get(name):
                    raise CatalogError(f"{key}: missing {name}")
            if not isinstance(entry["steps"], list) or not all(
                isinstance(value, str) and value.strip() for value in entry["steps"]
            ):
                raise CatalogError(f"{key}: steps must be non-empty text")
            for related in entry.get("related", []):
                if related not in self.entries:
                    raise CatalogError(f"{key}: unknown related topic {related}")
        for course_id, course in self.courses.items():
            if not course.get("steps"):
                raise CatalogError(f"{course_id}: no steps")
            seen = set()
            for step in course["steps"]:
                if step["id"] in seen:
                    raise CatalogError(f"{course_id}: duplicate step {step['id']}")
                seen.add(step["id"])
                if step.get("gate") not in GATES:
                    raise CatalogError(f"{course_id}/{step['id']}: invalid gate")
                if step["help"] not in self.entries:
                    raise CatalogError(f"{course_id}/{step['id']}: missing topic")
                if step["gate"] == "quiz":
                    answers = step.get("answers", [])
                    correct = step.get("correct")
                    if not (len(answers) >= 2 and isinstance(correct, int)
                            and 0 <= correct < len(answers)):
                        raise CatalogError(f"{course_id}/{step['id']}: invalid quiz")
                if step["gate"] == "event" and not step.get("event"):
                    raise CatalogError(f"{course_id}/{step['id']}: missing event")
                if step["gate"] == "predicate" and not step.get("predicate"):
                    raise CatalogError(f"{course_id}/{step['id']}: missing predicate")

    def search(self, query: str = "", category: str = "") -> list[str]:
        words = query.casefold().split()
        found = []
        for key, entry in self.entries.items():
            if category and category != entry["category"]:
                continue
            haystack = " ".join([key, entry["title"], entry["summary"],
                                entry["explanation"], entry["example"], entry["pitfalls"],
                                " ".join(entry["steps"]), entry.get("keywords", "")]).casefold()
            if all(word in haystack for word in words):
                found.append(key)
        return sorted(found, key=lambda key: (self.entries[key]["category"], self.entries[key]["title"]))

    def html(self, key: str, *, detail: bool = True, current: str = "") -> str:
        """Escape all source/dynamic content; no external assets or executable HTML."""
        e = self.entries[key]
        paragraph = lambda title, text: f"<h3>{escape(title)}</h3><p>{escape(str(text)).replace(chr(10), '<br>')}</p>"
        html = [f"<h2>{escape(e['title'])}</h2>",
                f"<p><b>{escape(e['category'])}</b></p>",
                f"<p>{escape(e['summary'])}</p>"]
        if current:
            html.append(paragraph("当前控件状态", current))
        html.append("<h3>怎样使用</h3><ol>" +
                    "".join(f"<li>{escape(s)}</li>" for s in e["steps"]) + "</ol>")
        if e.get("risk"):
            html.append(paragraph("操作前请注意", e["risk"]))
        if detail:
            html += [paragraph("为什么这样设计", e["explanation"]),
                     paragraph("一个具体例子", e["example"]),
                     paragraph("常见误区与排查", e["pitfalls"])]
            if e.get("scope"):
                html.append(paragraph("职责边界", e["scope"]))
            if e.get("source"):
                html.append(paragraph("对应实现", e["source"]))
        else:
            html.append("<p>按 F1 或选择“固定并深入了解”，阅读原理、示例和排查方法。</p>")
        return "".join(html)


@dataclass
class LessonProgress:
    """Persistent progress. Skipped steps never masquerade as completed."""
    course_id: str
    step_index: int = 0
    completed: set[str] = field(default_factory=set)
    skipped: set[str] = field(default_factory=set)
    paused: bool = False
    version: int = 1
    evidence: dict[str, str] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"version": self.version, "course_id": self.course_id,
                "step_index": self.step_index, "completed": sorted(self.completed),
                "skipped": sorted(self.skipped), "paused": self.paused,
                "evidence": dict(self.evidence), "context": dict(self.context)}

    @classmethod
    def from_dict(cls, data: Any, course: dict) -> "LessonProgress":
        if not isinstance(data, dict) or data.get("version") != 1 or data.get("course_id") != course["id"]:
            return cls(course["id"])
        ids = {s["id"] for s in course["steps"]}
        try:
            index = min(max(int(data.get("step_index", 0)), 0), len(course["steps"]) - 1)
            completed = set(data.get("completed", [])) & ids
            skipped = (set(data.get("skipped", [])) & ids) - completed
            evidence = {str(k): str(v)[:300] for k, v in data.get("evidence", {}).items() if k in completed}
        except (TypeError, ValueError, AttributeError):
            return cls(course["id"])
        context = data.get("context", {})
        if not isinstance(context, dict):
            context = {}
        # Accept only small inert JSON values, never code or objects.
        context = {str(k)[:80]: v for k, v in context.items()
                   if isinstance(v, (str, int, float, bool, type(None))) and len(str(v)) < 500}
        return cls(course["id"], index, completed, skipped, bool(data.get("paused")), 1, evidence, context)


class LessonSession:
    def __init__(self, course: dict, saved: Any = None):
        self.course = course
        self.progress = LessonProgress.from_dict(saved, course)
        self.ready = self._restored_ready()
        self.feedback = ""

    def _restored_ready(self):
        step = self.step
        return (step["id"] in self.progress.completed or
                (step["gate"] == "event" and
                 self.progress.context.get("_event:" + step["id"]) == step["event"]))

    @property
    def step(self):
        return self.course["steps"][self.progress.step_index]

    @property
    def complete(self):
        return len(self.progress.completed) == len(self.course["steps"])

    def mark_read(self):
        if self.step["gate"] == "read":
            self.ready = True
            self.feedback = "已阅读本步骤说明。"

    def answer(self, index: int) -> bool:
        step = self.step
        if step["gate"] != "quiz":
            return False
        self.ready = index == step["correct"]
        self.feedback = step.get("feedback", "请依据说明再试一次。") if not self.ready else "回答正确，可以继续。"
        return self.ready

    def notify_event(self, event_id: str):
        if not self.progress.paused and self.step["gate"] == "event" and self.step["event"] == event_id:
            self.ready = True
            self.progress.context["_event:" + self.step["id"]] = event_id
            self.feedback = "已检测到本步骤的实际界面操作。"

    def notify_predicate(self, name: str, passed: bool, detail: str = ""):
        if (not self.progress.paused and self.step["gate"] == "predicate"
                and self.step["predicate"] == name):
            # Do not revoke already completed learning steps on temporary data loss.
            self.ready = bool(passed) or self.step["id"] in self.progress.completed
            self.feedback = detail or ("已观察到目标状态。" if passed else "等待满足条件；没有设备时可以明确跳过。")

    def advance(self, *, skip=False) -> bool:
        key = self.step["id"]
        if skip:
            if key not in self.progress.completed:
                self.progress.skipped.add(key)
        else:
            if not self.ready:
                return False
            self.progress.completed.add(key)
            self.progress.skipped.discard(key)
            self.progress.evidence[key] = self.feedback or "已确认阅读"
        if self.progress.step_index < len(self.course["steps"]) - 1:
            self.progress.step_index += 1
            self.ready = self._restored_ready()
            self.feedback = ""
        return True

    def back(self):
        self.progress.step_index = max(0, self.progress.step_index - 1)
        self.ready = self._restored_ready()
        self.feedback = ""

    def pause(self):
        self.progress.paused = True

    def resume(self):
        self.progress.paused = False
