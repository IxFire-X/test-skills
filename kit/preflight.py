"""Preflight of a project before a test-skills run (no network, read-only).

usage: python3 preflight.py --pack <test-skills copy> --project <project root> [--docs <spec inside the project>]...
       [--m2 ~/.m2/repository] [--json report.json]

Prints a table «ок / внимание / стоп» with what to do.  Exit code: 0 — no stop, 1 — at least one stop.
Checks: Python ≥ 3.11; the package (tools.doctor); the project scan (tools.scan_project); JDK 17+; the build
wrapper (mvnw executable on POSIX, mvnw.cmd on Windows) or Maven on PATH; the Maven repository has surefire,
the dependency plugin and the pinned PIT jars (offline machine); linters that read the whole tree
(nohttp, spotless, checkstyle/rat/license with ${basedir} or **/*) — they also read .pilot-runs (finding 1);
docker-compose / Testcontainers / an external database of the feature; secrets and personal data in the files
the pipeline reads (the specification, .skillsrc, config, the source tree).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

OK, WARN, STOP = "ок", "внимание", "стоп"
TEXT_SUFFIXES = {".java", ".kt", ".xml", ".properties", ".yml", ".yaml", ".md", ".txt", ".json", ".sql", ".gradle", ".kts", ".html", ".env", ".cfg", ".ini", ".conf"}
SKIP_DIRS = {".git", "target", "build", ".gradle", ".idea", "node_modules", ".pilot-runs", ".tools", "test-cases"}
SECRET = re.compile(r"(?i)(-----BEGIN [A-Z ]*PRIVATE KEY-----|AKIA[0-9A-Z]{16}|(?:password|passwd|secret|token|api[_-]?key)\s*[:=]\s*['\"]?[^\s'\"$<{][^\s'\"]{5,})")
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE = re.compile(r"(?<!\d)(?:\+7|8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}(?!\d)")
SNILS_INN = re.compile(r"(?<!\d)(?:\d{3}-\d{3}-\d{3} \d{2}|\d{12})(?!\d)")
rows: list[tuple[str, str, str, str]] = []


def add(status: str, check: str, finding: str, hint: str = "") -> None:
    rows.append((status, check, finding, hint))


def run(argv: list[str], cwd: Path | None = None, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False)


def check_python() -> None:
    version = sys.version_info
    if version >= (3, 11):
        add(OK, "Python", f"{version.major}.{version.minor}.{version.micro}")
    else:
        add(STOP, "Python", f"{version.major}.{version.minor} < 3.11", "запускать пакет python3.11+ (python3 --version)")
    for module in ("jsonschema", "yaml"):
        try:
            __import__(module)
            add(OK, f"модуль {module}", "установлен")
        except ImportError:
            add(STOP, f"модуль {module}", "нет", f"pip install -r <пакет>/requirements.txt (локальное зеркало pip), нужен {module}")


def check_pack(pack: Path) -> None:
    done = run([sys.executable, "-m", "tools.doctor", "--root", str(pack)], cwd=pack)
    try:
        report = json.loads(done.stdout)
    except ValueError:
        add(STOP, "пакет (doctor)", f"doctor не выдал JSON (код {done.returncode}): {(done.stderr or done.stdout)[-200:]}", "проверить копию пакета и Python")
        return
    integrity = report.get("integrity") or {}
    if report.get("status") == "PASS" and integrity.get("valid"):
        add(OK, "пакет (doctor)", f"PASS, целостность valid, Python {report.get('python', {}).get('version')}")
    else:
        add(STOP, "пакет (doctor)", f"{report.get('status')}, отсутствуют: {integrity.get('missing')}", "распаковать архив заново и сверить sha256-список")


def check_scan(pack: Path, project: Path) -> dict:
    target = next((str(path.relative_to(project)) for path in (project / "pom.xml", project / "build.gradle", project / "build.gradle.kts") if path.is_file()), ".")
    done = run([sys.executable, "-m", "tools.scan_project", "--project", str(project), "--target", target], cwd=pack)
    try:
        scan = json.loads(done.stdout)
    except ValueError:
        add(STOP, "scan_project", f"сканер упал (код {done.returncode}): {(done.stderr or done.stdout)[-200:]}", "прислать текст ошибки")
        return {}
    stack = scan.get("stack") or scan.get("modules") or scan
    add(OK, "scan_project", "стек: " + json.dumps(stack, ensure_ascii=False)[:160])
    return scan


def check_jdk() -> None:
    java_home = os.environ.get("JAVA_HOME")
    java = Path(java_home) / "bin" / ("java.exe" if os.name == "nt" else "java") if java_home else None
    executable = str(java) if java and java.is_file() else shutil.which("java")
    if not executable:
        add(STOP, "JDK", "java не найден (ни JAVA_HOME, ни PATH)", "export JAVA_HOME=<JDK 17+>; PATH=$JAVA_HOME/bin:$PATH")
        return
    done = run([executable, "-version"])
    text = done.stderr + done.stdout
    match = re.search(r'version "(\d+)(?:\.(\d+))?', text)
    major = int(match.group(1)) if match else 0
    major = int(match.group(2)) if match and major == 1 else major
    if major >= 17:
        add(OK, "JDK", f"{executable}: {major}" + ("" if java_home else " (JAVA_HOME не задан — задать для Maven и пакета)"))
    else:
        add(STOP, "JDK", f"{executable}: версия {major or '?'}", "нужен JDK 17+ (JAVA_HOME)")
    if os.name != "nt" and not java_home:
        add(WARN, "JAVA_HOME", "не задан", "export JAVA_HOME=<путь к JDK 17+>; пакет и mvnw берут его")


def check_wrapper(project: Path) -> str | None:
    if (project / "pom.xml").is_file():
        wrapper = project / ("mvnw.cmd" if os.name == "nt" else "mvnw")
        if wrapper.is_file():
            if os.name != "nt" and not os.access(wrapper, os.X_OK):
                add(STOP, "обёртка Maven", f"{wrapper.name} есть, но не исполняемый", f"chmod +x {wrapper}  (иначе адаптер не узнает обёртку: os.access X_OK)")
            else:
                add(OK, "обёртка Maven", wrapper.name)
            properties = project / ".mvn" / "wrapper" / "maven-wrapper.properties"
            if properties.is_file():
                url = re.search(r"distributionUrl=(\S+)", properties.read_text(encoding="utf-8", errors="replace"))
                add(WARN if url and url.group(1).startswith("http") else OK, "дистрибутив Maven обёртки",
                    (url.group(1) if url else "нет distributionUrl") + " — без сети нужен уже скачанный дистрибутив в ~/.m2/wrapper/dists",
                    "проверить: ls ~/.m2/wrapper/dists (или указать внутреннее зеркало)")
            return "maven"
        if shutil.which("mvn"):
            add(WARN, "обёртка Maven", "mvnw нет, есть mvn в PATH", "в .skillsrc test.adapter_id: maven:selected-symbols-v1 (системный Maven)")
            return "maven"
        add(STOP, "обёртка Maven", "нет ни mvnw, ни mvn", "добавить mvnw проекта или Maven в PATH")
        return "maven"
    if any((project / name).is_file() for name in ("build.gradle", "build.gradle.kts")):
        wrapper = project / ("gradlew.bat" if os.name == "nt" else "gradlew")
        if not wrapper.is_file():
            add(STOP, "обёртка Gradle", "gradlew нет", "добавить gradlew проекта")
        elif os.name != "nt" and not os.access(wrapper, os.X_OK):
            add(STOP, "обёртка Gradle", "gradlew не исполняемый", f"chmod +x {wrapper}")
        else:
            add(OK, "обёртка Gradle", wrapper.name)
        add(WARN, "Gradle + Windows", "команда --tests на каждый метод: класс > ~90 методов упрётся в лимит cmd.exe (находка № 4)", "на Linux не актуально")
        return "gradle"
    add(STOP, "сборка", "нет pom.xml / build.gradle в корне проекта", "указать корень модуля")
    return None


def check_maven_repository(m2: Path, pack: Path) -> None:
    if not m2.is_dir():
        add(STOP, "репозиторий Maven", f"{m2} нет", "собрать проект один раз (mvnw test) с доступом к внутреннему репозиторию — он наполнит ~/.m2")
        return
    pins = json.loads((pack / "tools" / "mutation_tools.json").read_text(encoding="utf-8"))["java"]
    wanted = {"maven-surefire-plugin": m2 / "org/apache/maven/plugins/maven-surefire-plugin",
              "maven-dependency-plugin " + pins["resolver_plugin"].rsplit(":", 1)[1]: m2 / "org/apache/maven/plugins/maven-dependency-plugin" / pins["resolver_plugin"].rsplit(":", 1)[1]}
    for root in pins["roots"]:
        group, artifact, version = root.split(":")
        wanted[f"{artifact} {version}"] = m2 / group.replace(".", "/") / artifact / version
    wanted["junit-platform-launcher"] = m2 / "org/junit/platform/junit-platform-launcher"
    for name, path in wanted.items():
        if path.is_dir() and any(path.rglob("*.jar")):
            add(OK, f"~/.m2: {name}", "есть")
        else:
            severity = STOP if "surefire" in name else WARN
            hint = ("без него мутации будут NOT_RUNNABLE: положить артефакт из внутреннего репозитория "
                    f"(mvn dependency:get -Dartifact=...) или запускать без --mutation" if severity == WARN else "без surefire тесты не запустятся")
            add(severity, f"~/.m2: {name}", f"нет ({path})", hint)


def _exclude_line(name: str, path: Path, text: str) -> str:
    """A ready line that keeps the linter away from .pilot-runs."""
    if path.suffix in {".gradle", ".kts"}:
        return {"spotless": "в блоке format/java: targetExclude('.pilot-runs/**')",
                "checkstyle": "tasks.withType(Checkstyle) { exclude('**/.pilot-runs/**') }",
                "nohttp": "nohttp { source.exclude('**/.pilot-runs/**') }"}.get(name, "exclude('**/.pilot-runs/**') в задаче линтера")
    if name in {"nohttp", "checkstyle"}:
        current = re.search(r"<excludes>([^<]*)</excludes>", text)
        if current:
            return f"<excludes>{current.group(1)},**/.pilot-runs/**/*</excludes>  (было: <excludes>{current.group(1)}</excludes>)"
        return "<excludes>**/.pilot-runs/**/*</excludes> в <configuration> плагина"
    return "<excludes><exclude>.pilot-runs/**</exclude></excludes> в <configuration> плагина"


def check_linters(project: Path, build: str | None) -> None:
    """Only the build files of the build the pipeline runs (a Maven project's build.gradle never runs)."""
    patterns = ("pom.xml",) if build == "maven" else ("*.gradle", "*.gradle.kts") if build == "gradle" else ("pom.xml", "*.gradle", "*.gradle.kts")
    builds = [path for pattern in patterns for path in project.rglob(pattern) if not (set(path.relative_to(project).parts) & SKIP_DIRS)]
    found = False
    for path in builds:
        text = path.read_text(encoding="utf-8", errors="replace")
        for name, pattern in (("nohttp", r"nohttp"), ("spotless", r"spotless"), ("checkstyle", r"maven-checkstyle-plugin|checkstyle\s*\{"),
                              ("apache-rat", r"apache-rat"), ("license", r"license-maven-plugin|com\.mycila"), ("editorconfig", r"editorconfig")):
            if not re.search(pattern, text, re.I):
                continue
            whole_tree = bool(re.search(r"<sourceDirector(?:y|ies)>\s*\$\{(?:basedir|project\.basedir)\}|<includes?>\s*\*\*/\*\s*<|target\s*=?\s*fileTree\(['\"]?\.", text))
            found = True
            if whole_tree or name in {"nohttp", "apache-rat", "license"}:
                add(STOP if name == "nohttp" else WARN, f"линтер {name}", f"{path.relative_to(project)}: читает всё дерево — и .pilot-runs (находка № 1)",
                    "на ветке прогона исключить .pilot-runs: " + _exclude_line(name, path, text) +
                    ". Иначе --runner process гарантированно не дойдёт до тестов (stdin.txt раннера — копии файлов проекта), "
                    "а оркестрованный прогон ломается после первой упавшей сборки (вывод Maven с http:// в .pilot-runs)")
            else:
                add(OK, f"линтер {name}", f"{path.relative_to(project)}: область — исходники")
    if not found:
        add(OK, "линтеры по всему дереву", "не найдены")


def check_services(project: Path) -> None:
    composes = [path for name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml") for path in [project / name] if path.is_file()]
    poms = " ".join(path.read_text(encoding="utf-8", errors="replace") for path in project.rglob("pom.xml") if not (set(path.relative_to(project).parts) & SKIP_DIRS))
    configs = [path for path in project.rglob("application*.*") if path.suffix in {".properties", ".yml", ".yaml"} and not (set(path.relative_to(project).parts) & SKIP_DIRS)]
    urls = sorted({match for path in configs for match in re.findall(r"jdbc:([a-z0-9]+):", path.read_text(encoding="utf-8", errors="replace"))})
    if composes:
        add(WARN, "docker-compose", ", ".join(path.name for path in composes), "тестам фичи не должен нужен внешний сервис; если нужен — поднять до прогона (docker на машине?)")
    if "testcontainers" in poms:
        add(WARN, "Testcontainers", "в зависимостях есть", "без docker такие тесты пропускаются или падают; сгенерированные тесты должны идти на H2/встроенной БД")
    if urls:
        external = [url for url in urls if url not in {"h2", "hsqldb", "derby"}]
        add(WARN if external else OK, "БД в конфигурации", ", ".join(urls), "внешняя БД — тесты пакета должны работать на тестовом профиле со встроенной БД" if external else "")


def check_sensitive(project: Path, docs: list[str]) -> None:
    files = [project / item for item in docs] + [project / ".skillsrc"]
    files += [path for path in project.rglob("*") if path.is_file() and path.suffix in TEXT_SUFFIXES and not (set(path.relative_to(project).parts) & SKIP_DIRS)
              and path.stat().st_size < 1_000_000]
    hits: dict[str, list[str]] = {"секреты": [], "email": [], "телефоны": [], "СНИЛС/ИНН": []}
    for path in dict.fromkeys(files):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        relative = str(path.relative_to(project))
        for name, pattern in (("секреты", SECRET), ("email", EMAIL), ("телефоны", PHONE), ("СНИЛС/ИНН", SNILS_INN)):
            if pattern.search(text):
                hits[name].append(relative)
    for name, paths in hits.items():
        if not paths:
            add(OK, f"ПДн/секреты: {name}", "не найдено")
            continue
        severity = STOP if name == "секреты" and any(item in docs or item == ".skillsrc" for item in paths) else WARN
        add(severity, f"ПДн/секреты: {name}", f"{len(paths)} файл(ов): {', '.join(paths[:5])}" + (" …" if len(paths) > 5 else ""),
            "пакет передаёт выбранные файлы модели: проверить, что это тестовые/демо-значения; реальные — убрать или исключить в .skillsrc (limits / exclusions)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pack", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--docs", action="append", default=[])
    parser.add_argument("--m2", default=str(Path.home() / ".m2" / "repository"))
    parser.add_argument("--json")
    args = parser.parse_args()
    pack, project = Path(args.pack).resolve(), Path(args.project).resolve()
    check_python()
    check_pack(pack)
    check_scan(pack, project)
    check_jdk()
    build = check_wrapper(project)
    if build == "maven":
        check_maven_repository(Path(args.m2), pack)
    check_linters(project, build)
    check_services(project)
    for doc in args.docs:
        add(OK if (project / doc).is_file() else STOP, "спецификация", doc, "" if (project / doc).is_file() else "путь --docs — внутри проекта")
    if not (project / ".skillsrc").is_file():
        add(WARN, ".skillsrc", "нет", "первый next создаст по разметке; для внутреннего проекта лучше положить свой (review_runner, requirements.id_pattern)")
    check_sensitive(project, args.docs)
    width = max(len(row[1]) for row in rows)
    for status, check, finding, hint in rows:
        print(f"{status:9} {check:{width}}  {finding}" + (f"\n{'':9} {'':{width}}  → {hint}" if hint and status != OK else ""))
    stops = sum(row[0] == STOP for row in rows)
    print(f"\nИтог: стоп {stops}, внимание {sum(row[0] == WARN for row in rows)}, ок {sum(row[0] == OK for row in rows)}")
    if args.json:
        Path(args.json).write_bytes(json.dumps([dict(zip(("status", "check", "finding", "hint"), row)) for row in rows], ensure_ascii=False, indent=1).encode("utf-8"))
    return 1 if stops else 0


if __name__ == "__main__":
    raise SystemExit(main())
