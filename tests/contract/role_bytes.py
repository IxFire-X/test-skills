"""V0: the byte budget of each role — its SKILL.md plus its references (and assets) — as the agent at work reads them.

``LIMIT[role]`` is the size at the time of V0 (the measure of ``tools.step_size.role_files``, line ends read as ``\n``) plus a margin of
``min(5 %, 3 KB)``.  A role that grows over its limit fails ``test_role_bytes`` in every environment — the budgets of ``test_review_view``
and ``test_skeleton_budget`` only run where the Petclinic night run is on disk.  Raising a limit is a decision: the commit says why."""
from __future__ import annotations

# role: (bytes at V0, limit = bytes + min(5 %, 3 KB))
BUDGET = {
    "autotest-reviewer": (12474, 13097),
    "context-marker": (13660, 14343),
    "orchestrate": (90298, 93298),
    "tc-generator": (35783, 37572),
    "tc-reviewer": (19349, 20316),
    "tc-to-autotest": (17470, 18343),
}
LIMIT = {role: limit for role, (_measured, limit) in BUDGET.items()}
