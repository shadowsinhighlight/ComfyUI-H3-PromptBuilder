"""ComfyUI-H3-PromptBuilder

Submodules are loaded under a package name this file creates and registers
itself, rather than relying on the name ComfyUI happens to give the folder.

ComfyUI loads a custom node pack by full filesystem path and does not always
register that name in sys.modules before executing __init__.py. A relative
import such as `from .h3_refs import ...` then resolves to
"/workspace/.../ComfyUI-H3-PromptBuilder.h3_refs", which no finder can locate,
and the whole pack fails with a confusing ModuleNotFoundError. Creating our own
package entry sidesteps that entirely: the submodules' relative imports resolve
against a name that is guaranteed to exist and to have a __path__.
"""

import importlib.util
import os
import shutil
import sys
import traceback
from importlib.machinery import ModuleSpec

TAG = "[H3-PromptBuilder]"
HERE = os.path.dirname(os.path.abspath(__file__))
PKG = "comfyui_h3_promptbuilder"

# Bytecode from a previous version is a real hazard: extracting a release
# preserves the archive's file times, so an old .pyc can be newer than the .py
# beside it and get loaded instead — including imports of modules this version
# no longer ships. Clearing it costs one recompile.
try:
    _cache = os.path.join(HERE, "__pycache__")
    if os.path.isdir(_cache):
        shutil.rmtree(_cache, ignore_errors=True)
except Exception:
    pass

if PKG not in sys.modules:
    _spec = ModuleSpec(PKG, None, is_package=True)
    _pkg = importlib.util.module_from_spec(_spec)
    _pkg.__path__ = [HERE]
    sys.modules[PKG] = _pkg

# Belt and braces: the synthetic package above makes relative imports resolve,
# and this makes plain `import h3_refs` resolve too. Either mechanism alone is
# enough; having both means a submodule's import style cannot break the pack.
if HERE not in sys.path:
    sys.path.append(HERE)

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}
_FAILED = []


def _load(name, required=True):
    """Load one submodule as PKG.<name>, so its own relative imports work."""
    full = f"{PKG}.{name}"
    if full in sys.modules:
        mod = sys.modules[full]
    else:
        try:
            spec = importlib.util.spec_from_file_location(
                full, os.path.join(HERE, f"{name}.py")
            )
            if spec is None or spec.loader is None:
                raise ImportError(f"{name}.py not found in {HERE}")
            mod = importlib.util.module_from_spec(spec)
            # registered before exec so intra-package imports can find it
            sys.modules[full] = mod
            spec.loader.exec_module(mod)
            # bind onto the package, as a normal import would
            setattr(sys.modules[PKG], name, mod)
        except Exception:
            sys.modules.pop(full, None)
            _FAILED.append(name)
            print(f"{TAG} failed to load {name}.py:")
            traceback.print_exc()
            if required:
                print(f"{TAG} nodes from {name}.py will be unavailable.")
            return None

    NODE_CLASS_MAPPINGS.update(getattr(mod, "NODE_CLASS_MAPPINGS", {}) or {})
    NODE_DISPLAY_NAME_MAPPINGS.update(
        getattr(mod, "NODE_DISPLAY_NAME_MAPPINGS", {}) or {}
    )
    return mod


_load("h3_refs")
_load("h3_nodes")
_load("h3_studio")
_load("h3_server", required=False)   # upload and preset routes; optional

if _FAILED:
    print(f"{TAG} loaded with {len(_FAILED)} failed module(s): {', '.join(_FAILED)}")
else:
    print(f"{TAG} loaded {len(NODE_CLASS_MAPPINGS)} nodes.")

WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
