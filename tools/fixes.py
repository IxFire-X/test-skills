"""P1: the one table ``code -> fix`` of the refusals an agent meets.

A refusal that carries its own fix (``error.fix``, a ``; fix: …`` tail, a row of ``diagnostics``) keeps it; the table is
what ``tools.json_cli.error_fields`` and ``HostStop`` fall back to, so every code has a way out in the envelope.  The
names of the codes are the refusals' own and do not change here; the texts say what to do, not what the rule is.
"""

from __future__ import annotations

# one rule for the three blocker codes (audit of the work: 6 refusals, 3 iterations over one question)
_BLOCKER_RULE = (
    "блокер ставится только на отсутствующий или неподтверждённый технический элемент шага; пара code → field_path: "
    "UNRESOLVED_OPERATION и UNSUPPORTED_AUTOMATION_CAPABILITY → /operation, UNRESOLVED_INPUT_BINDING → /inputs, "
    "UNRESOLVED_OUTPUT_BINDING → /outputs, UNRESOLVED_ASSERTION, UNSUPPORTED_ASSERTION_DIALECT и UNCONFIRMED_TYPE → "
    "/expectations/<n>/assertions. Шаг с блокерами: у каждого expectation с пустым assertions свой блокер на "
    "/expectations/<n>/assertions; операция есть, входы и утверждения связаны — блокер лишний, убери его. "
    'Пример: {"blocker_id": "BLOCK-<namespace>-1", "code": "UNRESOLVED_INPUT_BINDING", "field_path": "/inputs", '
    '"reason": "адрес не подтверждён", "provenance": ["<источник>"]}'
)

_STAGES = "context-marker, tc-generator, tc-reviewer, tc-to-autotest, autotest-reviewer"
_DELETE_FIELD = "удали поле {name} из ответа — код поставит его сам"

# P2: the whole review in one sentence — the fix of the refusals of its stage and the way out of `status` (item 5)
REVIEW_CHAIN = ("ревью — две команды: `tools.api review-open --run-root <run> --attempt <id> --output-dir <dir> --model-id <модель> "
                "--invocation-id <id вызова ревьюера> --fresh-context yes|no` → вызов ревьюера по пакету → `tools.api review-select "
                "--run-root <run> --attempt <id> --output-dir <dir> --verdict <файл ответа ревьюера>`")

