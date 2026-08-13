# Automatic `.skillsrc` Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Автоматически обнаруживать все модули проекта, безопасно создавать или проверять корневой `.skillsrc` версии 3 и передавать выбранный модуль в тестовую цепочку без догадок о критических значениях.

**Architecture:** Отдельный read-only инвентаризатор формирует доказательный JSON-отчёт; общий загрузчик нормализует `.skillsrc` версий 2 и 3; отдельный инициализатор компилирует, согласует и атомарно записывает YAML. Оркестратор вызывает инициализатор до `context-marker`, останавливается на критических вопросах и передаёт явный `module_id` средству запуска.

**Tech Stack:** Python 3.10+, стандартная библиотека, PyYAML 6.x, jsonschema 4.x, JSON Schema Draft 2020-12, `unittest`.

## Global Constraints

- Не добавлять новых runtime-зависимостей сверх `PyYAML>=6,<7` и `jsonschema>=4.23,<5`.
- Обнаружение не выполняет код, сборку, тесты, менеджеры пакетов или контейнеры целевого проекта.
- Не читать `.env`, ключи, токены, каталоги зависимостей, кеши и сборочные результаты.
- Все сохраняемые пути относительно корня проекта; абсолютные, UNC, drive-relative и traversal-пути отклоняются.
- При критической неоднозначности вернуть структурированный вопрос и не создавать или изменять `.skillsrc`.
- Некритические неизвестные значения не угадывать: поле пропустить и записать предупреждение.
- Один `.skillsrc` версии 3 описывает все самостоятельные модули монорепозитория.
- `.skillsrc` хранит пути к источникам фич, но не копирует содержание фич и требований.
- Существующий `.skillsrc` версии 2 продолжает работать как один логический модуль.
- Перед каждой цепочкой выполнять быстрый структурный контроль актуальности.
- Без подтверждения не удалять и не заменять существующие пользовательские значения.
- Запись `.skillsrc` атомарна; при ошибке исходные байты сохраняются.
- Автоматическая операция не выполняет `git add`, commit или push в целевом проекте.
- Постоянные диагностические JSON-артефакты размещать только под `docs/to_do/` целевого проекта.

## File Map

### Новые файлы

- `tools/skillsrc_manifest.py` — загрузка YAML, schema validation, нормализация v2/v3, выбор модуля и безопасное разрешение корня.
- `tools/stack_catalog.py` — единый каталог имён манифестов и маркеров стеков для адресного и полного сканеров.
- `tools/discover_project.py` — read-only инвентаризация всего проекта и JSON CLI.
- `tools/init_skillsrc.py` — компиляция, согласование, проверка актуальности и атомарная запись `.skillsrc`.
- `schemas/project-discovery-output.schema.json` — контракт отчёта инвентаризации.
- `schemas/skillsrc-init-output.schema.json` — контракт результата инициализации/проверки.
- `tests/__init__.py` — пакет stdlib-тестов.
- `tests/test_skillsrc_manifest.py` — v2/v3, выбор модуля и path confinement.
- `tests/test_discover_project.py` — одностековые проекты, монорепозиторий, неоднозначности, секреты и детерминизм.
- `tests/test_init_skillsrc.py` — создание, согласование, атомарность, fingerprint и идемпотентность.
- `tests/test_run_tests_skillsrc_v3.py` — выбор module root и совместимость `run_tests.py`.
- `tests/test_orchestrator_skillsrc_bootstrap.py` — обязательный preflight и end-to-end bootstrap-контракт.

### Изменяемые файлы

- `schemas/skillsrc.schema.json` — совместимые ветки v2 и v3.
- `tools/scan_project.py` — импорт общего каталога стеков, честное read-only описание и удаление неиспользуемого writer-кода.
- `tools/run_tests.py` — общий загрузчик `.skillsrc`, `--module` и module-root execution context.
- `tools/doctor.py` — проверка новых обязательных tools/schemas.
- `skills/orchestrate/SKILL.md` — bootstrap до `context-marker`, вопросы и выбор модуля.
- `skills/orchestrate/references/orchestration-contract.md` — точные команды и stop/continue semantics.
- `.skillsrc.example` — канонический пример версии 3.
- `README.md`, `USAGE.md`, `HOW-IT-WORKS.md` — автоматическая инициализация, монорепозитории и ручной override.

---

### Task 1: Совместимая schema v3 и общий загрузчик `.skillsrc`

**Files:**
- Modify: `schemas/skillsrc.schema.json`
- Create: `tools/skillsrc_manifest.py`
- Create: `tests/__init__.py`
- Create: `tests/test_skillsrc_manifest.py`

**Interfaces:**
- Produces: `SkillsrcError(code: str, details: list[dict[str, object]])`.
- Produces: `load_skillsrc(path: Path, schema_path: Path | None = None) -> dict[str, Any]`.
- Produces: `normalize_skillsrc(document: Mapping[str, Any]) -> dict[str, Any]` returning a mapping with exact keys `project` and `modules`, where `modules` is a nonempty list of normalized module mappings.
- Produces: `select_module(normalized: Mapping[str, Any], module_id: str | None) -> dict[str, Any]`.
- Produces: `resolve_module_root(project_root: Path, module: Mapping[str, Any]) -> Path`.
- Consumed by: Tasks 3, 4 and 5.

- [ ] **Step 1: Создать focused-тесты v2/v3 и безопасных путей**

