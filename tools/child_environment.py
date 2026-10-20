"""The environment of the project's build, its tests and PIT, without secret-like variables (security review note 6).

The build tools, the generated tests and the mutation tool run the project's code; they never need
an access token of the operator's shell.  A variable is removed when its name looks like a secret
(``*TOKEN*``, ``*SECRET*``, ``*PASSWORD*``, ``*PASSWD*``, ``*API_KEY*``, ``*APIKEY*``, ``AWS_*``,
``AZURE_*``, ``GOOGLE_APPLICATION_CREDENTIALS``) — even when it starts like a build variable
(``GRADLE_PUBLISH_TOKEN``) — and a proxy variable whose value carries credentials (``user:pass@host``);
the other variables stay.  The model CLI gets its own allowlist (model_runner).  Only names and reasons
are ever logged, never values.
"""
from __future__ import annotations

import os
import re
from typing import Mapping

SECRET_NAME = re.compile(r"TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|APIKEY|^AWS_|^AZURE_|^GOOGLE_APPLICATION_CREDENTIALS$")
_SECRET_NAME = SECRET_NAME
_PROXY_NAME = re.compile(r"(?:HTTP|HTTPS|FTP|ALL|NO)_PROXY")


def _reason(name: str, value: str) -> str | None:
    """Why a variable stays out of the build, or None.  A secret-like name wins over every build name (S11 A3:
    ``GRADLE_PUBLISH_TOKEN``, ``TMP_TOKEN``); a proxy keeps its place unless its value carries credentials (``user:pass@``)."""
    upper = name.upper()
    if SECRET_NAME.search(upper) is not None:
        return "secret-name"
    if _PROXY_NAME.fullmatch(upper) is not None and "@" in str(value):
        return "proxy-credentials"
    return None


def removal_reasons(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """name → reason of every variable the build environment leaves out (never a value)."""
    return {name: reason for name, value in sorted((os.environ if source is None else source).items()) if (reason := _reason(name, value))}


def removed_names(source: Mapping[str, str] | None = None) -> list[str]:
    """Names of the variables the build environment leaves out, sorted."""
    return sorted(removal_reasons(source))


def build_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """A copy of ``source`` (default: this process's environment) without secret-like variables."""
    return {name: value for name, value in (os.environ if source is None else source).items() if _reason(name, value) is None}
