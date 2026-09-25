"""Load the tokenizer contract without importing the Open WebUI application.

Deployment preflights run in the deliberately small Plandex runtime image,
where the web application's optional Python dependencies are not installed.
Importing ``open_webui`` would execute its package initializer before reaching
the dependency-free tokenizer module.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def load_tokenizer() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / 'backend/open_webui/control_plane/plandex_tokenizer.py'
    spec = importlib.util.spec_from_file_location('_plandex_tokenizer_runtime', path)
    if spec is None or spec.loader is None:
        raise RuntimeError('unable to load Plandex tokenizer contract')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tokenizer = load_tokenizer()