```python
# tests/test_skillsrc_manifest.py
import tempfile
import unittest
from pathlib import Path

import yaml

from tools.skillsrc_manifest import (
    SkillsrcError,
    load_skillsrc,
    normalize_skillsrc,
    resolve_module_root,
    select_module,
)


class SkillsrcManifestTests(unittest.TestCase):
    def _write(self, root: Path, value: dict) -> Path:
        path = root / ".skillsrc"
        path.write_text(yaml.safe_dump(value, sort_keys=False, allow_unicode=True), encoding="utf-8")
        return path

    def test_v2_normalizes_to_one_root_module(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = self._write(root, {
                "version": "2.0",
                "project": {"name": "api", "language": "python", "framework": "fastapi", "build_tool": "pip"},
                "paths": {"source": "src", "tests": "tests"},
                "test": {"framework": "pytest"},
            })
            normalized = normalize_skillsrc(load_skillsrc(path))
            self.assertEqual([module["id"] for module in normalized["modules"]], ["root"])
            self.assertEqual(normalized["modules"][0]["stack"]["language"], "python")

    def test_v3_requires_explicit_module_when_multiple_exist(self):
        document = {
            "version": "3.0",
            "project": {"name": "platform"},
            "discovery": {"on_missing": "automatic", "conflict_policy": "ask_user"},
            "modules": [
                {"id": "api", "root": "services/api", "stack": {"language": "python", "build_tool": "pip"}, "detected_from": ["services/api/pyproject.toml"]},
                {"id": "web", "root": "apps/web", "stack": {"language": "typescript", "build_tool": "npm"}, "detected_from": ["apps/web/package.json"]},
            ],
        }
        normalized = normalize_skillsrc(document)
        with self.assertRaisesRegex(SkillsrcError, "module selection is required"):
            select_module(normalized, None)
        self.assertEqual(select_module(normalized, "web")["root"], "apps/web")

    def test_module_root_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(SkillsrcError, "unsafe module root"):
                resolve_module_root(Path(temp), {"id": "bad", "root": "../outside"})
```

- [ ] **Step 2: Запустить тест и подтвердить ожидаемый RED**

Run: `python -m unittest tests.test_skillsrc_manifest -v`

Expected: FAIL с `ModuleNotFoundError: No module named 'tools.skillsrc_manifest'`.

- [ ] **Step 3: Расширить schema двумя взаимоисключающими ветками**

В `schemas/skillsrc.schema.json` определить верхнеуровневый `oneOf`:

```json
{
  "oneOf": [
    {"$ref": "#/$defs/v2"},
    {"$ref": "#/$defs/v3"}
  ],
  "$defs": {
    "v2": {
      "type": "object",
      "required": ["project"],
      "properties": {
        "version": {"type": "string", "not": {"const": "3.0"}},
        "project": {"$ref": "#/$defs/v2_project"}
      }
    },
    "v3": {
      "type": "object",
      "required": ["version", "project", "discovery", "modules"],
      "properties": {
        "version": {"const": "3.0"},
        "project": {"$ref": "#/$defs/v3_project"},
        "discovery": {"$ref": "#/$defs/discovery"},
        "modules": {"type": "array", "minItems": 1, "items": {"$ref": "#/$defs/module"}}
      },
      "additionalProperties": false
    }
  }
}
```

Сохранить все существующие v2-поля и enums. Для v3 зафиксировать:

- `project.name` — required; `project.methodology` — optional enum `sdd|tdd|bdd|mixed`;
- `discovery.on_missing=automatic`, `discovery.conflict_policy=ask_user`;
- module required: `id`, `root`, `stack.language`, `detected_from`;
- `stack.language`: `java|python|go|typescript|kotlin`;
- optional `framework`; optional `build_tool` из существующего enum;
- `paths.source|tests|resources`: unique arrays непустых строк;
- `test.framework` из существующего enum;
- `test.wrapper.windows|linux`: непустые строки;
- `feature_sources.requirements|openapi|architecture|source`: unique arrays строк;
- `detected_from`: непустой unique array строк;
- v3 также сохраняет top-level `resolution`, `contracts`, `skills_registry` через существующие definitions.

- [ ] **Step 4: Реализовать загрузку, нормализацию и выбор модуля**

```python
# tools/skillsrc_manifest.py
class SkillsrcError(ValueError):
    def __init__(self, code: str, message: str, details: list[dict[str, object]] | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or []


def load_skillsrc(path: Path, schema_path: Path | None = None) -> dict[str, Any]:
    schema_path = schema_path or Path(__file__).resolve().parents[1] / "schemas" / "skillsrc.schema.json"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise SkillsrcError("invalid_shape", ".skillsrc must be a mapping")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(document), key=lambda item: list(item.absolute_path))
    if errors:
        raise SkillsrcError("schema_invalid", ".skillsrc failed schema validation", [
            {"path": "/" + "/".join(map(str, error.absolute_path)), "message": error.message}
            for error in errors
        ])
    return document


def select_module(normalized: Mapping[str, Any], module_id: str | None) -> dict[str, Any]:
    modules = list(normalized["modules"])
    if module_id is None and len(modules) == 1:
        return modules[0]
    if module_id is None:
        raise SkillsrcError("module_required", "module selection is required")
    matches = [module for module in modules if module["id"] == module_id]
    if len(matches) != 1:
        raise SkillsrcError("module_unknown", f"unknown module: {module_id}")
    return matches[0]
```

`normalize_skillsrc` преобразует v2 в модуль `id: root`, `root: .`, переносит `project.language|framework|build_tool` в `stack`, строки `paths` в одноэлементные массивы и top-level `sdd` в `feature_sources`. V3 копируется в нормализованную структуру без потери пользовательских полей.

`resolve_module_root` использует `Path.resolve()` и `os.path.commonpath`; абсолютный, UNC, drive-relative или содержащий `..` путь отклоняется до доступа к файловой системе.

- [ ] **Step 5: Запустить focused-тесты**

Run: `python -m unittest tests.test_skillsrc_manifest -v`

Expected: PASS для v2 normalization, v3 selection, schema negatives и confinement.

- [ ] **Step 6: Проверить schema metaschema и format**

Run: `python -m json.tool schemas/skillsrc.schema.json`

Expected: exit 0.

Run: `git diff --check -- schemas/skillsrc.schema.json tools/skillsrc_manifest.py tests/__init__.py tests/test_skillsrc_manifest.py`

Expected: exit 0.

- [ ] **Step 7: Commit**

