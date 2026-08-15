"""
Zero-dependency test runner.

These are ordinary pytest tests — `pytest` from the project root is the
normal way to run them, and does more (better diffs, filtering, plugins).
This runner exists so the suite is *always* runnable with nothing installed
beyond what the dashboard already needs, which matters for two reasons:

1. Anyone who downloaded the folder can sanity-check their install without
   adding a dev dependency.
2. Verification shouldn't depend on being able to reach PyPI.

It supports the small subset of pytest actually used here: module-level
`test_*` functions, `Test*` classes with `test_*` methods, and fixture
injection by parameter name (function-scoped, resolved per test).

Usage:  python tests/run_tests.py [-v]
"""
import importlib.util
import inspect
import sys
import traceback
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(TESTS_DIR.parent))

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    return module


def collect_fixtures(conftest) -> dict:
    return {
        name: obj
        for name, obj in vars(conftest).items()
        if callable(obj) and getattr(obj, "_is_fixture", False)
    }


def resolve(func, fixtures, cache):
    """Build kwargs for `func` by resolving each parameter name to a fixture,
    recursively (fixtures may depend on other fixtures). Cached per test so
    two params asking for the same fixture get the same object, matching
    pytest's function-scope behavior."""
    kwargs = {}
    for name in inspect.signature(func).parameters:
        if name == "self":
            continue
        if name not in fixtures:
            raise KeyError(f"no fixture named '{name}'")
        if name not in cache:
            cache[name] = fixtures[name](**resolve(fixtures[name], fixtures, cache))
        kwargs[name] = cache[name]
    return kwargs


def iter_tests(module):
    """Yield (label, callable) for every test in a module."""
    for name, obj in sorted(vars(module).items()):
        if name.startswith("test_") and inspect.isfunction(obj):
            yield name, obj
        elif name.startswith("Test") and inspect.isclass(obj):
            instance = obj()
            # `dir()`, not `vars()`. `vars()` returns only the class's own
            # __dict__, so any test method inherited from a shared base class
            # was silently never collected — and silently is the problem: the
            # suite reported a healthy count while skipping them entirely.
            #
            # That is exactly how the store's contract tests behaved. Seven
            # shared assertions, written once and meant to run against both
            # backends, ran against neither. Mutation testing is what exposed
            # it: breaking FileStore's duplicate check changed nothing.
            for meth_name in sorted(dir(obj)):
                if not meth_name.startswith("test_"):
                    continue
                if not inspect.isfunction(getattr(obj, meth_name, None)):
                    continue
                yield f"{name}::{meth_name}", getattr(instance, meth_name)


def main() -> int:
    verbose = "-v" in sys.argv
    conftest = load_module(TESTS_DIR / "conftest.py")
    fixtures = collect_fixtures(conftest)

    passed, failures = 0, []
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        module = load_module(path)
        print(f"\n{DIM}{path.name}{RESET}")
        for label, func in iter_tests(module):
            try:
                func(**resolve(func, fixtures, {}))
            except Exception:
                failures.append((f"{path.name}::{label}", traceback.format_exc()))
                print(f"  {RED}FAIL{RESET} {label}")
            else:
                passed += 1
                if verbose:
                    print(f"  {GREEN}pass{RESET} {label}")

    print()
    for name, tb in failures:
        print(f"{RED}{'=' * 70}\nFAILED {name}{RESET}\n{tb}")

    total = passed + len(failures)
    color = GREEN if not failures else RED
    print(f"{color}{passed}/{total} passed{RESET}" + (f", {len(failures)} failed" if failures else ""))

    # The UI modules get their shared state injected at runtime (see
    # runtime.py), so a missing value can't be caught by importing them.
    # This static check covers that gap, and belongs with the test run
    # rather than as a separate step someone has to remember.
    binding_ok = run_binding_check()
    return 1 if (failures or not binding_ok) else 0


def run_binding_check() -> bool:
    checker = TESTS_DIR.parent / "tools" / "check_bindings.py"
    if not checker.exists():
        return True
    import subprocess

    result = subprocess.run(
        [sys.executable, str(checker)], capture_output=True, text=True
    )
    output = (result.stdout + result.stderr).strip()
    color = GREEN if result.returncode == 0 else RED
    print(f"{color}{output}{RESET}")
    return result.returncode == 0


if __name__ == "__main__":
    raise SystemExit(main())