FIXES: dict[str, str] = {
    # --- the review layer (moved from orchestrate_test_case_revision._FIXES, FaT-10d) ---
    "REVIEW_CONTEXT_LIMIT": "бюджет сессии меньше пакета ревью: сессия ещё ждёт вердикта — закрой её `tools.api review-abort --reason "
                            "REVIEW_CONTEXT_LIMIT --out <run>/tmp/session.json` и вызови select без --review; вердикт уже записан — "
                            "начни новый прогон (scan --new-run) и строй session-start командой `tools.api session-start`",
    "ORCHESTRATION_RECEIPT_SHAPE": "--candidate-receipt — файл candidate_bundle_receipt из вывода publish (его поля как есть)",
    "REVIEWER_PROTOCOL": "входы не совпадают с журналом: передай те же файлы, что open-session, и сессию из `tools.api complete-review`",
    "ORCHESTRATION_INPUT": "входы ревью не прошли проверку: передай файлы, которые отдали publish и open-session, без правок; "
                           "их перечень называет diagnostics",
    "ORCHESTRATION_PUBLICATION": "публикация не прочиталась обратно: ничего не правь руками, повтори команду; повторный отказ — "
                                 "сообщи код и run_id владельцу пакета",
    "ORCHESTRATION_RECEIPT": "квитанция не та: --candidate-receipt — candidate_bundle_receipt из вывода publish, остальные — "
                             "из журнала (`tools.api read-receipt`), без правок",
    "ORCHESTRATION_SELECTION": "выбор редакции отказан: смотри diagnostics, исправь названное в ответе ревьюера и повтори select",
    "ORCHESTRATION_VERIFICATION": "проверка выбранной редакции не прошла: ничего не правь руками, повтори select; "
                                  "повторный отказ — сообщи код и run_id владельцу пакета",
    "INDEPENDENCE_UNVERIFIED": "ревью требует свежего контекста: передай --fresh-context yes, если ревьюер запущен новым "
                               "сеансом; нового сеанса нет — прогон должен начинаться с scan --accept-self-review",
    "ATTEMPT_TERMINAL": "попытка закрыта, итог — report.md прогона; новая работа — новый прогон (scan --new-run)",
    # --- the review in two commands (P2) ---
    "FRESH_CONTEXT_REQUIRED": "--fresh-context yes — ревьюер отдельный субагент или вызов без истории генерации; ревью в той же сессии — "
                              "прогон должен начинаться с scan --accept-self-review (или review.self: true в .skillsrc)",
    "REVIEW_ALREADY_OPEN": "ревью уже открыто: повтори review-open с теми же --model-id, --invocation-id и --fresh-context или иди "
                           "дальше — review-select --verdict <файл ответа ревьюера>",
    "REVIEW_LEDGER_UNBOUND": "файл леджера ревьюера лежит без событий и это не та ревизия, что публикуется: повтори ту же команду с теми же входами "
                             "(она допишет события), иные входы — новое ревью, новый прогон (scan --new-run)",
    "RECEIPT_FILE_UNBOUND": "файл квитанции лежит без событий журнала, а его байты не те, что публикуются: повтори команду с теми же входами, что дали "
                            "файл (она допишет события), иные входы — новый прогон (scan --new-run)",
    "BASELINE_UNBOUND": "baseline прогона не сходится с записью попытки (файл подменён или прогон повреждён): начни новый прогон тем же scan с --new-run",
    "REVIEW_CLOSED": "ревью закрыто (сессия прервана или завершена), вердикт в него не добавить; новое ревью — новый прогон (scan --new-run)",
    "REVIEW_CANARY_MISMATCH": "карта канареек или вид ревьюера не совпадают с квитанцией review-canaries журнала: ничего не правь руками; "
                              "вид и карту строит `tools.api review-open` (или `python -m tools.review_view --candidate`) — если файлы "
                              "испорчены или удалены, начни прогон заново (scan --new-run) и сообщи код и run_id владельцу пакета",
    "REVIEW_ONLY_CANARY_FIXES": "все правки ответа — по контрольным ошибкам, которые код поставил в вид ревьюера: оставь эти находки с "
                                "severity INFO, правки по ним убери, вердикт вынеси по остальному (ПРИНЯТО, если больше ничего); отдай "
                                "ответ единственным повтором стадии (--transport-attempts 2)",
    "REVIEW_CANARY_ONLY_VERDICT": "отказ держится только на контрольных ошибках, которые код поставил в вид ревьюера: оставь эти находки с "
                                  "severity INFO, вердикт вынеси по остальному (ПРИНЯТО, если больше ничего); отдай ответ единственным "
                                  "повтором стадии (--transport-attempts 2)",
    "REVIEW_CODEWORD_NAMED": "ревьюер назвал контрольное слово генератора — он видел задачу генератора: проведи ревью в новой сессии "
                             "(/clear или новое окно: «проведи ревью test-skills <run_id>») и подай её ответ; ревью в той же сессии — "
                             "только если прогон начат с scan --accept-self-review",
    "MODEL_OUTPUT_TRUNCATED": "повтори стадию один раз (--transport-attempts 2) и попроси модель ответить тем же JSON целиком, компактно, без "
                              "отступов и переносов; дальше — finish, если повтор тоже оборван",
    "ANSWER_FILE_UNREADABLE": "--artifact/--verdict — путь к файлу ответа модели, который лежит на диске; запиши ответ в файл и подай его путь",
    "ANSWER_JSON_INVALID": "файл ответа — один JSON-объект в UTF-8 без комментариев и дублей ключей; исправь файл и подай его единственным "
                           "повтором стадии (--transport-attempts 2)",
    "COMMAND_NOT_IN_PACKAGE": "ревью — две команды: tools.api review-open … → вызов ревьюера → tools.api review-select --verdict <файл ответа ревьюера>",
    "LEGACY_REVIEW_NOT_IN_PACKAGE": "ревью — две команды: tools.api review-open --model-id … --invocation-id … --fresh-context yes|no → вызов "
                                    "ревьюера → tools.api review-select --verdict <файл ответа ревьюера>",
    "REVIEW_NOT_OPEN": "сначала " + REVIEW_CHAIN,
    "REVIEWER_RESPONSE_MISSING": "ответ ревьюера не записан: tools.api review-select --verdict <файл ответа ревьюера> записывает его и "
                                 "выбирает ревизию; ревьюера ещё не вызывали — " + REVIEW_CHAIN,
    "GENERATOR_INPUT_HEADER": "удали header_digest из grouping — команда plan поставит digest заголовка сама",
    # --- the model stages of the journal ---
    "MODEL_REQUEST_INVALID": "model-request --stage <стадия>:<экземпляр>; стадии: " + _STAGES + "; --attempt — attempt_id этого "
                             "прогона (run_pipeline status); --input-digest на каждый вход стадии по порядку",
    "MODEL_REQUEST_DUPLICATE": "у стадии один запрос: не повторяй model-request, ответ отдай в model-response",
    "MODEL_REQUEST_OUT_OF_ORDER": "стадии цепочки идут по порядку: сделай model-request следующей стадии, названной в сообщении",
    "MODEL_INPUTS_MISMATCH": "входы стадии не совпадают с журналом: возьми digest и источник из сообщения отказа (он называет верный "
                             "digest каждой позиции) и повтори model-request; у ревьюера кейсов запроса вручную нет — " + REVIEW_CHAIN,
    "MODEL_STAGE_ARTIFACT_INVALID": "ответ не прошёл схему стадии: исправь поля из diagnostics и отдай ответ единственным повтором "
                                    "стадии (--transport-attempts 2)",
    "MODEL_RESPONSE_DIGEST_MISMATCH": "удали из ответа поля digest, названные в сообщении, — код поставит их сам; отдай ответ "
                                      "единственным повтором стадии (--transport-attempts 2)",
    "MODEL_REUSE_REFUSED": "переиспользовать можно только ответ context-marker принятого прогона того же проекта; "
                           "иначе model-request без --reuse-from",
    "MODEL_REUSE_MISMATCH": "ответ переиспользуемой стадии не меняется: передай файл ответа источника как есть",
    "MODEL_STAGE_ANSWERED": "у стадии уже есть ответ, другой его не заменит; дальше — следующая стадия",
    "MODEL_STAGE_NOT_REQUESTED": "сначала tools.api model-request этой стадии, потом model-response",
    # --- batches (BatchAssemblyError) ---
    "BATCH_ID_CONFLICT": "batch_id, namespace и идентификаторы фрагмента должны быть из плана пачки: возьми их из batch-plan.json "
                         "и не меняй; идентификаторы внутри фрагмента начинаются с namespace пачки",
    "BATCH_PARTITION_CONFLICT": "фрагмент закрывает ровно требования своей пачки: owned_source_requirement_ids — как в плане, "
                                "mappings — по одному на каждое из них, кейс ссылается только на требования своей пачки",
    "BATCH_SEMANTIC_CONFLICT": "смотри diagnostics: у каждой строки свой путь и fix; исправь все за один заход и повтори validate-fragment",
    "BATCH_UNCOVERED_REQUIREMENT": "у каждого требования пачки должен быть кейс: добавь кейс с этим requirement_ids (или кейс с "
                                   "явным блокером) — это ошибка ответа, стадия имеет единственный повтор",
    "BATCH_CAPABILITY_CONFLICT": "одна capability_id — одно описание во всех пачках: сделай описания одинаковыми или дай разные id",
    "IMPORT_HUMAN_FIELD_CHANGED": "кейс из CSV импорта не переписывается: верни человеческие поля (title, шаги, ожидания) как в CSV",
    "IMPORT_TRACE_CHANGED": "requirement_ids кейса импорта должны вести только к требованиям его строки CSV: верни трассировку",
    # --- cases import (moved from cases_import._FIXES) ---
    "IMPORT_CASE_LINKS": "в ответе context-marker:requirements у каждой строки-кейса CSV ровно одна связь case_links: row — номер "
                         "строки кейса, source_requirement_ids — SREQ этого ответа, пустой список только с reason; отдай исправленный "
                         "ответ единственным повтором стадии (--transport-attempts 2), ничего не записано",
    "IMPORT_LINKS_NOT_ANSWERED": "при --docs требования и связи кейсов размечает модель: сначала model-request и model-response "
                                 "context-marker:requirements, потом cases-import",
    "IMPORT_CSV_V4": "выгрузите кейсы в профиле v5 (по умолчанию) или экспортом из Zephyr — в Test Data должен быть текст человека",
    "ZEPHYR_CSV_COLUMNS": "нужен экспорт тест-кейсов Zephyr Scale в CSV с колонками шаблона; лишние колонки уберите из выгрузки",
    "ZEPHYR_CSV_ROWS": "заполните названные ячейки CSV: у кейса Name и хотя бы один шаг, у каждого шага Step и Expected Result — "
                       "наблюдаемый результат системы, не «успешно», «ok» или «—»; потом повторите scan --cases",
    "IMPORT_CSV_SECRET": "уберите секрет из CSV (пароль, токен, учётные данные в адресе): замените значение плейсхолдером <…> "
                         "(например <пароль>) и повторите scan --cases; файл с секретом модель не увидит",
    "ZEPHYR_CSV_ENCODING": "сохраните CSV в UTF-8 (Excel: Файл → Сохранить как → CSV UTF-8)",
    "ZEPHYR_CSV_NOT_CSV": "нужен экспорт тест-кейсов Zephyr Scale в CSV",
    "ZEPHYR_CSV_DELIMITER": "нужен CSV с разделителем «,» или «;» и колонками Zephyr (Name, …)",
    "IMPORT_NOT_CASES_RUN": "cases-import — для прогона scan --cases; у этого прогона требования размечает модель",
    "IMPORT_CSV_CHANGED": "CSV изменился после scan: повторите scan --cases --new-run",
    "IMPORT_REQUIREMENTS_INVALID": "требования из CSV не прошли проверку ответа разметки — запрос не записан; исправьте названные "
                                   "ячейки CSV и повторите scan --cases --new-run",
    "ZEPHYR_CSV_PROFILE": "--csv-profile — zephyr-scale-step-row-24-v5 (по умолчанию, текст человека) или zephyr-scale-step-row-24-v4 "
                          "(плюс машинные колонки шагов); профили v1–v3 только читаются; других значений нет",
    # --- the host (HostStop without a fix at its raise site) ---
    "FINISH_FACT_UNDETERMINED": "у прогона нет попытки: finish закрывает попытку, а её нет — сначала дойди до model-request "
                                "(run_pipeline status называет следующий шаг); для прогона, где работа не начата, "
                                "достаточно scan --new-run",
    "INVENTORY_INVALID": "опись проекта не читается или не сходится с диском: повтори scan (--new-run), файлы проекта не меняй во время scan; "
                         "если detail называет module или attempt.schema.json — проверь в .skillsrc root модуля: путь существующей папки проекта",
    "SKILLSRC_UNREADABLE": ".skillsrc должен быть обычным файлом внутри проекта: уберите каталог, junction или ссылку с этим именем "
                           "(или права на чтение) и повторите scan; нужен новый — `python -m tools.init_skillsrc --project <проект> --write`",
    "SOURCE_TARGET_INVALID": "--target — путь внутри выбранного модуля проекта (относительно корня проекта), без «..»",
    "SNAPSHOT_DOCS": "--docs и --reference — пути внутри проекта к существующим файлам или папкам, не ссылкам и не junction",
    "EXECUTION_COORDINATES": "--run — один точный run_id этого проекта из run_pipeline status; --project — корень проекта прогона",
    "EXECUTION_RECEIPT": "выполнение закончилось, а квитанция не записана или не читается: повтори команду (она идемпотентна), "
                         "руками журнал не правь",
    "EXECUTION_RECOVERY": "начатый запуск тестов не восстанавливается из журнала: run_pipeline status называет состояние, "
                          "дальше — finish или новый прогон",
    "BASELINE_INCOMPLETE": "замер состояния проекта до запуска не читается: повтори prepare-execution (замер делает код), файлы проекта не меняй",
    "RERUN_COORDINATES": "--run — один точный run_id прогона, который нужно перезапустить, --attempt — его attempt_id",
    # --- bare ValueErrors that became codes ---
    "INVALID_ARGUMENT": "проверь флаги команды: tools.api <команда> --help",
    # --- the audit of the canon: the order is the code's work, the rest is the agent's ---
    "CANONICAL_REQUIREMENT_ID_ORDER": "requirement_ids кейса — в порядке requirements документа (display_order); validate-fragment, "
                                      "model-response и assemble ставят порядок сами",
    "CANONICAL_CATEGORY_ORDER": "categories кейса — в порядке positive, negative, boundary, authorization, security, concurrency, "
                                "idempotency, observability, functional; validate-fragment, model-response и assemble ставят порядок сами",
    "CANONICAL_ARGUMENT_ORDER": "arguments capability — по name в порядке кодовых точек Unicode",
    "CANONICAL_RESULT_ORDER": "results capability — по name в порядке кодовых точек Unicode",
    "CANONICAL_CAPABILITY_ORDER": "operation_capabilities — по capability_id в порядке кодовых точек Unicode",
    "CANONICAL_BLOCKER_ORDER": "automation_blockers шага — по blocker_id в порядке кодовых точек Unicode",
    "CANONICAL_SOURCE_REQUIREMENT_ORDER": "source_requirements — документы подряд, внутри документа по номеру строки как числу",
    "CANONICAL_DISPLAY_ORDER": "display_order элементов списка — 1, 2, 3 … подряд без пропусков и повторов; удали поле display_order — код поставит его сам по позиции",
    "SCOPE_PLAN_UNREADABLE": "опубликованный план пачек есть в журнале, но не читается (файл удалён или изменён): охват не берётся из scope-decision.json; "
                             "run_pipeline status называет файл — восстановить его нельзя, начни новый прогон: scan --new-run",
    "RECEIPT_MISSING": "квитанции в прогоне нет: её пишет код шага, который её создаёт; run_pipeline status называет следующий шаг, файлы руками не создавай",
    "CANONICAL_DOCUMENT_ID": "document_id — безопасный идентификатор (буквы, цифры, . _ : -), как в заголовке пачки; поле не меняй",
    "CANONICAL_NONFINITE_NUMBER": "числа в JSON конечные: NaN и Infinity не допускаются",
    "CANONICAL_REVISION_TYPE": "revision — целое число, 1 для нового документа",
    "SEMANTIC_BLOCKER_PATH": _BLOCKER_RULE,
    "SEMANTIC_MISSING_ASSERTION_BLOCKER": _BLOCKER_RULE,
    "SEMANTIC_SPURIOUS_BLOCKER": _BLOCKER_RULE,
    "SEMANTIC_MISSING_OPERATION_BLOCKER": "у шага без operation (null) должен быть блокер с field_path /operation, например "
                                          '{"blocker_id": "BLOCK-<namespace>-1", "code": "UNRESOLVED_OPERATION", "field_path": "/operation", '
                                          '"reason": "…", "provenance": ["…"]}; либо опиши operation',
    "SEMANTIC_HUMAN_EXPECTATION_BODY_MISSING": "в конец text ожидания допиши JSON-шаблон тела ответа с отступом (полями, на которые "
                                               "есть утверждения http_body); значения — из утверждений или человеческие плейсхолдеры "
                                               "вроде <идентификатор>; для not_exists шаблон не нужен",
    "SEMANTIC_HUMAN_EXPECTATION_FIELD_MISSING": "добавь в JSON-шаблон text ожидания поле, на которое есть утверждение (путь — в message)",
    "SEMANTIC_HUMAN_EXPECTATION_UNEXPECTED_FIELD": "убери из JSON-шаблона text поле, на отсутствие которого есть утверждение not_exists",
    "SEMANTIC_HUMAN_EXPECTATION_PLACEHOLDER": "для утверждения exists в шаблоне text — плейсхолдер вроде <идентификатор>, не придуманное значение",
    "SEMANTIC_HUMAN_EXPECTATION_LITERAL_MISMATCH": "значение в JSON-шаблоне text должно равняться литералу утверждения equals",
    "SEMANTIC_HUMAN_BODY_MISSING": "в конец test_data шага допиши полный JSON тела запроса с отступом — по нему идут входы body",
    "SEMANTIC_HUMAN_BODY_UNBOUND": "каждое листовое поле JSON тела в test_data должно иметь вход location=body с pointer (RFC 6901) "
                                   "этого поля: добавь недостающие входы (их перечень — в message), контейнер объектом не связывай",
    "SEMANTIC_HUMAN_BODY_BINDING_MISSING": "вход body указывает на поле, которого нет в JSON тела test_data: поправь pointer или допиши поле",
    "SEMANTIC_HUMAN_BODY_LITERAL_MISMATCH": "значение в JSON тела test_data должно равняться literal входа body по этому pointer",
    "SEMANTIC_HUMAN_BODY_OPAQUE_CONTAINER": "вход не-литерал не может владеть объектом или массивом с конкретными листьями: "
                                            "свяжи листья отдельными входами или заблокируй входы (/inputs)",
    "SEMANTIC_HUMAN_BODY_DYNAMIC_LITERAL": "вход не-литерал показывай в JSON тела плейсхолдером вроде <идентификатор>, не значением",
    "SEMANTIC_HUMAN_HTTP_REQUEST_FORMAT": "test_data HTTP-шага: полный JSON тела с отступом или ровно «Тело запроса отсутствует.»",
    "SEMANTIC_HUMAN_HTTP_RESULT_FORMAT": "text ожидания: описание наблюдаемого результата, пустая строка и «HTTP <код> <причина>» "
                                         "подтверждённого статуса (причина как в стандарте)",
    "SEMANTIC_HUMAN_EXPECTED_PLACEHOLDER": "text ожидания описывает наблюдаемый результат системы, не отметку выполнения теста",
    "SEMANTIC_BODY_TARGET_OVERLAP": "pointer входов body не должны совпадать и не должны быть предком/потомком друг друга: оставь "
                                    "листовые входы, вложенные указатели того же поля удали",
    "SEMANTIC_DUPLICATE_TARGET": "у каждой цели HTTP-входа (location + name или pointer) одна привязка: удали повтор",
    "SEMANTIC_DUPLICATE_ID": "идентификаторы уникальны во всём документе: переименуй повтор",
    "SEMANTIC_CAPABILITY_TYPE_MISMATCH": "семантический тип входа/выхода должен совпадать с типом аргумента/результата capability",
    "SEMANTIC_DUPLICATE_CAPABILITY_ACTION": "одно действие (adapter + action) — одна capability: объедини описания",
    "SEMANTIC_DUPLICATE_CAPABILITY_ARGUMENT": "у каждого аргумента capability не больше одного входа шага: удали повтор",
    "SEMANTIC_FORWARD_STEP_OUTPUT": "вход или утверждение ссылается на выход шага, который идёт позже: поменяй порядок шагов или источник",
    "SEMANTIC_HTTP_PATH_BINDINGS": "каждому {плейсхолдеру} пути HTTP нужен вход location=path с тем же name (и наоборот)",
    "SEMANTIC_MANUAL_STEP_OUTPUT": "автоматизируемый шаг не берёт выход ручного шага (manual_only): возьми значение из другого источника "
                                   "или сделай и этот шаг ручным",
    "SEMANTIC_OPERATION_ASSERTION_KIND": "вид actual утверждения не подходит операции шага: у http — http_status, http_header, http_body; "
                                         "у project_action — project_result",
    "SEMANTIC_OPERATION_OUTPUT_KIND": "вид source выхода не подходит операции шага: у http — http_status, http_header, http_body; "
                                      "у project_action — project_result",
    "SEMANTIC_OPERATION_TARGET_KIND": "location входа не подходит операции шага: path/query/header/body — для http, arg — для project_action",
    "SEMANTIC_OPERATOR_TYPE": "оператор утверждения не подходит типу значения: сравнения больше/меньше — для чисел, matches — для строк",
    "SEMANTIC_OUTPUT_TYPE": "semantic_type выхода не подходит его источнику",
    "SEMANTIC_TARGET_TYPE": "semantic_type входа не подходит его цели",
    "SEMANTIC_TYPE_MISMATCH": "тип источника и тип цели различаются: приведи semantic_type к одному",
    "SEMANTIC_PARAM_GROUP_MISMATCH": "кейсы одной param_group различаются только значениями одного параметра: выровняй остальное",
    "SEMANTIC_PARAM_GROUP_PARAMETER": "в группе param_group варьируется ровно один параметр дизайна, у каждого кейса свой вариант: проверь design_refs",
    "SEMANTIC_PARAM_GROUP_SINGLE": "param_group из одного кейса не бывает: убери группу или добавь вариант",
    "SEMANTIC_REQUIRED_CAPABILITY_ARGUMENT": "каждому обязательному аргументу capability нужен ровно один вход шага (location=arg)",
    "SEMANTIC_UNDECLARED_CAPABILITY_ARGUMENT": "вход arg ссылается на аргумент, которого нет в capability: объяви его или убери вход",
    "SEMANTIC_UNDECLARED_CAPABILITY_RESULT": "выход project_result ссылается на результат, которого нет в capability: объяви его или убери выход",
    "SEMANTIC_RU_RU_HUMAN_FIELDS": "человеческие поля (title, objective, action, text …) — по-русски, content_locale ru-RU",
    "SEMANTIC_SECRET_HANDLE_IN_HUMAN_FIELD": "значение секрета в человеческом поле не пишется: ссылка на secret_handle через safe_label",
    "SEMANTIC_UNCOVERED_REQUIREMENT": "у каждого требования должен быть кейс: добавь кейс с его requirement_ids",
    "SEMANTIC_UNKNOWN_CANONICAL_REQUIREMENT": "ссылка на требование, которого нет в requirements: поправь id или добавь требование",
    "SEMANTIC_UNKNOWN_CAPABILITY": "operation.capability_id ссылается на необъявленную capability: объяви её в operation_capabilities",
    "SEMANTIC_UNKNOWN_REQUIREMENT": "requirement_ids кейса ссылаются на требование, которого нет в requirements: поправь id",
    "SEMANTIC_UNKNOWN_SOURCE_REQUIREMENT": "mapping ссылается на SREQ, которого нет в плане: возьми id из batch-plan.json",
    "SEMANTIC_UNKNOWN_STEP_OUTPUT": "step_output ссылается на несуществующий выход шага: step_id шага — STEP-<хвост case_id после TC->-<NN, номер шага в кейсе двумя цифрами> "
                                    "(STEP-B1-003-02 — шаг 2 кейса TC-B1-003), output_id — имя выхода этого шага; поправь ссылку или объяви выход",
    "SEMANTIC_UNMAPPED_CANONICAL_REQUIREMENT": "у каждого требования канона должен быть mapping с исходным SREQ",
    "SEMANTIC_UNMAPPED_SOURCE_REQUIREMENT": "у каждого SREQ пачки должен быть mapping: добавь недостающий",
    # --- the paths the agent names (tools.output_guard) ---
    "API_OUT_OUTSIDE_RUN": "добавь --run-root <run> (существующий прогон) и пиши файл в <run>/tmp/<имя>.json",
    "OUTPUT_DIR_OUTSIDE_PROJECT": "каталог публикации — внутри проекта (например <project>/test-cases/) и не внутри .git; "
                                  "повтори с таким --output-dir",
    # --- the pause after the marker (tools.scope_pause) ---
    "SCOPE_DECISION_MISMATCH": "scope-decision.json расходится с опубликованным планом: верь плану (out_of_scope), файл не правь; другое решение — новый прогон (scan --new-run)",
    "SCAN_INCOMPLETE": "повтори тот же scan (те же --module, --docs, --profile): он достроит недостающие пачки контекста в этом прогоне",
    "SCOPE_DECISION_INVALID": "не правь артефакты прогона руками; другое решение — новый прогон (scan --new-run)",
    # --- the schema: every SCHEMA_* has the generic fix of fix_for; these are the ones that need more ---
    "SCHEMA_ENUM": "значение вне перечня — перечень в message; category: positive, negative, boundary, authorization, security, "
                   "concurrency, idempotency, observability, functional; priority: CRITICAL, HIGH, MEDIUM, LOW; technique: EP, "
                   "BVA, DT, PAIRWISE, OTHER",
    "SCHEMA_ONE_OF": "значение не подходит ни одной из форм: inputs — target {location: path|query|header, name, sensitive} или "
                     "{location: body, pointer, sensitive}, semantic_type — соседнее поле target, не внутри; outputs — source "
                     "{kind: http_status} | {kind: http_header|project_result, name} | {kind: http_body, pointer}, источников log и audit "
                     "нет: такое наблюдение — утверждение ожидания или его блокер",
    "SCHEMA_REQUIRED": "не хватает обязательного поля (его имя — в path): утверждение assertion требует assertion_id, display_order, "
                       "actual, operator и expected (кроме exists и not_exists)",
    # --- P8a-1: a delivery without the execution files ---
    "PROFILE_NOT_IN_PACKAGE": "в этой поставке нет исполнения тестов — повтори команду без --profile или с --profile cases-only-v1: "
                              "кейсы и отчёт доступны, автотесты запускает тот, у кого есть полная поставка",
    "MODE_B_NOT_IN_PACKAGE": "эта поставка — для кейсов, режима автотестов в ней нет: не вызывай эту команду; кейсы и отчёт доступны "
                             "(scan без --profile, status, report, finish)",
    # --- P4: the build is run by a human (materialize prints the command, ingest takes the reports) ---
    "INGEST_WITHOUT_MATERIALIZE": "сначала `run_pipeline materialize --project <project> --run <run_id>`: он кладёт тесты в проект и печатает команду для человека",
    "INGEST_REPORT_MISSING": "запустите команду из materialize (`materialize --run <run_id>` напечатает её снова) и повторите ingest; сборка не дошла до тестов — "
                             "`ingest --no-reports --reason BUILD_FAILED|TESTS_NOT_STARTED`",
    "INGEST_REPORT_STALE": "в каталоге только отчёты, лежавшие до materialize: запустите команду из materialize заново и повторите ingest",
    "INGEST_REPORT_OUTSIDE_MODULE": "отчёты должны лежать внутри модуля (без ссылок) или в <run>/incoming/: скопируйте XML туда и повторите ingest с --reports",
    "INGEST_REPORT_MISMATCH": "в отчётах не те тесты, что рецензированы (чужой модуль, лишние, недостающие, дубликаты): запустите команду из materialize целиком "
                              "и повторите ingest",
    "INGEST_CHALLENGE_MISSING": "в отчёте нет слова команды из materialize: отчёт получен не этой командой; запустите её и повторите ingest",
    "INGEST_CHALLENGE_MISMATCH": "в отчёте слово другого запуска: запустите команду этой попытки (materialize напечатает её) и повторите ingest",
    "INGEST_TESTS_CHANGED": "верните файлы тестов командой `materialize --run <run_id> --restore`, запустите команду из materialize и повторите ingest; "
                            "вернуть нельзя — `ingest --no-reports --reason RESULT_UNAVAILABLE`",
    "INGEST_REQUEST_CHANGED": "проект или среда модуля изменились после materialize (имена файлов в message): верните их или начните новый прогон",
    "INGEST_REPORT_CHANGED": "отчёты другие, чем те, по которым записан результат; результат не меняется — для нового прогона `scan --new-run`",
    "INGEST_REASON_WITH_REPORTS": "оставьте одно: отчёты — `--reports <каталог>`, их отсутствие — `--no-reports --reason …`",
    "INGEST_REASON_UNKNOWN": "`--no-reports` требует `--reason BUILD_FAILED|TESTS_NOT_STARTED|RESULT_UNAVAILABLE`, а `--reason` без `--no-reports` не принимается",
    "INGEST_REASON_AFTER_RESULT": "результат попытки уже записан: ingest без --no-reports печатает его снова",
    "MATERIALIZE_RESTORE_NOTHING": "тесты ещё не положены в проект: `materialize` без --restore",
    "MATERIALIZATION_COMMAND_UNBOUND": "файл команды materialize лежит без событий журнала: повторите `materialize` с теми же аргументами",
}

_SCHEMA_GENERIC = "приведи значение по пути path к схеме: что именно не так, сказано в message"


def fix_for(code: str | None) -> str | None:
    """The fix of a refusal code: the table, else the generic one of a ``SCHEMA_*`` code, else None."""
    if not code:
        return None
    found = FIXES.get(code)
    if found is None and code.startswith("SCHEMA_"):
        return _SCHEMA_GENERIC
    return found


def delete_field_fix(name: str) -> str:
    """The fix of a field the code puts itself (a digest) that the answer gave with another value."""
    return _DELETE_FIELD.format(name=name)
