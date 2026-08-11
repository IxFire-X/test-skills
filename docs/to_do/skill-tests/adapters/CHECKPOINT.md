# Byte-preserving adapters checkpoint

Дата: 2026-08-11.

## Результат

Добавлены два optional installer:

- adapters/generic/install_skills.py — Python, --dry-run;
- adapters/windows/install.ps1 — PowerShell, -WhatIf.

Оба копируют ровно шесть canonical skill packages, исключают transient __pycache__/pyc, не меняют contracts/pipeline.json и не переписывают совпадающие destination files.

Test-first RED: 5 failed, 1 passed — оба installer отсутствовали. GREEN: 6 passed.

Permanent evidence создан только в этом каталоге:

- artifacts/install-generic/
- artifacts/install-windows/.

После package cleanup обе final-копии содержат 20 файлов. Повторный запуск каждого installer: copied=0, unchanged=20. Read-only manifest comparison подтвердил exact relative paths, SHA-256 и mtime_ticks для обеих destinations. Generic dry-run и PowerShell WhatIf не создали destination.

## Literal commands

Абсолютный cwd:

D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills

~~~json
["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","-m","pytest","tests\\test_adapters.py","-q","--tb=short"]
["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills\\adapters\\generic\\install_skills.py","--source","D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills\\skills","--destination","D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills\\docs\\to_do\\skill-tests\\adapters\\artifacts\\dry-run-must-not-exist","--dry-run"]
["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills\\adapters\\generic\\install_skills.py","--source","D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills\\skills","--destination","D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills\\docs\\to_do\\skill-tests\\adapters\\artifacts\\install-generic"]
["pwsh","-NoProfile","-File","D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills\\adapters\\windows\\install.ps1","-SkillPackRoot","D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills","-Destination","D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills\\docs\\to_do\\skill-tests\\adapters\\artifacts\\whatif-must-not-exist","-WhatIf"]
["pwsh","-NoProfile","-File","D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills\\adapters\\windows\\install.ps1","-SkillPackRoot","D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills","-Destination","D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills\\docs\\to_do\\skill-tests\\adapters\\artifacts\\install-windows"]
~~~