```bash
git add schemas/skillsrc.schema.json tools/skillsrc_manifest.py tests/__init__.py tests/test_skillsrc_manifest.py
git commit -m "feat: add modular skillsrc manifest model"
```

---

### Task 2: Детерминированная инвентаризация всех модулей

**Files:**
- Create: `tools/stack_catalog.py`
- Create: `tools/discover_project.py`
- Create: `schemas/project-discovery-output.schema.json`
- Create: `tests/test_discover_project.py`
- Modify: `tools/scan_project.py`

**Interfaces:**
- Consumes: v3 module shape from Task 1.
- Produces: `discover_project(project_dir: Path) -> dict[str, Any]`.
- Produces: `project_fingerprint(project_dir: Path, evidence_paths: Sequence[str]) -> str`.
- Produces CLI: `python tools/discover_project.py --project <root> [--output <artifact-dir>/project-discovery.json]`, where `<artifact-dir>` is confined below exact `docs/to_do`.
- Report statuses: `ready`, `needs_input`, `error`.
- Consumed by: Task 3.

- [ ] **Step 1: Написать tests для single-stack, monorepo и ambiguity**

```python
# tests/test_discover_project.py
class DiscoverProjectTests(unittest.TestCase):
    def test_discovers_python_and_java_modules_in_stable_order(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "services/api").mkdir(parents=True)
            (root / "services/api/pyproject.toml").write_text(
                '[project]\nname="api"\ndependencies=["fastapi"]\n[project.optional-dependencies]\ntest=["pytest"]\n',
                encoding="utf-8",
            )
            (root / "services/api/src").mkdir()
            (root / "services/api/tests").mkdir()
            (root / "services/orders/src/main/java").mkdir(parents=True)
            (root / "services/orders/pom.xml").write_text(
                '<project><dependencies><dependency><artifactId>spring-boot-starter-web</artifactId></dependency>'
                '<dependency><artifactId>junit-jupiter</artifactId></dependency></dependencies></project>',
                encoding="utf-8",
            )
            report = discover_project(root)
            self.assertEqual(report["status"], "ready")
            self.assertEqual([item["root"] for item in report["modules"]], ["services/api", "services/orders"])
            self.assertEqual(report["modules"][0]["stack"]["framework"], "fastapi")
            self.assertEqual(report["modules"][1]["test"]["framework"], "junit5")

    def test_multiple_test_frameworks_return_blocking_question(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "package.json").write_text(
                '{"name":"web","devDependencies":{"jest":"1","mocha":"1"}}', encoding="utf-8"
            )
            report = discover_project(root)
            self.assertEqual(report["status"], "needs_input")
            self.assertEqual(report["questions"][0]["field"], "modules.web.test.framework")
            self.assertEqual({option["value"] for option in report["questions"][0]["options"]}, {"jest", "mocha"})
```

Добавить отдельные tests для:

- `npm workspaces` и Maven aggregator: pure aggregator не становится исполняемым модулем;
- одинаковых имён каталогов: IDs выводятся из относительных корней и остаются уникальными;
- проекта без манифестов: `status=error`, пустые modules, без записи;
- существующих `mvnw.cmd`, `mvnw`, `gradlew.bat`, `gradlew`: только реально найденные wrapper paths;
- `.env`, private registry URL и symlink escape: секрет не появляется ни в одном JSON-поле;
- одинакового fingerprint при неизменном проекте и другого после изменения манифеста;
- стабильного порядка modules, paths, evidence, questions и warnings;
- `scan_project.py` не создаёт и не меняет `.skillsrc`.

- [ ] **Step 2: Запустить tests и подтвердить RED**

Run: `python -m unittest tests.test_discover_project -v`

Expected: FAIL с отсутствующим `tools.discover_project`.

- [ ] **Step 3: Вынести единый каталог стеков**

```python
# tools/stack_catalog.py
IGNORED_DIR_NAMES = frozenset({
    ".git", ".idea", ".tools", ".venv", "venv", "__pycache__",
    "node_modules", "target", "build", "dist", ".pytest_cache", ".ruff_cache",
})

MANIFEST_LANGUAGES = {
    "pyproject.toml": "python",
    "requirements.txt": "python",
    "requirements-dev.txt": "python",
    "setup.py": "python",
    "Pipfile": "python",
    "pom.xml": "java",
    "build.gradle": "java",
    "build.gradle.kts": "java",
    "package.json": "typescript",
    "go.mod": "go",
}
```

Перенести из `scan_project.py` текущие framework/test marker tables без изменения их значений. Экспортировать `match_marker(text, markers) -> str | None`, `manifest_language(name) -> str | None` и `normalize_build_tool(value) -> str`. Адресный сканер импортирует эти public symbols вместо собственной копии.

- [ ] **Step 4: Реализовать read-only discovery report**

```python
# tools/discover_project.py
def discover_project(project_dir: Path) -> dict[str, Any]:
    root = project_dir.resolve()
    manifests = find_confined_manifests(root)
    if not manifests:
        return build_report("error", root.name, [], [], [], ["build manifests were not found"])
    modules, questions, warnings = analyze_module_roots(root, manifests)
    status = "needs_input" if questions else "ready"
    evidence = sorted({path for module in modules for path in module["detected_from"]})
    return {
        "status": status,
        "project_name": root.name,
        "modules": sorted(modules, key=lambda item: (item["root"], item["id"])),
        "questions": sorted(questions, key=lambda item: item["id"]),
        "warnings": sorted(warnings),
        "errors": [],
        "fingerprint": project_fingerprint(root, evidence),
        "scanned_at": datetime.now(timezone.utc).isoformat(),
    }
```

Алгоритм module roots:

1. Найти confined manifests, не заходя в ignored directories.
2. Разобрать Maven `<modules>`, Gradle `include`, npm `workspaces` и `go.work` как связи workspace → child.
3. Pure aggregator без source/test дерева хранить только как evidence обнаружения children, но не добавлять как самостоятельный test module.
4. Остальные manifest directories считать кандидатами модулей.
5. Для одного module root объединить runtime и test manifests.
6. Добавлять только существующие source/test/resource/feature-source paths.
7. При двух несовместимых значениях критического поля создать `question`, а не выбирать первое.
8. Не включать содержимое зависимостей и URL в evidence; evidence содержит только относительный путь и безопасный тип маркера.

