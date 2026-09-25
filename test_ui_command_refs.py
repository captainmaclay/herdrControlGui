"""Статическая проверка config_app.py: каждое обращение `self.<имя>` в HerdrConfigApp должно
указывать на существующий метод, атрибут класса, атрибут, присвоенный где-либо в классе,
или на метод tkinter.Tk.

Ловит целый класс падений, которые повторялись после правок агентами:
  AttributeError: '_tkinter.tkapp' object has no attribute '_save_claude_settings_action'
  AttributeError: '_tkinter.tkapp' object has no attribute 'load_data'
Работает за миллисекунды и не требует дисплея.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent / "config_app.py"
CLASS_NAME = "HerdrConfigApp"


def _collect(tree: ast.Module):
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == CLASS_NAME)
    defined: set[str] = set()
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Assign):
            defined.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defined.add(node.target.id)

    refs: dict[str, int] = {}
    for node in ast.walk(cls):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self":
            if isinstance(node.ctx, ast.Store):
                defined.add(node.attr)
            else:
                refs.setdefault(node.attr, node.lineno)
        # setattr(self, "name", ...)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "setattr"
                and len(node.args) >= 2 and isinstance(node.args[0], ast.Name) and node.args[0].id == "self"
                and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str)):
            defined.add(node.args[1].value)
    return defined, refs


def _guarded_by_getattr_or_hasattr(tree: ast.Module) -> set[str]:
    """Имена, к которым код обращается через getattr(self, "x", ...) / hasattr(self, "x") — они опциональны."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in ("getattr", "hasattr") and len(node.args) >= 2
                and isinstance(node.args[0], ast.Name) and node.args[0].id == "self"
                and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str)):
            names.add(node.args[1].value)
    return names


def _real_tkinter():
    """Настоящий tkinter, даже если другой тест подменил sys.modules['tkinter'] заглушкой."""
    import importlib
    import sys
    mod = sys.modules.get("tkinter")
    if mod is not None and hasattr(mod, "Tk") and isinstance(getattr(mod, "Tk"), type):
        return mod
    saved = sys.modules.pop("tkinter", None)
    try:
        return importlib.import_module("tkinter")
    except ImportError:
        return None
    finally:
        if saved is not None:
            sys.modules["tkinter"] = saved


class TestConfigAppSelfReferences(unittest.TestCase):
    def test_every_self_reference_exists(self):
        tkinter = _real_tkinter()
        if tkinter is None:  # pragma: no cover
            self.skipTest("tkinter недоступен")
        tree = ast.parse(SRC.read_text(encoding="utf-8"))
        defined, refs = _collect(tree)
        optional = _guarded_by_getattr_or_hasattr(tree)
        missing = {
            name: line for name, line in refs.items()
            if name not in defined and name not in optional and not hasattr(tkinter.Tk, name)
        }
        self.assertEqual(missing, {}, "Ссылки на несуществующие методы/атрибуты (имя: строка): %s" % missing)

    def test_no_leftover_herdr_card_references(self):
        src = SRC.read_text(encoding="utf-8")
        for name in ("herdr_badge", "herdr_panes_lbl", "_build_herdr_card", "herdr_card"):
            self.assertNotIn(name, src)

    def test_aiwatcher_card_is_built_on_routes_page(self):
        tree = ast.parse(SRC.read_text(encoding="utf-8"))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == CLASS_NAME)
        routes = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_build_page_routes")
        calls = [n.func.attr for n in ast.walk(routes)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
        self.assertIn("_build_aiwatcher_card", calls)
        self.assertLess(calls.index("_build_aiwatcher_card"), calls.index("_build_gemini_card"))

    def test_main_starts_aiwatcher(self):
        tree = ast.parse(SRC.read_text(encoding="utf-8"))
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
        src = ast.unparse(main)
        self.assertIn("app.aiwatcher.start()", src)
        self.assertLess(src.index("app.aiwatcher.start()"), src.index("app.mainloop()"))


if __name__ == "__main__":
    unittest.main()
