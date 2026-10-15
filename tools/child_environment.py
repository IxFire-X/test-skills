"""The environment of the project's build, its tests and PIT, without secret-like variables (security review note 6).

The build tools, the generated tests and the mutation tool run the project's code; they never need
an access token of the operator's shell.  A variable is removed when its name looks like a secret
(``*TOKEN*``, ``*SECRET*``, ``*PASSWORD*``, ``*PASSWD*``, ``*API_KEY*``, ``*APIKEY*``, ``AWS_*``,
``AZURE_*``, ``GOOGLE_APPLICATION_CREDENTIALS``); the variables a build needs — ``PATH``, ``HOME``,
``JAVA_HOME``, ``TMP*``/``TEMP``, ``LANG``/``LC_*``, ``MAVEN_OPTS``, ``M2_HOME``, ``GRADLE_*`` and the
proxy variables — always stay.  The model CLI is not started with this environment: its credentials
are its own configuration.  Only names are ever logged, never values.
"""
from __future__ import annotations

import os
import re
from typing import Mapping

_SECRET_NAME = re.compile(r"TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|APIKEY|^AWS_|^AZURE_|^GOOGLE_APPLICATION_CREDENTIALS$")
_KEPT_NAME = re.compile(r"PATH|HOME|JAVA_HOME|TMP\w*|TEMP|LANG|LC_\w+|MAVEN_OPTS|M2_HOME|GRADLE_\w+|(?:HTTP|HTTPS|FTP|ALL|NO)_PROXY")


def _removed(name: str) -> bool:
    upper = name.upper()
    return _KEPT_NAME.fullmatch(upper) is None and _SECRET_NAME.search(upper) is not None


def removed_names(source: Mapping[str, str] | None = None) -> list[str]:
    """Names of the variables the build environment leaves out, sorted."""
    return sorted(name for name in (os.environ if source is None else source) if _removed(name))


def build_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """A copy of ``source`` (default: this process's environment) without secret-like variables."""
    return {name: value for name, value in (os.environ if source is None else source).items() if not _removed(name)}