Question shape:

```json
{
  "id": "module:web:test.framework",
  "field": "modules.web.test.framework",
  "impact": "Определяет шаблон генерации и средство запуска тестов",
  "options": [
    {"id": "jest", "value": "jest", "evidence": ["package.json:devDependencies.jest"]},
    {"id": "mocha", "value": "mocha", "evidence": ["package.json:devDependencies.mocha"]}
  ]
}
```

- [ ] **Step 5: Создать JSON Schema отчёта**

`schemas/project-discovery-output.schema.json` требует все поля отчёта. Установить conditional semantics:

- `ready`: modules nonempty, questions empty, errors empty;
- `needs_input`: modules nonempty, questions nonempty, errors empty;
- `error`: errors nonempty;
- option IDs уникальны внутри вопроса проверкой Python semantic helper; schema проверяет форму;
- `fingerprint` соответствует `^[0-9a-f]{64}$`;
- `additionalProperties: false` на всех объектах.

- [ ] **Step 6: Синхронизировать адресный scanner**

В `tools/scan_project.py`:

- импортировать marker tables/helpers из `tools.stack_catalog` с тем же direct/package import pattern;
- исправить docstring и CLI description: scanner никогда не обновляет `.skillsrc`;
- удалить `_SKILLSRC_TEMPLATE`, `update_skillsrc`, `_guess_paths`, `_patch_skillsrc_fields`;
- оставить `skillsrc_updated=False`, `skillsrc_path=None` в report для обратной совместимости `scan-project-output.schema.json`.

- [ ] **Step 7: Запустить focused suite и CLI schema validation**

Run: `python -m unittest tests.test_discover_project -v`

Expected: PASS.

Run: `python tools/discover_project.py --project .`

Expected: один JSON-документ; exit 0 при `ready|needs_input`, exit 1 только при `error`.

Сохранить stdout во временный файл вне репозитория и проверить:

Run: `python tools/validate_artifact.py schemas/project-discovery-output.schema.json <temporary-report.json>`

Expected: `{"errors":[],"status":"valid"}`.

- [ ] **Step 8: Scope check и commit**

Run: `git diff --check -- tools/stack_catalog.py tools/discover_project.py tools/scan_project.py schemas/project-discovery-output.schema.json tests/test_discover_project.py`

Expected: exit 0.

```bash
git add tools/stack_catalog.py tools/discover_project.py tools/scan_project.py schemas/project-discovery-output.schema.json tests/test_discover_project.py
git commit -m "feat: discover all project modules"
```

---

### Task 3: Компиляция, согласование и атомарная запись `.skillsrc`

**Files:**
- Create: `tools/init_skillsrc.py`
- Create: `schemas/skillsrc-init-output.schema.json`
- Create: `tests/test_init_skillsrc.py`

**Interfaces:**
- Consumes: `discover_project`, `project_fingerprint`, `load_skillsrc`, `normalize_skillsrc`.
- Produces: `compile_skillsrc(discovery: Mapping[str, Any], answers: Mapping[str, str]) -> dict[str, Any]`.
- Produces: `reconcile_skillsrc(existing: dict[str, Any] | None, proposed: dict[str, Any], answers: Mapping[str, str]) -> dict[str, Any]`.
- Produces: `ensure_skillsrc(project_dir: Path, answers: Mapping[str, str], write: bool) -> dict[str, Any]`.
- Produces CLI: `python tools/init_skillsrc.py --project <root> [--write] [--answers <answers.json>] [--output <artifact-dir>/skillsrc-init.json]`, where `<artifact-dir>` is confined below exact `docs/to_do`.
- Exit codes: 0 `preview|created|updated|unchanged`; 3 `needs_input|conflict`; 1 `error`; 2 invocation/read/shape error.

- [ ] **Step 1: Написать tests жизненного цикла**

```python
# tests/test_init_skillsrc.py
class InitSkillsrcTests(unittest.TestCase):
    def test_missing_manifest_is_created_and_second_run_is_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "pyproject.toml").write_text(
                '[project]\nname="api"\ndependencies=["fastapi", "pytest"]\n', encoding="utf-8"
            )
            (root / "src").mkdir()
            (root / "tests").mkdir()
            first = ensure_skillsrc(root, {}, write=True)
            self.assertEqual(first["status"], "created")
            original = (root / ".skillsrc").read_bytes()
            second = ensure_skillsrc(root, {}, write=True)
            self.assertEqual(second["status"], "unchanged")
            self.assertEqual((root / ".skillsrc").read_bytes(), original)

    def test_critical_question_never_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "package.json").write_text(
                '{"name":"web","devDependencies":{"jest":"1","mocha":"1"}}', encoding="utf-8"
            )
            report = ensure_skillsrc(root, {}, write=True)
            self.assertEqual(report["status"], "needs_input")
            self.assertFalse((root / ".skillsrc").exists())

    def test_existing_bytes_survive_replace_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "pyproject.toml").write_text(
                '[project]\nname="api"\ndependencies=["fastapi", "pytest"]\n', encoding="utf-8"
            )
            (root / "src").mkdir()
            (root / "tests").mkdir()
            self.assertEqual(ensure_skillsrc(root, {}, write=True)["status"], "created")
            original = (root / ".skillsrc").read_bytes()
            (root / "docs/requirements").mkdir(parents=True)
            with unittest.mock.patch("tools.init_skillsrc.os.replace", side_effect=OSError("denied")):
                report = ensure_skillsrc(root, {}, write=True)
            self.assertEqual(report["status"], "error")
            self.assertEqual((root / ".skillsrc").read_bytes(), original)
```

