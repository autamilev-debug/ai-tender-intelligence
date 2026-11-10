"""Detect missing production imports and compare the installed wheel to its source."""

import argparse
import ast
from importlib import metadata
from importlib.util import find_spec, resolve_name
from pathlib import Path
import symtable
import sys
import tomllib


PACKAGE = "ai_tender_intelligence"
ROOT = Path(__file__).resolve().parents[1]
ASSETS = Path("share/ai-tender-intelligence")
RESOURCE_DIRS = (
    "prompts", "reference-data", "migrations", "discovery_migrations",
    "document_migrations", "extraction_migrations",
)
RESOURCE_SUFFIXES = {".md", ".json", ".xml", ".py", ".mako", ".sql"}


def modules(package_dir):
    return {
        ".".join(path.relative_to(package_dir.parent).with_suffix("").parts).removesuffix(".__init__"): path
        for path in package_dir.rglob("*.py")
    }


def internal(name):
    return name == PACKAGE or name.startswith(PACKAGE + ".")


def literal_string(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else ""


def check_imports(sources):
    """Check direct, relative and literal dynamic references without importing the app."""
    code = {name: path.read_text(encoding="utf-8") for name, path in sources.items()}
    symbols = {
        name: {
            symbol.get_name() for symbol in symtable.symtable(value, str(sources[name]), "exec").get_symbols()
            if symbol.is_assigned() or symbol.is_imported()
        }
        for name, value in code.items()
    }
    missing = set()
    for name, path in sources.items():
        package = name if path.name == "__init__.py" else name.rpartition(".")[0]
        for node in ast.walk(ast.parse(code[name], filename=str(path))):
            required = []
            if isinstance(node, ast.Import):
                required = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                target = node.module or ""
                if node.level:
                    target = resolve_name("." * node.level + target, package)
                required.append(target)
                if internal(target) and target in symbols:
                    required.extend(
                        target + "." + alias.name for alias in node.names
                        if alias.name != "*" and alias.name not in symbols[target]
                    )
            elif isinstance(node, (ast.List, ast.Tuple)):
                for flag, value in zip(node.elts, node.elts[1:]):
                    target = literal_string(value)
                    if literal_string(flag) == "-m" and internal(target):
                        required.append(target)
                        if target in sources and sources[target].name == "__init__.py":
                            required.append(target + ".__main__")
            elif isinstance(node, ast.Call) and node.args:
                function = node.func
                callee = getattr(function, "id", getattr(function, "attr", ""))
                if callee in {"__import__", "import_module"}:
                    required.append(literal_string(node.args[0]))
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                target, separator, attribute = node.value.partition(":")
                if separator and internal(target) and attribute.isidentifier():
                    required.append(target)
                    if target in symbols and attribute not in symbols[target]:
                        missing.add(f"{path}:{node.lineno}: {target}:{attribute}")
            for target in required:
                if internal(target) and target not in sources:
                    missing.add(f"{path}:{node.lineno}: {target}")
    if missing:
        raise ValueError("Missing internal runtime dependencies:\n" + "\n".join(sorted(missing)))


def check(root, *, installed=False):
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    sources = modules(root / "src" / PACKAGE)
    if PACKAGE not in sources:
        raise ValueError("Missing application package")
    check_imports(sources)

    assets = config["tool"]["setuptools"]["data-files"]
    bundled = {
        root / name: Path(folder) / Path(name).name
        for folder, files in assets.items()
        for name in files
    }
    expected = {root / "alembic.ini"} | {
        path for folder in RESOURCE_DIRS for path in (root / folder).rglob("*")
        if path.is_file() and path.suffix in RESOURCE_SUFFIXES
    }
    missing = {
        source for source, destination in bundled.items()
        if not source.is_file() or destination != ASSETS / source.relative_to(root)
    } | (expected - bundled.keys())
    if missing:
        names = ", ".join(sorted(path.relative_to(root).as_posix() for path in missing))
        raise ValueError("Missing or unbundled runtime resources: " + names)

    scripts = config["project"]["scripts"]
    for name, entry in scripts.items():
        target, function = entry.split(":")
        if target not in sources:
            raise ValueError(f"Missing console entrypoint {name}: {entry}")
        tree = ast.parse(sources[target].read_text(encoding="utf-8"))
        if not any(isinstance(node, ast.FunctionDef) and node.name == function for node in tree.body):
            raise ValueError(f"Missing console entrypoint {name}: {entry}")

    if installed:
        spec = find_spec(PACKAGE)
        prefix = Path(sys.prefix).resolve()
        if spec is None or spec.origin is None or not Path(spec.origin).resolve().is_relative_to(prefix):
            raise ValueError("Expected a wheel installed in the verification environment")
        packaged = modules(Path(spec.origin).parent)
        absent_modules = sorted(sources.keys() - packaged.keys())
        if absent_modules:
            raise ValueError("Missing installed modules: " + ", ".join(absent_modules))
        absent_assets = sorted(path.as_posix() for path in bundled.values() if not (prefix / path).is_file())
        if absent_assets:
            raise ValueError("Missing installed resources: " + ", ".join(absent_assets))
        entries = {
            entry.name: entry.value
            for entry in metadata.distribution("ai-tender-intelligence").entry_points
            if entry.group == "console_scripts"
        }
        if entries != scripts:
            raise ValueError("Installed console entrypoints differ from pyproject.toml")

    print(f"Checked {len(sources)} source modules and {len(bundled)} runtime resources"
          + (" against the installed wheel" if installed else ""))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed", action="store_true", help="Compare source and installed wheel contents")
    args = parser.parse_args()
    try:
        check(ROOT, installed=args.installed)
    except (ValueError, ImportError) as error:
        sys.exit(str(error))
