# Образец ответа автотестов (r1)

Один JSON-файл. Значения в `<…>` — из вида документа и `read`; id кейсов, шагов, ожиданий и проверок — как в виде, целиком.

```json
{"schema_version":"5.0.0","stage":"tc-to-autotest","warnings":[],"artifacts":{"automation_status":"GENERATED","automation_revision":1,"predecessor_automation_sha256":null,"correction_review_sha256":null,
"source":{"document_id":"<TCDOC-…>","revision":1},
"generated_files":[{"file_id":"FILE-1","path":"<путь класса по FQN под корнем тестов модуля>","language":"java","framework":"junit5","content":"<текст класса>"}],
"generated_symbols":[{"file_id":"FILE-1","symbol_id":"SYMBOL-001","locator":{"kind":"java_class_method","class_fqn":"<FQN>","method_name":"<метод>"}}],
"implementation_relations":[
{"kind":"operation","case_id":"TC-B1-001","step_id":"STEP-B1-001-01","file_id":"FILE-1","symbol_id":"SYMBOL-001"},
{"kind":"assertion","case_id":"TC-B1-001","step_id":"STEP-B1-001-01","expectation_id":"EXP-B1-001-01-1","assertion_id":"ASSERT-B1-001-01-1-1","file_id":"FILE-1","symbol_id":"SYMBOL-001"}],
"manual_dispositions":[],"diagnostics":[]}}
```

Код ставит сам (не пиши): `source.source_digest`, `source.effective_bundle_receipt_digest`, `content_digest` каждого файла. В r1 оба ключа `predecessor_automation_sha256` и `correction_review_sha256` пиши как в образце — `null`. Ответ r2 — то же с `automation_revision: 2` и полным новым набором; в r2 оба ключа пустыми (код ставит digest ответа и ревью r1). Каждая операция и каждая проверка кейса — своя строка связи.