Импортировать `unittest.mock` в начале файла. Остальные lifecycle cases оформить отдельными методами с именами `test_preview_never_writes`, `test_unknown_answer_is_rejected`, `test_v3_additive_module_update_preserves_existing_values`, `test_destructive_change_requires_answer`, `test_v2_matching_module_stays_v2`, `test_v2_multimodule_requires_migration_answer`, `test_fingerprint_drift_never_writes`, `test_output_must_be_under_docs_to_do`, `test_invalid_existing_yaml_is_preserved`, `test_repeated_write_is_byte_identical` и `test_receipt_redacts_registry_credentials`.

Добавить tests для:

- preview без `--write` не создаёт файл;
- answers принимает только ID существующей option;
- новый unambiguous module добавляется в v3, не меняя существующие semantic values;
- удаление или изменение существующего значения возвращает `conflict` до ответа;
- v2 с одним совпадающим модулем остаётся v2 и `unchanged`;
- v2 + найденный второй модуль возвращает migration question и не переписывается;
- fingerprint drift между compile и replace не пишет файл;
- `--output` вне exact `docs/to_do` отклоняется;
- malformed YAML/schema existing file не перезаписывается;
- repeated answer/write produces byte-identical canonical YAML;
- stdout/receipt не содержит содержимого private registry URL.

- [ ] **Step 2: Запустить tests и подтвердить RED**

Run: `python -m unittest tests.test_init_skillsrc -v`

Expected: FAIL с отсутствующим `tools.init_skillsrc`.

- [ ] **Step 3: Реализовать compiler и answer validation**

```python
def compile_skillsrc(discovery: Mapping[str, Any], answers: Mapping[str, str]) -> dict[str, Any]:
    unresolved = []
    selected_values: dict[str, object] = {}
    for question in discovery["questions"]:
        selected = answers.get(question["id"])
        options = {option["id"]: option for option in question["options"]}
        if selected is None:
            unresolved.append(question)
            continue
        if selected not in options:
            raise InitError("answer_unknown", f"unknown option for {question['id']}")
        selected_values[question["field"]] = options[selected]["value"]
    if unresolved:
        raise NeedsInput(unresolved)
    modules = apply_selected_values(copy.deepcopy(discovery["modules"]), selected_values)
    return {
        "version": "3.0",
        "project": {"name": discovery["project_name"]},
        "discovery": {"on_missing": "automatic", "conflict_policy": "ask_user"},
        "modules": modules,
    }
```

Методология не выводится из имён каталогов. Она сохраняется из existing document или добавляется только через отдельный подтверждённый ответ.

- [ ] **Step 4: Реализовать reconciliation без скрытой потери данных**

`reconcile_skillsrc` сравнивает нормализованные v2/v3 manifests по module ID и полям:

- exact match → `unchanged`;
- missing module в v3 + отсутствуют конфликты → semantic-preserving `updated`;
- detected replacement/removal → question с options `keep-existing|use-detected`;
- v2 migration → отдельный question `migrate-v2-to-v3`;
- существующие `resolution`, `contracts`, `skills_registry`, methodology и неизвестные для compiler, но разрешённые schema поля сохраняются.

Для generated v3 serialization использовать:

```python
yaml.safe_dump(document, allow_unicode=True, sort_keys=False, default_flow_style=False)
```

Повторная сериализация того же document должна давать те же байты с `\n` line endings.

- [ ] **Step 5: Реализовать stale guard и атомарную запись**

```python
def atomic_write_skillsrc(
    project_dir: Path,
    destination: Path,
    document: Mapping[str, Any],
    evidence_paths: Sequence[str],
    expected_fingerprint: str,
) -> None:
    if project_fingerprint(project_dir, evidence_paths) != expected_fingerprint:
        raise InitError("project_changed", "project manifests changed during initialization")
    payload = yaml.safe_dump(document, allow_unicode=True, sort_keys=False).encode("utf-8")
    handle, temporary_name = tempfile.mkstemp(prefix=".skillsrc.", suffix=".tmp", dir=project_dir)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        load_skillsrc(temporary)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
```

До `os.replace` повторно проверить, что destination не изменился со времени чтения: сохранить SHA-256 старых bytes и сравнить непосредственно перед replace.

- [ ] **Step 6: Реализовать JSON CLI и output confinement**

CLI report shape:

```json
{
  "status": "created",
  "skillsrc_path": ".skillsrc",
  "written": true,
  "module_ids": ["api"],
  "questions": [],
  "changes": [{"operation": "create", "path": ".skillsrc"}],
  "warnings": [],
  "errors": [],
  "discovery_fingerprint": "<64 lowercase hex>"
}
```

`--output` допускает только confined путь, содержащий exact components `docs/to_do`; receipt пишется атомарно и также печатается в stdout. Даже `needs_input` и `error` сохраняют честный receipt, если output path разрешён.

- [ ] **Step 7: Создать и проверить init-output schema**

Schema требует exact status-specific semantics:

- `created|updated`: `written=true`, changes nonempty;
- `unchanged`: `written=false`, changes empty;
- `preview`: `written=false`;
- `needs_input|conflict`: `written=false`, questions nonempty;
- `error`: `written=false`, errors nonempty;
- `additionalProperties: false` в каждом объекте.

- [ ] **Step 8: Запустить focused tests**

Run: `python -m unittest tests.test_init_skillsrc -v`

Expected: PASS.

Run: `git diff --check -- tools/init_skillsrc.py schemas/skillsrc-init-output.schema.json tests/test_init_skillsrc.py`

Expected: exit 0.

- [ ] **Step 9: Commit**

```bash
git add tools/init_skillsrc.py schemas/skillsrc-init-output.schema.json tests/test_init_skillsrc.py
git commit -m "feat: initialize skillsrc safely"
```

---

### Task 4: Выбор модуля в `run_tests.py`

**Files:**
- Modify: `tools/run_tests.py`
- Create: `tests/test_run_tests_skillsrc_v3.py`

