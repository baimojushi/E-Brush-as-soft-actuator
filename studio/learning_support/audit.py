"""Static help coverage audit. This does not claim native GUI visual acceptance."""
from __future__ import annotations
import argparse
import ast
import json
from pathlib import Path
from .model import Catalog

CONTROL_TYPES = {
    "QPushButton", "QToolButton", "QCheckBox", "QRadioButton", "QComboBox",
    "QSpinBox", "QDoubleSpinBox", "QSlider", "QLineEdit", "QPlainTextEdit",
    "QTextEdit", "QTextBrowser", "QTableWidget", "QListWidget", "QTabWidget",
    "QGroupBox", "QDockWidget", "QProgressBar", "PlotWidget", "ImageView",
}

def audit(package=None):
    package = Path(package) if package else Path(__file__).resolve().parents[1]
    root = package.parent
    catalog = Catalog.load(package / "learning_support")
    bindings, missing = [], []
    constructors = 0
    for path in sorted(package.rglob("*.py")):
        if "learning_support" in path.parts or "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        parents = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            name = n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
            if name == "_help_anchor":
                expression = n.args[1]
                bindings.append({
                    "file": path.relative_to(root).as_posix(), "line": n.lineno,
                    "target": ast.unparse(n.args[0]), "topic_expression": ast.unparse(expression)})
                if isinstance(expression, ast.Constant) and expression.value not in catalog.entries:
                    missing.append({"file": path.relative_to(root).as_posix(), "line":n.lineno,
                                    "error":"Unknown literal help ID", "help_id":expression.value})
            if name in CONTROL_TYPES:
                constructors += 1
                parent = parents.get(n)
                if not (isinstance(parent, ast.Call) and isinstance(parent.func, ast.Name)
                        and parent.func.id == "_help_anchor"):
                    missing.append({"file":path.relative_to(root).as_posix(), "line":n.lineno,
                                    "error":"Unbound owned control", "type":name})
    return {
        "scope": "Owned control constructor sites; inherited native children and custom canvas bindings use integration.py",
        "kind": "static-source-audit-not-native-visual-acceptance",
        "catalog_entries": len(catalog.entries), "courses": len(catalog.courses),
        "course_steps": sum(len(c["steps"]) for c in catalog.courses.values()),
        "owned_control_constructor_sites": constructors,
        "missing": missing, "bindings": bindings,
        "notes": [
            "Loop constructor sites expand to multiple runtime controls.",
            "Form labels, metric cards and custom canvases are additionally bound in integration.py.",
            "Concept-only topics and optional-entry topics need not have a visible widget in the main window.",
            "OS file dialogs and third-party internal menus retain their native behavior.",
            "Run LearningManager.coverage() and target-machine GUI acceptance for native runtime verification.",
        ],
    }

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = audit()
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text+"\n", encoding="utf-8")
    else:
        print(text)
    return 1 if report["missing"] else 0

if __name__ == "__main__":
    raise SystemExit(main())
