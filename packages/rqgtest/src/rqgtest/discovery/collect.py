"""Discover a complete test root using fixed naming and inheritance contracts."""
from __future__ import annotations
import ast
import hashlib
import importlib.util
import inspect
from pathlib import Path
import sys
from ..core.case import ITestCase, TestCase, StatefulTestCase

_ROOTS = (TestCase, StatefulTestCase)
_ALLOWED = {"get_enterpoint", "get_input_data", "get_output_data", "equals", "__init__"}
# Source files below data-only roots are never imported as executable test modules.
_DATA_DIRS = frozenset({"cases"})
_LOADER_BASES = frozenset({"DataLoader", "JSONDataLoader", "JSONLDataLoader", "CSVDataLoader", "PickleDataLoader"})



def _inspect_module(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = False
    loader_count = 0
    loader_bases = _imported_loader_bases(tree)
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        if (isinstance(node, ast.ClassDef) and len(node.bases) == 1
                and isinstance(node.bases[0], ast.Name)
                and node.bases[0].id in loader_bases):
            loader_count += 1
            if loader_count > 1:
                raise ValueError(f"At most one local DataLoader subclass per test module: {path}")
            continue
        if isinstance(node, ast.ClassDef) and len(node.bases) == 1 and isinstance(node.bases[0], ast.Name) and node.bases[0].id in {r.__name__ for r in _ROOTS}:
            found = True
            if not node.name.endswith("Case"):
                raise ValueError(f"Case name must end with Case: {path}:{node.lineno}")
            for member in node.body:
                if isinstance(member, ast.FunctionDef) and member.name in _ALLOWED:
                    continue
                if isinstance(member, ast.Expr) and isinstance(member.value, ast.Constant) and isinstance(member.value.value, str):
                    continue
                raise ValueError(f"Nonstandard Case member: {path}:{member.lineno}")
            continue
        raise ValueError(f"Test root may contain only imports and direct Cases: {path}:{node.lineno}")
    if not found and not loader_count:
        raise ValueError(f"No direct Case or DataLoader in {path}")


def _imported_loader_bases(tree: ast.Module) -> set[str]:
    bases: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module in ("rqgtest", "rqgtest.data.loader"):
            bases.update(alias.asname or alias.name for alias in node.names
                         if alias.name in _LOADER_BASES)
    return bases


def _inspect_dataloader_module(path: Path) -> None:
    """Require one actual built-in DataLoader descendant, without module helpers.

    A loader module may contain imports and one Loader class, but no free
    functions, executable module assignments, or TestCase declarations.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported_bases = _imported_loader_bases(tree)
    classes: list[ast.ClassDef] = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            continue
        if isinstance(node, ast.Import):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        if isinstance(node, ast.ClassDef):
            classes.append(node)
            continue
        raise ValueError(f"DataLoader module may contain only imports and one class: {path}:{node.lineno}")
    if len(classes) != 1:
        raise ValueError(f"Exactly one DataLoader subclass required: {path}")
    cls = classes[0]
    if (len(cls.bases) != 1 or not isinstance(cls.bases[0], ast.Name)
            or cls.bases[0].id not in imported_bases):
        raise ValueError(f"DataLoader subclass must directly extend an imported standard loader: {path}:{cls.lineno}")


def _is_data_file(path: Path, root: Path) -> bool:
    """Data fixtures can include deliberately invalid Python source samples."""
    return path.relative_to(root).parts[0] in _DATA_DIRS


def _is_loader_file(path: Path, root: Path) -> bool:
    return path.relative_to(root).parts[0] == "dataloaders"

def discover(root: Path, *, selected_files: tuple[Path, ...] | None = None):
    if not root.is_dir():
        raise ValueError(f"Test root missing: {root}")
    # Targeted discovery imports only selected modules, retaining full
    # validation for every selected module. Full runs validate the whole root.
    # Full discovery validates all loader modules, even when no Case imports
    # them. Selected discovery retains its existing isolation semantics.
    if selected_files is None:
        for path in sorted(root.rglob("*.py")):
            if _is_loader_file(path, root) and path.name != "__init__.py":
                _inspect_dataloader_module(path)
    if selected_files is not None:
        if not selected_files:
            raise ValueError("Selected test files may not be empty")
        modules = sorted({path.resolve() for path in selected_files})
        for path in modules:
            if (not path.is_relative_to(root.resolve()) or not path.is_file()
                    or not path.name.startswith("test_") or path.suffix != ".py"
                    or _is_loader_file(path, root) or _is_data_file(path, root)):
                raise ValueError(f"Invalid selected test module: {path}")
        markers = set()
        for path in modules:
            parent = path.parent
            while parent.is_relative_to(root.resolve()):
                markers.add(parent / "__init__.py")
                if parent == root.resolve():
                    break
                parent = parent.parent
    else:
        modules = sorted(x for x in root.rglob("*.py")
                         if x.name != "__init__.py"
                         and not _is_loader_file(x, root)
                         and not _is_data_file(x, root))
        markers = {x for x in root.rglob("__init__.py")
                   if not _is_data_file(x, root)}
    if not modules:
        raise ValueError(f"No Python test cases in {root}")
    for marker in sorted(markers):
        if marker.is_file() and marker.read_text(encoding="utf-8").strip():
            raise ValueError(f"Executable package marker forbidden in test root: {marker}")
    # Import the project-specific loader package through its test root.
    # This supports `from dataloaders.foo import FooLoader` on every platform.
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    cases = []
    hashes = {}
    for index, path in enumerate(modules):
        if not path.name.startswith("test_"):
            raise ValueError(f"Non-test Python module in test root: {path}")
        _inspect_module(path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        hashes[path.relative_to(root).as_posix()] = digest
        spec = importlib.util.spec_from_file_location(f"rqgtest_case_{index}", path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load test source {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        for cls in sorted((val for val in vars(module).values() if inspect.isclass(val) and val.__module__ == spec.name and issubclass(val, ITestCase)), key=lambda c: c.__name__):
            if len(cls.__bases__) != 1 or cls.__bases__[0] not in _ROOTS:
                raise ValueError(f"Non-direct Case inheritance: {cls.__name__}")
            case = cls()
            cases.append((f"{path.relative_to(root)}::{cls.__name__}", case))
    if not cases:
        raise ValueError("No test cases discovered")
    return cases, hashes