**Interfaces:**
- Consumes: Task 1 `load_skillsrc`, `normalize_skillsrc`, `select_module`, `resolve_module_root`.
- Produces: `resolve_execution_context(project_root: Path, skillsrc_path: Path, module_id: str | None, language_override: str | None) -> tuple[Path, str, dict[str, Any]]`.
- Adds CLI: `--module <module-id>`.
- Preserves CLI: v2 single project and explicit `--language`.

- [ ] **Step 1: Написать module-selection tests без запуска целевых тестов**

```python
# tests/test_run_tests_skillsrc_v3.py
class RunTestsSkillsrcV3Tests(unittest.TestCase):
    def _module(self, module_id, root, language, build_tool):
        return {
            "id": module_id,
            "root": root,
            "stack": {"language": language, "build_tool": build_tool},
            "detected_from": [f"{root}/manifest"],
        }

    def _write_v3(self, root, modules):
        path = root / ".skillsrc"
        path.write_text(yaml.safe_dump({
            "version": "3.0",
            "project": {"name": "platform"},
            "discovery": {"on_missing": "automatic", "conflict_policy": "ask_user"},
            "modules": modules,
        }, sort_keys=False), encoding="utf-8")
        return path

    def test_resolves_selected_module_root_and_language(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "services/api").mkdir(parents=True)
            (root / "apps/web").mkdir(parents=True)
            manifest = self._write_v3(root, modules=[
                self._module("api", "services/api", "python", "pip"),
                self._module("web", "apps/web", "typescript", "npm"),
            ])
            execution_root, language, selected = resolve_execution_context(root, manifest, "api", None)
            self.assertEqual(execution_root, (root / "services/api").resolve())
            self.assertEqual(language, "python")
            self.assertEqual(selected["id"], "api")

    def test_multiple_modules_without_selection_is_not_runnable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = self._write_v3(root, [
                self._module("api", "services/api", "python", "pip"),
                self._module("web", "apps/web", "typescript", "npm"),
            ])
            with self.assertRaises(SkillsrcError) as raised:
                resolve_execution_context(root, manifest, None, None)
            self.assertEqual(raised.exception.code, "module_required")

    def test_language_override_must_match_selected_module(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "services/api").mkdir(parents=True)
            manifest = self._write_v3(root, [self._module("api", "services/api", "python", "pip")])
            with self.assertRaises(SkillsrcError) as raised:
                resolve_execution_context(root, manifest, "api", "java")
            self.assertEqual(raised.exception.code, "language_conflict")
```

- [ ] **Step 2: Запустить tests и подтвердить RED**

Run: `python -m unittest tests.test_run_tests_skillsrc_v3 -v`

Expected: FAIL, потому что `resolve_execution_context` и `--module` отсутствуют.

- [ ] **Step 3: Заменить ручной YAML-парсер общим loader**

Удалить `detect_language_from_skillsrc`. Реализовать:

```python
def resolve_execution_context(project_root, skillsrc_path, module_id, language_override):
    document = normalize_skillsrc(load_skillsrc(skillsrc_path))
    module = select_module(document, module_id)
    execution_root = resolve_module_root(project_root, module)
    detected_language = module["stack"]["language"]
    if language_override and language_override != detected_language:
        raise SkillsrcError("language_conflict", "--language conflicts with selected module")
    return execution_root, language_override or detected_language, module
```

В `main()` выполнять selection до `load_automation_artifact`, чтобы generated file paths проверялись относительно выбранного module root. При `SkillsrcError` вернуть честный `NOT_RUNNABLE`, exit 2 и `missing`:

- `module_selection` для отсутствующего/неизвестного module ID;
- `skillsrc_validation` для invalid YAML/schema;
- `language_detection` для отсутствующего языка;
- `language_conflict` для несовместимого override.

Если `.skillsrc` v2, `--module` необязателен и выбирается `root`. Если `--language` передан без читаемого `.skillsrc`, сохранить существующее legacy-поведение с `project_root` как execution root.

- [ ] **Step 4: Добавить CLI argument и обновить help**

```python
parser.add_argument("--module", help="ID модуля из .skillsrc версии 3")
```

Документировать, что `--project` — корень репозитория, а фактический working directory вычисляется из выбранного module root.

- [ ] **Step 5: Запустить focused и существующие self-tests**

Run: `python -m unittest tests.test_run_tests_skillsrc_v3 -v`

Expected: PASS.

Run: `python tools/run_tests.py --help`

Expected: exit 0; help содержит `--module`.

Run: `git diff --check -- tools/run_tests.py tests/test_run_tests_skillsrc_v3.py`

Expected: exit 0.

- [ ] **Step 6: Commit**

```bash
git add tools/run_tests.py tests/test_run_tests_skillsrc_v3.py
git commit -m "feat: select skillsrc module for test runs"
```

---

### Task 5: Автоматический bootstrap в оркестраторе

**Files:**
- Modify: `skills/orchestrate/SKILL.md`
- Modify: `skills/orchestrate/references/orchestration-contract.md`
- Create: `tests/test_orchestrator_skillsrc_bootstrap.py`

**Interfaces:**
- Consumes: Task 3 CLI and `schemas/skillsrc-init-output.schema.json`.
- Consumes: Task 4 `--module`.
- Produces controller order: bootstrap → module selection → `context-marker` → existing pipeline.
- Produces persistent receipts under `<project>/docs/to_do/<run>/00-project-bootstrap/`.

- [ ] **Step 1: Написать contract tests для обязательного preflight**

