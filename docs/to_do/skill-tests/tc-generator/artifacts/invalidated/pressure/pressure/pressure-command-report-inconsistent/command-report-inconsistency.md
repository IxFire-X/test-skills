# Pressure command-report inconsistency

The evaluator reported exit 0 after reading `D:\AI-Projects\.worktrees\portable-testing-skills\schemas\tc-generator-output.schema.json`, but that literal path did not exist in the assigned worktree. The canonical schema instead resolves inside `test-orchestration-skills` and has the scenario-bound SHA-256 `69d1c235b35816a8ce4f40322d7795cf6610d4c8acbda89da1f5db78bf9edcaa`.

This pre-validator protocol evidence is internally impossible. The reserved generated output is preserved byte-for-byte solely as an unscored diagnostic; it was not validated, repaired, checker-processed, or semantically reviewed.
