"""The id of a module of .skillsrc is the path of its folder (``api``, ``services/core``).

Hex of the folder name (``root--617069``) is kept for names the id cannot carry, and as the old form: ``--module`` still
takes it, with the warning MODULE_ID_LEGACY and the new id.  A ``.`` stays out of the plain set: ``modules.<id>.stack.language``
(the field of a question) is split on dots.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

_PLAIN = re.compile(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*")


def legacy_module_id(root: str) -> str:
    """The earlier id: ``root`` for the project folder, else ``root--`` and the hex of each path part."""
    if root == ".":
        return "root"
    return "root--" + "--".join(part.encode("utf-8").hex() for part in root.split("/"))


def module_id_for(root: str) -> str:
    """The current id of the module whose folder is ``root`` (a path from the project root, ``.`` for the root itself)."""
    if root == ".":
        return "root"
    if root == "root" or _PLAIN.fullmatch(root) is None:  # a folder named root would be the id of the project folder
        return legacy_module_id(root)
    return root


def _given(value: str) -> str:
    return value.strip().replace("\\", "/").rstrip("/")


def _root(module: Mapping[str, Any]) -> str | None:
    root = module.get("root")
    return root if isinstance(root, str) and root else None


def alias_match(modules: Sequence[Mapping[str, Any]], given: str) -> Mapping[str, Any] | None:
    """The one module whose current or old id (of its folder) is ``given``; none when there is no such, or several."""
    wanted = _given(given)
    found = [module for module in modules
             if (root := _root(module)) is not None and wanted in {module_id_for(root), legacy_module_id(root)}]
    return found[0] if len(found) == 1 else None


def select(modules: Sequence[Mapping[str, Any]], given: str) -> Mapping[str, Any] | None:
    """The module named by ``--module``: the exact id of .skillsrc first, then the id of its folder in either form."""
    exact = [module for module in modules if module.get("id") == given]
    if len(exact) == 1:
        return exact[0]
    return None if exact else alias_match(modules, given)


def legacy_warning(module: Mapping[str, Any], given: str | None) -> str | None:
    """MODULE_ID_LEGACY when ``--module`` is the hex form of a folder that now has a plainer id."""
    root = _root(module)
    if given is None or root is None:
        return None
    current, old = module_id_for(root), legacy_module_id(root)
    if _given(given) != old or old == current:
        return None
    return f"MODULE_ID_LEGACY: --module {given} — старая запись id; id этого модуля теперь {current}, пишите --module {current}"


def current_id(modules: Sequence[Mapping[str, Any]], given: str | None) -> str | None:
    """``given`` as the id the discovery of ``modules`` gives that module (``given`` itself when it names none)."""
    if given is None:
        return None
    module = select(modules, given)
    return str(module["id"]) if module is not None and isinstance(module.get("id"), str) else given