```python
# tests/test_orchestrator_skillsrc_bootstrap.py
class OrchestratorSkillsrcBootstrapTests(unittest.TestCase):
    def test_bootstrap_precedes_context_marker_and_run_tests_uses_module(self):
        skill = Path("skills/orchestrate/SKILL.md").read_text(encoding="utf-8")
        bootstrap = skill.index("tools/init_skillsrc.py")
        context = skill.index("context-marker")
        self.assertLess(bootstrap, context)
        self.assertIn("--module <module-id>", skill)
        self.assertIn("needs_input", skill)
        self.assertIn("не задавай следующий вопрос одновременно", skill)

    def test_end_to_end_missing_skillsrc_creates_v3_and_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            (project / "pyproject.toml").write_text(
                '[project]\nname="api"\ndependencies=["fastapi", "pytest"]\n', encoding="utf-8"
            )
            (project / "src").mkdir()
            (project / "tests").mkdir()
            receipt = project / "docs/to_do/run/attempt-01/skillsrc-init.json"
            command = [
                sys.executable,
                str(REPOSITORY_ROOT / "tools/init_skillsrc.py"),
                "--project", str(project),
                "--write",
                "--output", str(receipt),
            ]
            first = subprocess.run(command, cwd=REPOSITORY_ROOT, capture_output=True, text=True, check=False)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            self.assertEqual(yaml.safe_load((project / ".skillsrc").read_text(encoding="utf-8"))["version"], "3.0")
            self.assertEqual(json.loads(receipt.read_text(encoding="utf-8"))["status"], "created")

            second_receipt = project / "docs/to_do/run/attempt-02/skillsrc-init.json"
            second_command = command[:-1] + [str(second_receipt)]
            second = subprocess.run(second_command, cwd=REPOSITORY_ROOT, capture_output=True, text=True, check=False)
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertEqual(json.loads(second_receipt.read_text(encoding="utf-8"))["status"], "unchanged")
```

В начале файла определить `REPOSITORY_ROOT = Path(__file__).resolve().parents[1]` и импортировать `json`, `subprocess`, `sys`, `tempfile`, `unittest`, `Path` и `yaml`.

Добавить test, где ambiguous package.json даёт exit 3, receipt `needs_input`, `.skillsrc` отсутствует и текст оркестратора запрещает переход к `context-marker`.

- [ ] **Step 2: Запустить test и подтвердить RED**

Run: `python -m unittest tests.test_orchestrator_skillsrc_bootstrap -v`

Expected: FAIL, потому что SKILL ещё не требует bootstrap до `context-marker`.

- [ ] **Step 3: Добавить обязательную подготовку проекта**

В `skills/orchestrate/SKILL.md` перед чтением первого stage skill установить порядок:

1. Выбрать exact корень целевого проекта и каталог текущей попытки под `docs/to_do/`.
2. Выполнить `init_skillsrc.py --project <project> --write --output <attempt-dir>/00-skillsrc-init.json`.
3. Проверить receipt через `validate_artifact.py` и `skillsrc-init-output.schema.json`.
4. При `needs_input|conflict` остановить пайплайн, показать только первый unresolved question с options/evidence/impact.
5. Сохранить выбранные option IDs в новой immutable attempt-папке как `00-skillsrc-answers.json` и повторить bootstrap с `--answers`.
6. При `created|updated|unchanged` загрузить `.skillsrc`, определить module ID.
7. Если feature matches несколько modules, остановиться и спросить; не выбирать по вероятности.
8. Только после exact module selection переходить к `context-marker`.

Запретить передачу discovery questions и ответов evaluator-скиллам: это controller evidence, а не содержимое фичи.

- [ ] **Step 4: Зафиксировать точные команды в orchestration contract**

```text
python <root>/tools/init_skillsrc.py --project <project> --write --output <project>/docs/to_do/<run>/00-project-bootstrap/attempt-01/skillsrc-init.json
python <root>/tools/validate_artifact.py <root>/schemas/skillsrc-init-output.schema.json <project>/docs/to_do/<run>/00-project-bootstrap/attempt-01/skillsrc-init.json
```

После ответа:

```text
python <root>/tools/init_skillsrc.py --project <project> --write --answers <project>/docs/to_do/<run>/00-project-bootstrap/attempt-02/skillsrc-answers.json --output <project>/docs/to_do/<run>/00-project-bootstrap/attempt-02/skillsrc-init.json
```

Команда исполнения становится:

```text
python <root>/tools/run_tests.py --project <project> --skillsrc <project>/.skillsrc --module <module-id> --automation-artifact <tc-to-autotest-output.json>
```

- [ ] **Step 5: Определить feature-to-module selection contract**

Порядок выбора:

1. Единственный module выбирается автоматически.
2. Exact user-supplied relative path выбирает содержащий его module root.
3. Для текстового названия проверить только объявленные `feature_sources` и source paths.
4. Один module с прямым совпадением требования, route, symbol или path выбирается и записывается в controller receipt.
5. Ноль совпадений — попросить путь/модуль.
6. Несколько совпадений — показать module IDs и evidence, затем спросить один выбор.

Отобранные требования и исходные файлы становятся `raw_content` существующего
этапа `context-marker`; его schema-valid JSON является динамическим контекстом
фичи для текущей попытки. Не вводить второй каталог фич, не добавлять business
content в `.skillsrc` и не сохранять его в bootstrap receipt.

- [ ] **Step 6: Запустить focused contract tests**

Run: `python -m unittest tests.test_orchestrator_skillsrc_bootstrap -v`

Expected: PASS.

Run: `git diff --check -- skills/orchestrate/SKILL.md skills/orchestrate/references/orchestration-contract.md tests/test_orchestrator_skillsrc_bootstrap.py`

Expected: exit 0.

- [ ] **Step 7: Commit**

```bash
git add skills/orchestrate/SKILL.md skills/orchestrate/references/orchestration-contract.md tests/test_orchestrator_skillsrc_bootstrap.py
git commit -m "feat: bootstrap skillsrc before orchestration"
```

---

### Task 6: Пользовательская документация, doctor и полная приёмка

**Files:**
- Modify: `.skillsrc.example`
- Modify: `README.md`
- Modify: `USAGE.md`
- Modify: `HOW-IT-WORKS.md`
- Modify: `tools/doctor.py`
- Modify: `tests/test_orchestrator_skillsrc_bootstrap.py`

