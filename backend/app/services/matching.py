from __future__ import annotations

import sys
from importlib import import_module
from types import ModuleType
from typing import Any

_package_module: ModuleType = import_module('.matching', package=__package__)

# Re-export the public API to maintain compatibility with legacy imports that
# still reference `app.services.matching` directly.
if getattr(_package_module, '__all__', None):
    for _name in _package_module.__all__:
        globals()[_name] = getattr(_package_module, _name)

__all__ = list(getattr(_package_module, '__all__', []))


def __getattr__(name: str) -> Any:
    return getattr(_package_module, name)


def __dir__() -> list[str]:
    combined = set(__all__) | set(globals().keys()) | set(dir(_package_module))
    return sorted(combined)


# Ensure future imports of this module resolve to the package implementation.
sys.modules[__name__] = _package_module
