# Финальный primary Sol review

Дата: 2026-08-11

## Граница независимости

Этот review выполнен primary Sol/High после implementation и E2E commits `6f83f8e57ac6fa5f24ea40f40126430f0346df0b`…`1780ddcfabc5b3b9d51a685a00c02b8dc4d2f513`. Он read-only относительно уже закоммиченных результатов, но не является требуемым design spec новым независимым Sol-agent: текущая orchestration policy не разрешала создать новую review-задачу. Поэтому документ не закрывает критерий fresh independent review и прямо сохраняет его как долг.

## Находки

### P1 — Python baseline уже заявленного Plan 3 scope

Plan 3 Task 3 требует bounded Part API model с authentication, positive, negative и boundary cases. Принятая цепочка проверяет шесть source-backed `parent/cascade/depth` случаев: positive и boundary, с project-native authenticated setup. Исходник дополнительно содержит invalid depth/parent и starred behavior, но они явно исключены входным контекстом. Это честный feature-slice PASS, а не доказательство полного Task 3 scope.

Требуемое изменение для полного acceptance: отдельная свежая Python-цепочка должна включить source-backed negative cases и хотя бы одно поддержанное authorization/denial решение либо точное source-based `N/A`.

### P1 — нет нового слепого forward/review цикла

`docs/to_do/forward-tests/` связывает ранее выполненные evaluator и real-project observations. Это сильное практическое evidence, но Task 6 требовал новых агентов без conversation history, а Task 7 — нового независимого Sol reviewer. Дополнительно formal tc-reviewer FINAL и orchestrate model-evaluator scorecards остаются pending.

Требуемое изменение для полного acceptance: один изолированный positive/pressure forward run на каждый skill и новый independent Sol review после него.

### P2 — межмодельная и языковая универсальность не доказана

Java и Python имеют execution evidence. PocketBase подтвердил статическую Go portability reviewer-а, но runtime был `BLOCKED` из-за отсутствия Go toolchain. TypeScript/Go поэтому остаются experimental. Текущие Codex/Sol/Terra/Luna observations нельзя экстраполировать на любую LLM.

### P3 — byte drift evidence найден и устранён

Первый финальный pytest обнаружил пять тестовых падений с одной причиной: в двух hash-bound tc-reviewer reports ранее удалили terminal LF. Commit `1780ddcfabc5b3b9d51a685a00c02b8dc4d2f513` восстановил исходные байты; metadata/observation не переписывались. Authoritative evidence suite после исправления: 201 passed; полный suite: 735 passed, 2 skipped.

## Подтверждённое качество

- Machine contract, schemas, generated docs, package shape и все шесть skill packages проходят проверки.
- Java: 7 ТК → 7 методов → targeted 7/7 → full 31/31 → trace PASS.
- Python feature slice: 6 ТК → 6 методов → targeted 6/6 → paired 17/17 → trace PASS.
- Empty subscription fixture честно даёт `NOT_RUNNABLE`, не PASS.
- JSON ТК всегда имеет проверенный lossless CSV companion.
- Generated tests имеют project-native setup, осмысленные assertions, точную TC mapping и не содержат secrets/placeholders/waits.
- Production/config/dependencies проектов не менялись ради прохождения тестов.

## Вердикт

Формальный verdict design-spec: `change` — два P1 acceptance gaps не позволяют объявить весь план завершённым.

Практический verdict: `approve for bounded Java/Python pilot transfer`. Текущий пакет можно переносить в другие проекты с fail-closed правилами и собирать реальные chain failures; нельзя обещать полное endpoint coverage без полного source context или одинаковую точность на любой LLM.