**Interfaces:**
- Consumes: все interfaces Tasks 1–5.
- Produces: portable user workflow без ручного копирования `.skillsrc.example`.
- Produces: final acceptance evidence for Windows-compatible and POSIX path shapes.

- [ ] **Step 1: Добавить documentation/doctor assertions**

В `tests/test_orchestrator_skillsrc_bootstrap.py` добавить:

```python
def test_public_docs_lead_with_automatic_initialization(self):
    for relative in ("README.md", "USAGE.md", "HOW-IT-WORKS.md"):
        text = Path(relative).read_text(encoding="utf-8")
        self.assertIn("автомат", text.lower())
        self.assertIn(".skillsrc", text)
    self.assertNotIn("Скопируйте `.skillsrc.example`", Path("README.md").read_text(encoding="utf-8"))

def test_doctor_requires_skillsrc_bootstrap_runtime(self):
    report = inspect_environment(Path("."))
    self.assertTrue(report["integrity"]["valid"])
```

- [ ] **Step 2: Запустить focused test и подтвердить RED**

Run: `python -m unittest tests.test_orchestrator_skillsrc_bootstrap -v`

Expected: FAIL на старой ручной инструкции README или отсутствующих doctor requirements.

- [ ] **Step 3: Переписать `.skillsrc.example` как v3**

Пример содержит два modules (`backend` Python/pytest и `frontend` TypeScript/jest), относительные paths, feature_sources, detected_from и обе wrapper-платформы только там, где они существуют в примере. Добавить комментарий:

```yaml
# Обычно этот файл создаёт tools/init_skillsrc.py.
# Редактируйте подтверждённые значения; при конфликте следующий запуск покажет diff.
```

Не добавлять фиктивные секреты, URL с credentials и содержимое фич.

- [ ] **Step 4: Обновить публичный quick start**

В `README.md` и `USAGE.md` заменить обязательное ручное копирование на:

```text
При первой команде оркестратор автоматически сканирует структуру проекта и создаёт `.skillsrc`.
Если критическое значение неоднозначно, он остановится и задаст один вопрос.
```

Сохранить ручную команду как диагностический/CI вариант:

```bash
python tools/init_skillsrc.py --project /path/to/project --write --output /path/to/project/docs/to_do/skillsrc-init.json
```

Добавить разделы:

- single-module flow;
- monorepo `modules[]`;
- `--answers` и immutable attempts;
- quick freshness check;
- v2 compatibility;
- Windows и Linux examples;
- `.skillsrc` не даёт разрешение менять исходный проект.

В `HOW-IT-WORKS.md` вставить bootstrap перед текущим первым этапом и показать, что feature context строится отдельно после module selection.

- [ ] **Step 5: Расширить doctor integrity**

Добавить в `required_files`:

```python
"schemas/skillsrc.schema.json",
"schemas/project-discovery-output.schema.json",
"schemas/skillsrc-init-output.schema.json",
"tools/skillsrc_manifest.py",
"tools/discover_project.py",
"tools/init_skillsrc.py",
```

Проверить `$id` новых schemas тем же способом, что существующий tc-to-autotest schema. Dependencies остаются `jsonschema` и `yaml`.

- [ ] **Step 6: Запустить весь stdlib test suite**

Run: `python -m unittest discover -s tests -v`

Expected: все tests PASS, skipped допускаются только для symlink privilege case на Windows и должны иметь явную причину.

- [ ] **Step 7: Запустить portable runtime checks**

Run: `python tools/doctor.py --root .`

Expected: JSON `status=PASS`, exit 0.

Run: `python tools/contract_check.py --root . --full`

Expected: JSON `status=passed`, exit 0.

Run: `python -m json.tool schemas/skillsrc.schema.json`

Expected: exit 0.

Run: `python -m json.tool schemas/project-discovery-output.schema.json`

Expected: exit 0.

Run: `python -m json.tool schemas/skillsrc-init-output.schema.json`

Expected: exit 0.

- [ ] **Step 8: Выполнить два end-to-end smoke scenarios**

Сценарий A — временный single-module Python project:

1. Создать `pyproject.toml`, `src/`, `tests/`, `docs/requirements/`.
2. Запустить `init_skillsrc.py --write`.
3. Проверить generated v3 manifest и receipt schemas.
4. Повторить команду и подтвердить `unchanged` и byte-identical `.skillsrc`.

Сценарий B — временный Python + Java monorepo:

1. Создать два module manifests и source/test roots.
2. Запустить initialization и подтвердить два stable module IDs.
3. Вызвать `resolve_execution_context` для каждого module.
4. Убедиться, что working roots и languages различаются и confined.

Проекты создаются через `tempfile.TemporaryDirectory`; реальные пользовательские проекты не изменяются.

- [ ] **Step 9: Финальный scope и format check**

Run: `git status --short`

Expected: только файлы этого плана реализации.

Run: `git diff --check`

Expected: exit 0.

Проверить, что нет `.env`, credentials, абсолютных путей временных проектов, `__pycache__`, `.pytest_cache` или generated receipts в staged scope.

- [ ] **Step 10: Commit**

```bash
git add .skillsrc.example README.md USAGE.md HOW-IT-WORKS.md tools/doctor.py tests/test_orchestrator_skillsrc_bootstrap.py
git commit -m "docs: explain automatic project discovery"
```

---

## Final Acceptance Gate

Перед объявлением готовности:

1. Все шесть task commits существуют и содержат только назначенные paths.
2. `python -m unittest discover -s tests -v` завершён с exit 0.
3. `doctor.py` и `contract_check.py --full` завершены с exit 0.
4. Все три новые/изменённые schemas прошли JSON parse и фактические artifacts.
5. Single-module и monorepo smoke scenarios подтверждены.
6. Existing v2 manifest test остаётся зелёным.
7. Ambiguous stack test возвращает structured question и не создаёт `.skillsrc`.
8. Atomic-failure test сохраняет exact исходные bytes.
9. Freshness test не меняет файл при неизменном проекте.
10. Fresh независимый review проверяет соответствие этому plan и design spec.
