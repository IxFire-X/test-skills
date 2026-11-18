"""V0: the byte budget of each role — its SKILL.md plus its references (and assets) — as the agent at work reads them.

``LIMIT[role]`` is the size at the time of V0 (the measure of ``tools.step_size.role_files``, line ends read as ``\n``) plus a margin of
``min(5 %, 3 KB)``.  A role that grows over its limit fails ``test_role_bytes`` in every environment — the budgets of ``test_review_view``
and ``test_skeleton_budget`` only run where the Petclinic night run is on disk.  Raising a limit is a decision: the commit says why."""
from __future__ import annotations

# role: (bytes at V0, limit = bytes + min(5 %, 3 KB))
BUDGET = {
    "autotest-reviewer": (13918, 14613),  # texts1: +877 (13 041 → 13 918; a step: framework imports against the build file; a wrong import is a compile error); was (12474, 13097)
    "context-marker": (15757, 16544),  # tails13: +796 over 14 961 (the table rule 5 %): a plain document is no OpenSpec — a requirement per numbered statement, build_context only for its digest (the texts2 sentence about build_context sent three agents of three to its split by headings: 10 requirements of 35 and the set accepted); the answer file lies in the run. upd-b2p2: limit +751 (14 958 → 15 709, the table rule 5 %; the verdicts fits / rewrite / obsolete of an import with documents, +663 B on 14 298). texts1-b: +606 (13 640 → 14 246; a free sentence of disagreement, a sample of a requirement)
    "orchestrate": (60832, 63832),  # v0.7: +3 274 over 57 558 (the limit +3 000, the 3 KB cap of the rule): q2 — a sample of an HTTP step in fragment-sample.md (2 699 B: SEMANTIC_TARGET_TYPE ×114 on the big run without it), q3b — the without_case line of sections.md (575 B: the requirements a run of a section left without an accepted case, and what to do); each was measured on d56b9a4e without the other. Before: sections: +1 822 over 55 736 (the measure had 40 B of margin; references/sections.md, 1 796 B: a document told apart by sections, one request — where the runs stand by the journals, not by the memory of the agent, and the one CSV; the line of SKILL.md that said "no ceiling on the requirements" went, and the sentence on exclusion_summary stays in scan.md only). tails11: +1 895 (51 225 → 53 120; the limit +1 990: a sample of an imported case in fragment-sample.md, 2 162 B, less the 9 B impin took off the line of the plan views — the agent put together the machine part of 65 imported cases blind). texts1-b: +504 (50 721 → 51 225; the evidence key only in the case review, no generator output for the reviewer, the base of a relative --reports). Before: V8-W2b: SKILL 15 884 + references 32 455 (commands of both CLIs, role files, review outcome); V8-W2 was (46408, 48728)
    "tc-generator": (36219, 38029),  # texts1-b: +430 (35 789 → 36 219; rework identity, shared requirements, the design of a split requirement)
    "tc-reviewer": (20359, 21376),  # texts1-b: +67 (20 292 → 20 359; no generator output)
    "tc-to-autotest": (20327, 21343),  # texts1: +288 (18 312 → 18 600; framework imports); texts1-b: +1 727 (18 600 → 20 327; a sample of the answer, 1 632 B, a pointer to it and a digest note); was (17470, 18343)
}
LIMIT = {role: limit for role, (_measured, limit) in BUDGET.items()}
