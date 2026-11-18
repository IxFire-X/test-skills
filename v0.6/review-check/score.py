"""Сверка ревьюера: сколько контрольных ошибок вида поймал ответ модели.

Только стандартная библиотека; файлы не меняет, ничего не запускает, в сеть не ходит.
Запуск (пути — аргументами):
    python -I score.py --key key/v080-01.json --answer work/answer-v080-01.json

Зачёт — как в замере на Haiku: находка с тем же case_id и тем же step_id или expectation_id, что у контрольной ошибки.
В скобках — мягкий зачёт: назван хотя бы тот же кейс.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(errors='replace')

CLASSES = {
    'status': 'изменён код ответа в тексте',
    'boundary': 'сдвинуто число в тексте',
    'assertion_removed': 'удалена строка «Проверки»',
    'state_check_removed': 'убран пункт из «Проверки»',
}


def load_answer(path):
    """JSON ответа. Терпит BOM, обёртку ```json … ``` и текст вокруг: берёт от первой { до последней }."""
    raw = Path(path).read_text(encoding='utf-8-sig', errors='replace')
    try:
        return json.loads(raw), None
    except ValueError:
        pass
    start, end = raw.find('{'), raw.rfind('}')
    if start < 0 or end <= start:
        return None, 'в файле нет JSON'
    try:
        return json.loads(raw[start:end + 1]), 'JSON был в обёртке, взят по скобкам'
    except ValueError as exc:
        return None, f'ответ не JSON: {exc}'


def norm(value):
    return str(value or '').strip().upper()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--key', required=True, help='key/<вид>.json')
    parser.add_argument('--answer', required=True, help='файл ответа модели')
    args = parser.parse_args()

    key = json.loads(Path(args.key).read_text(encoding='utf-8'))
    answer, note = load_answer(args.answer)
    view, canaries = key['view'], key['canaries']
    if answer is None:
        print(f'{view}: {note}. Поймано 0 из {len(canaries)} (ответ не прочитан).')
        return 2
    findings = answer.get('findings') if isinstance(answer, dict) else answer
    findings = [f for f in (findings or []) if isinstance(f, dict)]
    if note:
        print('Примечание:', note)

    hits = []
    for c in canaries:
        same_case = [f for f in findings if norm(f.get('case_id')) == norm(c['case_id'])]
        strict = [f for f in same_case
                  if norm(f.get('step_id')) == norm(c['step_id']) or norm(f.get('expectation_id')) == norm(c['expectation_id'])]
        hits.append((c, bool(strict), bool(same_case)))

    k = sum(h for _, h, _ in hits)
    k_case = sum(h for _, _, h in hits)
    severities = {}
    for f in findings:
        s = norm(f.get('severity')) or '—'
        severities[s] = severities.get(s, 0) + 1
    verdict = answer.get('verdict') if isinstance(answer, dict) else None
    print(f'Вид {view} ({key["bytes"] // 1000} КБ, кейсов {key["cases"]}): поймано {k} из {len(canaries)}'
          f' (назван хотя бы кейс: {k_case} из {len(canaries)})')
    print(f'Находок всего {len(findings)}: ' + ', '.join(f'{s} {n}' for s, n in sorted(severities.items())) + f'; вердикт: {verdict}')
    print('По видам ошибок:')
    for cls, title in CLASSES.items():
        rows = [h for c, h, _ in hits if c['cls'] == cls]
        if rows:
            print(f'  {title}: {sum(rows)} из {len(rows)}')
    body_status = [h for c, h, _ in hits if c['cls'] == 'state_check_removed' and re.search(r'body/status\b', c['what'])]
    if body_status:
        print(f'    из них убран body/status из тела ответа об ошибке (самый незаметный вариант): {sum(body_status)} из {len(body_status)}')
    print('По ошибкам (+ поймана, ~ назван только кейс, - пропущена):')
    for c, h, hc in hits:
        mark = '+' if h else ('~' if hc else '-')
        print(f'  {mark} {c["case_id"]} {c["step_id"]} [{c["cls"]}]')
    by_cls = ', '.join(f'{cls} {sum(h for c, h, _ in hits if c["cls"] == cls)}/{sum(1 for c, _, _ in hits if c["cls"] == cls)}'
                       for cls in CLASSES if any(c['cls'] == cls for c, _, _ in hits))
    print(f'\nСтрока для отчёта: {view} | поймано {k}/{len(canaries)} | {by_cls} | находок {len(findings)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
