"""The one table ``code -> fix`` of the refusals an agent meets.

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

# the whole review in one sentence — the fix of the refusals of its stage and the way out of `status` (item 5)
REVIEW_CHAIN = ("ревью — `tools.api prepare --run-root <run> --stage tc-reviewer:canonical --fresh-context yes|no [--invocation-id <метка сессии ревьюера>]` → вызов ревьюера по пакету, его ответ — в `incoming/tc-reviewer-canonical.json` прогона → `tools.api submit --run-root <run> --stage tc-reviewer:canonical`")

FIXES: dict[str, str] = {
    # --- the review layer (moved from orchestrate_test_case_revision._FIXES) ---
    "REVIEW_CONTEXT_LIMIT": "бюджет сессии меньше пакета ревью: сессия ещё ждёт вердикта — закрой её `tools.api submit --run-root <run> --stage tc-reviewer:canonical "
                            "--abort REVIEW_CONTEXT_LIMIT`; вердикт уже записан — начни новый прогон (scan --new-run) и открой ревью заново командой `tools.api prepare --stage tc-reviewer:canonical`",
    "REVIEWER_PROTOCOL": "входы не совпадают с журналом: повтори `tools.api prepare` / `submit` со стадией `tc-reviewer:canonical` — код берёт входы из журнала",
    "ORCHESTRATION_INPUT": "входы ревью не прошли проверку: их перечень называет diagnostics; файлов у агента нет — повтори `tools.api submit --stage tc-reviewer:canonical`",
    "ORCHESTRATION_PUBLICATION": "публикация не прочиталась обратно: ничего не правь руками, повтори команду; повторный отказ — "
                                 "сообщи код и run_id владельцу пакета",
    "ORCHESTRATION_RECEIPT": "квитанция не та: ничего не правь руками, квитанции берутся из журнала; повтори `tools.api submit --stage tc-reviewer:canonical`",
    "ORCHESTRATION_SELECTION": "выбор редакции отказан: смотри diagnostics, исправь названное в ответе ревьюера и повтори `tools.api submit --stage tc-reviewer:canonical`",
    "ORCHESTRATION_VERIFICATION": "проверка выбранной редакции не прошла: ничего не правь руками, повтори `tools.api submit --stage tc-reviewer:canonical`; "
                                  "повторный отказ — сообщи код и run_id владельцу пакета",
    "INDEPENDENCE_UNVERIFIED": "ревью требует свежего контекста: передай --fresh-context yes, если ревьюер запущен новым "
                               "сеансом; нового сеанса нет — человек согласен на ревью в той же сессии: повтори prepare с --fresh-context no --accept-self-review",
    "ATTEMPT_TERMINAL": "попытка закрыта, итог — report.md прогона; новая работа — новый прогон (scan --new-run)",
    # --- the review in two commands ---
    "FRESH_CONTEXT_REQUIRED": "--fresh-context yes — ревьюер отдельный субагент или вызов без истории генерации; ревью в той же сессии — "
                              "человек согласен на ревью в той же сессии: --fresh-context no --accept-self-review (или review.self: true в .skillsrc)",
    "REVIEW_ALREADY_OPEN": "ревью уже открыто: повтори `prepare --stage tc-reviewer:canonical` с теми же --invocation-id и --fresh-context или иди "
                           "дальше — ответ ревьюера в слот и `submit --stage tc-reviewer:canonical`",
    "REVIEW_LEDGER_UNBOUND": "файл леджера ревьюера лежит без событий и это не та ревизия, что публикуется: повтори ту же команду с теми же входами "
                             "(она допишет события), иные входы — новое ревью, новый прогон (scan --new-run)",
    "RECEIPT_FILE_UNBOUND": "файл квитанции лежит без событий журнала, а его байты не те, что публикуются: повтори команду с теми же входами, что дали "
                            "файл (она допишет события), иные входы — новый прогон (scan --new-run)",
    "BASELINE_UNBOUND": "baseline прогона не сходится с записью попытки (файл подменён или прогон повреждён): начни новый прогон тем же scan с --new-run",
    "REVIEW_CLOSED": "ревью закрыто (сессия прервана или завершена), вердикт в него не добавить; новое ревью — новый прогон (scan --new-run)",
    "REVIEW_CANARY_MISMATCH": "карта канареек или вид ревьюера не совпадают с квитанцией review-canaries журнала: ничего не правь руками; "
                              "вид и карту строит `tools.api prepare --stage tc-reviewer:canonical` (или `python -m tools.review_view --candidate`) — если файлы "
                              "испорчены или удалены, начни прогон заново (scan --new-run) и сообщи код и run_id владельцу пакета",
    "REVIEW_ONLY_CANARY_FIXES": "все правки ответа — по контрольным ошибкам, которые код поставил в вид ревьюера: оставь эти находки с "
                                "severity INFO, правки по ним убери, вердикт вынеси по остальному (ПРИНЯТО, если больше ничего); отдай "
                                "ответ единственным повтором стадии (--transport-attempts 2)",
    "REVIEW_CODEWORD_NAMED": "ревьюер назвал контрольное слово генератора — он видел задачу генератора: проведи ревью в новой сессии "
                             "(/clear или новое окно: «проведи ревью test-skills <run_id>») и подай её ответ; ревью в той же сессии — "
                             "только если человек согласен: prepare с --fresh-context no --accept-self-review",
    "MODEL_OUTPUT_TRUNCATED": "повтори стадию один раз (--transport-attempts 2) и попроси модель ответить тем же JSON целиком, компактно, без "
                              "отступов и переносов; дальше — finish, если повтор тоже оборван",
    "ANSWER_FILE_UNREADABLE": "файл ответа не читается: ответ модели лежит в слоте стадии (`answer_slot` из `prepare` или `status`, `incoming/<стадия>.json`); запиши ответ туда и повтори `submit`",
    "ANSWER_JSON_INVALID": 'ответ целый, но не разбирается как JSON (место ошибки — в сообщении): запиши его заново целиком одним JSON-объектом в UTF-8 — без текста до и после, без комментариев и дублей ключей, кавычки внутри строк экранированы; подай новый файл единственным повтором стадии (--transport-attempts 2)',
    "REVIEW_NOT_OPEN": "сначала " + REVIEW_CHAIN,
    "REVIEWER_RESPONSE_MISSING": "ответ ревьюера не записан: положи его в `incoming/tc-reviewer-canonical.json` и повтори `tools.api submit --stage tc-reviewer:canonical`; "
                                 "ревьюера ещё не вызывали — " + REVIEW_CHAIN,
    # --- the model stages of the journal ---
    "MODEL_REQUEST_INVALID": "prepare --stage <стадия>:<экземпляр>; стадии: " + _STAGES + "; стадию с номером ревизии называет run_pipeline status (next.stage)",
    "MODEL_REQUEST_DUPLICATE": "у стадии один запрос: повтори prepare (он отдаёт тот же запрос), ответ отдай в submit",
    "MODEL_REQUEST_OUT_OF_ORDER": "стадии цепочки идут по порядку: сделай prepare следующей стадии, названной в сообщении (run_pipeline status: next.stage)",
    "MODEL_INPUTS_MISMATCH": "входы стадии не совпадают с журналом: возьми digest и источник из сообщения отказа (он называет верный "
                             "digest каждой позиции) и повтори prepare этой стадии (входы он берёт из журнала сам); у ревьюера кейсов запроса вручную нет — " + REVIEW_CHAIN,
    "MODEL_STAGE_ARTIFACT_INVALID": "ответ не прошёл схему стадии: исправь поля из diagnostics и отдай ответ единственным повтором "
                                    "стадии (--transport-attempts 2)",
    "MODEL_RESPONSE_DIGEST_MISMATCH": "удали из ответа поля digest, названные в сообщении, — код поставит их сам; отдай ответ "
                                      "единственным повтором стадии (--transport-attempts 2)",
    "MODEL_REUSE_REFUSED": "переиспользовать можно разметку, plan, пачки и ревью кейсов принятого целиком прогона того же проекта с теми же входами; "
                           "причина названа в сообщении; иначе prepare без --reuse-from",
    "MODEL_REUSE_MISMATCH": "ответ переиспользуемой стадии не меняется и в слот не кладётся: prepare --stage <стадия> --reuse-from <run_id> берёт его из источника сам",
    "REUSE_SOURCE_INVALID": "файл прошлого прогона, названный в сообщении, повреждён или изменён — из него ничего не берётся и в новый прогон ничего не записано; "
                            "покажи это человеку; иначе prepare без --reuse-from",
    "MODEL_STAGE_ANSWERED": "у стадии уже есть ответ, другой его не заменит; дальше — следующая стадия",
    "MODEL_STAGE_NOT_REQUESTED": "сначала запрос стадии (prepare --stage <стадия>), потом ответ в слот и submit",
    # --- batches (BatchAssemblyError) ---
    "BATCH_ID_CONFLICT": "batch_id, namespace и идентификаторы фрагмента должны быть из плана пачки: возьми их из batch-plan.json "
                         "и не меняй; идентификаторы внутри фрагмента начинаются с namespace пачки",
    "BATCH_PARTITION_CONFLICT": "фрагмент закрывает ровно требования своей пачки: owned_source_requirement_ids — как в плане, "
                                "mappings — по одному на каждое из них, кейс ссылается только на требования своей пачки",
    "BATCH_SEMANTIC_CONFLICT": "смотри diagnostics: у каждой строки свой путь и fix; исправь все за один заход и подай фрагмент единственным повтором стадии (submit --transport-attempts 2)",
    "BATCH_UNCOVERED_REQUIREMENT": "у каждого требования пачки должен быть кейс: добавь кейс с этим requirement_ids (или кейс с "
                                   "явным блокером) — это ошибка ответа, стадия имеет единственный повтор",
    "BATCH_CAPABILITY_CONFLICT": "одна capability_id — одно описание во всех пачках: сделай описания одинаковыми или дай разные id",
    "IMPORT_HUMAN_FIELD_CHANGED": "кейс из CSV импорта не переписывается: верни человеческие поля (title, шаги, ожидания) как в CSV; кейсов в пачке — по одному на кейс CSV плюс новые под requirements из `write` скелета, у новых Key пуст",
    "IMPORT_TRACE_CHANGED": "requirement_ids кейса импорта должны вести только к требованиям его строки CSV (новый кейс — только к требованиям из `write`): верни трассировку",
    # --- cases import (moved from cases_import._FIXES) ---
    "IMPORT_CASE_LINKS": "в ответе context-marker:requirements у каждой строки-кейса CSV ровно одна связь case_links: row — номер "
                         "записи кейса в CSV (шапка — 1, первый кейс — 2), source_requirement_ids — SREQ этого ответа, пустой список только с reason; требование, добавленное изменением (ADDED), ни с одной строкой не связывай — под него пишутся новые кейсы; verdict (fits / rewrite / obsolete) "
                         "обязателен на судимых строках — их назвала строка отказа, rewrite — со связями и reason, obsolete — без связей, с reason и removed "
                         "{capability, name} из REMOVED дельты; отдай исправленный "
                         "ответ единственным повтором стадии (--transport-attempts 2), ничего не записано",
    "IMPORT_LINKS_NOT_ANSWERED": "при --docs требования и связи кейсов размечает модель: сначала prepare и submit --stage "
                                 "context-marker:requirements, потом submit --stage plan",
    "IMPORT_CSV_V4": "выгрузите кейсы в профиле v5 (по умолчанию) или экспортом из Zephyr — в Test Data должен быть текст человека",
    "ZEPHYR_CSV_COLUMNS": "нужен экспорт тест-кейсов Zephyr Scale в CSV с колонками шаблона; лишние колонки уберите из выгрузки",
    "ZEPHYR_CSV_ROWS": "заполните названные ячейки CSV: у кейса Name и хотя бы один шаг, у каждого шага Step и Expected Result — "
                       "наблюдаемый результат системы, не «успешно», «ok» или «—»; потом повторите scan --cases",
    "IMPORT_CSV_SECRET": "уберите секрет из CSV (пароль, токен, учётные данные в адресе): замените значение плейсхолдером <…> "
                         "(например <пароль>) и повторите scan --cases; файл с секретом модель не увидит",
    "ZEPHYR_CSV_ENCODING": "сохраните CSV в UTF-8 (Excel: Файл → Сохранить как → CSV UTF-8)",
    "ZEPHYR_CSV_NOT_CSV": "нужен экспорт тест-кейсов Zephyr Scale в CSV",
    "ZEPHYR_CSV_DELIMITER": "нужен CSV с разделителем «,» или «;» и колонками Zephyr (Name, …)",
    "IMPORT_NOT_CASES_RUN": "импорт — для прогона scan --cases; у этого прогона требования размечает модель",
    "IMPORT_CSV_CHANGED": "CSV изменился после scan: повторите scan --cases --new-run",
    "IMPORT_REQUIREMENTS_INVALID": "требования из CSV не прошли проверку ответа разметки — запрос не записан; исправьте названные "
                                   "ячейки CSV и повторите scan --cases --new-run",
    "ZEPHYR_CSV_PROFILE": "профиль CSV — zephyr-scale-step-row-24-v5 (по умолчанию, текст человека) или zephyr-scale-step-row-24-v4 "
                          "(плюс машинные колонки шагов); профили v1–v3 только читаются; других значений нет",
    # --- the host (HostStop without a fix at its raise site) ---
    "FINISH_FACT_UNDETERMINED": "у прогона нет попытки: finish закрывает попытку, а её нет — сначала дойди до prepare "
                                "(run_pipeline status называет следующий шаг); для прогона, где работа не начата, "
                                "достаточно scan --new-run",
    "INVENTORY_INVALID": "опись проекта не читается или не сходится с диском: повтори scan (--new-run), файлы проекта не меняй во время scan; "
                         "если detail называет module или attempt.schema.json — проверь в .skillsrc root модуля: путь существующей папки проекта",
    "PACK_INCOMPLETE": "в папке пакета нет файла, который читает код: пакет скопирован не целиком или файл удалён — возьмите поставку заново "
                       "и проверьте её `python -m tools.doctor --root <папка пакета>`; прогон не правьте",
    "SKILLSRC_UNREADABLE": ".skillsrc должен быть обычным файлом внутри проекта: уберите каталог, junction или ссылку с этим именем "
                           "(или права на чтение) и повторите scan; нужен новый — `python -m tools.init_skillsrc --project <проект> --write`",
    "RUN_ROOT_UNSAFE": ".pilot-runs в проекте (или папка прогона в нём) — ссылка: junction или симлинк, прогоны по ссылкам не пишутся. Удалите ссылку "
                       "(rmdir для junction, rm для симлинка — содержимое целевой папки останется) и повторите команду: прогоны создаются в самом проекте",
    "SOURCE_TARGET_INVALID": "--target — путь внутри выбранного модуля проекта (относительно корня проекта), без «..»",
    "SNAPSHOT_DOCS": "--docs и --reference — пути внутри проекта к существующим файлам или папкам, не ссылкам и не junction",
    "EXECUTION_COORDINATES": "--run — один точный run_id этого проекта из run_pipeline status; --project — корень проекта прогона",
    "EXECUTION_RECEIPT": "выполнение закончилось, а квитанция не записана или не читается: повтори команду (она идемпотентна), "
                         "руками журнал не правь",
    "BASELINE_INCOMPLETE": "замер состояния проекта до запуска не читается: повтори prepare-execution (замер делает код), файлы проекта не меняй",
    # --- bare ValueErrors that became codes ---
    "INVALID_ARGUMENT": "проверь флаги команды: tools.api <команда> --help",
    # --- the audit of the canon: the order is the code's work, the rest is the agent's ---
    "CANONICAL_REQUIREMENT_ID_ORDER": "requirement_ids кейса — в порядке requirements документа (display_order); submit ставит "
                                      "порядок сам",
    "CANONICAL_CATEGORY_ORDER": "categories кейса — в порядке positive, negative, boundary, authorization, security, concurrency, "
                                "idempotency, observability, functional; submit ставит порядок сам",
    "CANONICAL_ARGUMENT_ORDER": "arguments capability — по name в порядке кодовых точек Unicode",
    "CANONICAL_RESULT_ORDER": "results capability — по name в порядке кодовых точек Unicode",
    "CANONICAL_CAPABILITY_ORDER": "operation_capabilities — по capability_id в порядке кодовых точек Unicode",
    "CANONICAL_BLOCKER_ORDER": "automation_blockers шага — по blocker_id в порядке кодовых точек Unicode",
    "CANONICAL_SOURCE_REQUIREMENT_ORDER": "source_requirements — документы подряд, внутри документа по номеру строки как числу",
    "CANONICAL_DISPLAY_ORDER": "display_order элементов списка — 1, 2, 3 … подряд без пропусков и повторов; удали поле display_order — код поставит его сам по позиции",
    "SCOPE_PLAN_UNREADABLE": "опубликованный план пачек есть в журнале, но не читается (файл удалён или изменён): охват не берётся из scope-decision.json; "
                             "run_pipeline status называет файл — восстановить его нельзя, начни новый прогон: scan --new-run",
    "CANONICAL_DOCUMENT_ID": "document_id — безопасный идентификатор (буквы, цифры, . _ : -), как в заголовке пачки; поле не меняй",
    "CANONICAL_NONFINITE_NUMBER": "числа в JSON конечные: NaN и Infinity не допускаются",
    "CANONICAL_REVISION_TYPE": "revision — целое число, 1 для нового документа",
    "SEMANTIC_BLOCKER_PATH": _BLOCKER_RULE,
    "SEMANTIC_MISSING_ASSERTION_BLOCKER": _BLOCKER_RULE,
    "SEMANTIC_SPURIOUS_BLOCKER": _BLOCKER_RULE,
    "SEMANTIC_MISSING_OPERATION_BLOCKER": "у шага без operation (null) должен быть блокер с field_path /operation, например "
                                          '{"blocker_id": "BLOCK-<namespace>-1", "code": "UNRESOLVED_OPERATION", "field_path": "/operation", '
                                          '"reason": "…", "provenance": ["…"]}; либо опиши operation',
    "SEMANTIC_HUMAN_EXPECTATION_BODY_MISSING": "JSON-шаблон тела ответа должен быть ПОСЛЕДНИМ блоком text ожидания, с отступом (полями, на которые "
                                               "есть утверждения http_body); значения — из утверждений или плейсхолдеры <значение присутствующего поля>. "
                                               "Пример тела обычно строит код из утверждений и выходов шага; здесь не построил — корневой указатель, пропуск индекса "
                                               "массива, два значения на один путь или литерал на предке: допиши ОДИН блок последним (убери слова после него); для not_exists блок не нужен",
    "SEMANTIC_HUMAN_EXPECTATION_BODY_DUPLICATE": "в text ожидания HTTP-шага один JSON-блок — шаблон тела ответа, последний: слей пример тела и шаблон в один, остальные убери",
    "SEMANTIC_HUMAN_EXPECTATION_FIELD_MISSING": "добавь в JSON-шаблон text ожидания поле, на которое есть утверждение (путь — в message)",
    "SEMANTIC_HUMAN_EXPECTATION_UNEXPECTED_FIELD": "убери из JSON-шаблона text поле, на отсутствие которого есть утверждение not_exists",
    "SEMANTIC_HUMAN_EXPECTATION_PLACEHOLDER": "для утверждения exists в шаблоне text — плейсхолдер вроде <идентификатор>, не придуманное значение",
    "SEMANTIC_HUMAN_EXPECTATION_LITERAL_MISMATCH": "значение в JSON-шаблоне text должно равняться литералу утверждения equals",
    "SEMANTIC_HUMAN_BODY_MISSING": "в конец test_data шага допиши полный JSON тела запроса с отступом — по нему идут входы body; тело — валидный JSON: значение-заглушка — строка в кавычках (\"<…>\"), не голое <…>",
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
    "SEMANTIC_CAPABILITY_TYPE_MISMATCH": "тип входа задаёт ИСТОЧНИК, а не semantic_type: literal — json-тип значения (строка — json string, named из неё не сделать), step_output — объявленный тип выхода. "
                                         "Аргумент named-типа (например json_text) питает только fixture/environment/secret_handle (semantic_type = тип аргумента, type_provenance — где он описан) "
                                         "или step_output с выходом того же named-типа; иначе шаг ручной. Выход project_action: semantic_type = типу результата capability. Оба типа — в message",
    "SEMANTIC_DUPLICATE_CAPABILITY_ACTION": "одно действие (adapter + action) — одна capability: объедини описания",
    "SEMANTIC_DUPLICATE_CAPABILITY_ARGUMENT": "у каждого аргумента capability не больше одного входа шага: удали повтор",
    "SEMANTIC_FORWARD_STEP_OUTPUT": "`step_output` берёт выход шага строго выше по порядку шагов кейса — не своего шага и не нижнего: путь в диагностике называет вход или "
                                    "утверждение с такой ссылкой. Утверждение о выходе своего шага — `actual` вида `http_status`/`http_body`/`project_result`, не `step_output`; "
                                    "нужен выход нижнего шага — перенеси это утверждение или вход в тот шаг или переставь шаги",
    "SEMANTIC_HTTP_PATH_BINDINGS": "каждому {плейсхолдеру} пути HTTP нужен вход location=path с тем же name (и наоборот)",
    "SEMANTIC_MANUAL_STEP_OUTPUT": "автоматизируемый шаг не берёт выход ручного шага (manual_only): возьми значение из другого источника "
                                   "или сделай и этот шаг ручным",
    "SEMANTIC_OPERATION_ASSERTION_KIND": "вид actual утверждения не подходит операции шага: у http — http_status, http_header, http_body; "
                                         "у project_action — project_result",
    "SEMANTIC_OPERATION_OUTPUT_KIND": "вид source выхода не подходит операции шага: у http — http_status, http_header, http_body; "
                                      "у project_action — project_result",
    "SEMANTIC_OPERATION_TARGET_KIND": "location входа не подходит операции шага: path/query/header/body — для http, arg — для project_action",
    "SEMANTIC_OPERATOR_TYPE": "оператор берётся по типу значения (у named-типа — по его representation): equals/not_equals — expected того же типа (named-значение и literal несовместимы: "
                              "сравнивай contains/matches или expected-fixture того же named-типа); contains — значение string и literal string; matches — значение string, expected "
                              "{kind: regex, dialect: portable-regex-v1, pattern}, не literal; больше/меньше — integer/number; length_equals — string/array/object и literal integer ≥ 0; "
                              "schema_matches — object/array и schema_ref. JSON-текст (named, representation string) — contains или matches",
    "SEMANTIC_OUTPUT_TYPE": "semantic_type выхода не подходит его источнику",
    "SEMANTIC_TARGET_TYPE": "semantic_type входа не подходит его цели: path — string, integer, number или boolean (id из ответа остаётся integer, URL печатает код), "
                            "header — string, query — скаляр; тип выхода не меняй, чтобы подойти пути",
    "SEMANTIC_TYPE_MISMATCH": "semantic_type входа равен типу его источника (оба в message): literal — json-тип значения, step_output — тип выхода; у fixture/environment/secret_handle — тип аргумента "
                              "и type_provenance. Нужен другой тип — меняй источник, а не semantic_type",
    "SEMANTIC_PARAM_GROUP_MISMATCH": "кейс группы param_group отличается от первого кейса группы не только вариантом. Сравнивается всё, кроме `case_id`, `display_order`, `title`, `objective`, "
                                     "`design_refs`, `param_group` (в группе с импортированным кейсом — ещё `management`, `priority`, `preconditions`: у каждого свои из CSV), у шагов — `step_id`, `action`, `test_data`, у входов и утверждений — `input_id`/`assertion_id` и литеральных значений: "
                                     "операции, capability, виды и места входов и утверждений, ссылки `step_output` (по номеру шага), `expectations`, `automation_blockers` и число шагов "
                                     "должны совпасть. Путь в диагностике — первое расхождение в этом кейсе: выровняй кейс по первому кейсу группы. Тот же код при занятом имени: "
                                     "PG-… уже есть в прошлой пачке — дай группе новое имя, группа живёт в одной пачке",
    "SEMANTIC_PARAM_GROUP_PARAMETER": "в группе param_group варьируется ровно один параметр дизайна, у каждого кейса свой вариант: проверь design_refs; параметр нужен в design у каждого CREQ, к которому привязаны кейсы группы",
    "SEMANTIC_PARAM_GROUP_SINGLE": "param_group из одного кейса не бывает: убери группу или добавь вариант",
    "SEMANTIC_REQUIRED_CAPABILITY_ARGUMENT": "каждому обязательному аргументу capability нужен ровно один вход шага (location=arg)",
    "SEMANTIC_UNDECLARED_CAPABILITY_ARGUMENT": "вход arg ссылается на аргумент, которого нет в capability: объяви его или убери вход",
    "SEMANTIC_UNDECLARED_CAPABILITY_RESULT": "выход project_result ссылается на результат, которого нет в capability: объяви его или убери выход",
    "SEMANTIC_RU_RU_HUMAN_FIELDS": "человеческие поля (title, objective, action, text …) — по-русски, content_locale ru-RU",
    "SEMANTIC_SECRET_HANDLE_IN_HUMAN_FIELD": "значение секрета в человеческом поле не пишется: ссылка на secret_handle через safe_label",
    "SEMANTIC_UNCOVERED_REQUIREMENT": "у каждого требования должен быть кейс: добавь кейс с его requirement_ids",
    "SEMANTIC_UNKNOWN_CANONICAL_REQUIREMENT": "ссылка на требование, которого нет в requirements: поправь id или добавь требование",
    "SEMANTIC_UNKNOWN_CAPABILITY": "operation.capability_id ссылается на необъявленную capability: объяви её в operation_capabilities этого фрагмента; capability прошлой пачки — копия слово в слово из входа prior_capabilities",
    "SEMANTIC_UNKNOWN_REQUIREMENT": "requirement_ids кейса ссылаются на требование, которого нет в requirements: поправь id",
    "SEMANTIC_UNKNOWN_SOURCE_REQUIREMENT": "mapping ссылается на SREQ, которого нет в плане: возьми id из batch-plan.json",
    "SEMANTIC_UNKNOWN_STEP_OUTPUT": "step_output ссылается на несуществующий выход шага: step_id шага — STEP-<хвост case_id после TC->-<NN, номер шага в кейсе двумя цифрами> "
                                    "(STEP-B1-003-02 — шаг 2 кейса TC-B1-003), output_id — имя выхода этого шага; поправь ссылку или объяви выход",
    "SEMANTIC_UNMAPPED_CANONICAL_REQUIREMENT": "у каждого требования канона должен быть mapping с исходным SREQ",
    "SEMANTIC_UNMAPPED_SOURCE_REQUIREMENT": "у каждого SREQ пачки должен быть mapping: добавь недостающий",
    # --- the paths the agent names (tools.output_guard) ---
    "API_OUT_OUTSIDE_RUN": "--run-root — существующий прогон `<project>/.pilot-runs/<run_id>` (каталог из вывода `scan`); повтори команду с ним",
    "OUTPUT_DIR_OUTSIDE_RUN": "каталог публикации прогона `<run>/published` подменён ссылкой (junction) или ведёт за пределы прогона: код ничего туда не записал; агент ничего не правит — человек удаляет эту ссылку (на её месте должна быть обычная папка внутри прогона или её нет) и повторяет ту же команду",
    "INPUT_OUTSIDE_PROJECT": "передай --run-root <run> (или --project) и файл внутри этого проекта; без них — файл внутри прогона <project>/.pilot-runs/<run_id>/",
    # --- the pause after the marker (tools.scope_pause) ---
    "AUTOMATION_ORPHAN_FILE": "каждый файл из generated_files несёт хотя бы один символ, связанный строкой implementation_relations с шагом или утверждением кейса; файл-помощник без такого символа не принимается — держи помощники внутри тестового класса, а лишний файл убери",
    "AUTOMATION_RELATION_ORDER": "строки implementation_relations — в порядке документа кейсов: по кейсу, внутри кейса по шагу, у шага строка операции (operation) раньше строк проверок (assertion), затем по ожиданию и по проверке, при равенстве по file_id и symbol_id",
    "STAGE_NO_PLAN": "в журнале есть запрос пачки, а плана пачек в прогоне нет (прогон начат версией без опубликованного плана): покажи это человеку; прогон не продолжается — новый прогон тем же scan с --new-run",
    "SCOPE_MARKER_UNREADABLE": "ответ разметки есть в журнале, но его файл не читается или изменён (контекст дрейфовал): покажи это человеку; прогон не продолжается — новый прогон тем же scan с --new-run",
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
    # --- a delivery without the execution files ---
    "PROFILE_NOT_IN_PACKAGE": "в этой поставке нет исполнения тестов — повтори команду без --profile или с --profile cases-only-v1: "
                              "кейсы и отчёт доступны, автотесты запускает тот, у кого есть полная поставка",
    "MODE_B_NOT_IN_PACKAGE": "эта поставка — для кейсов, режима автотестов в ней нет: не вызывай его команды; прогон local-pilot-v1, сделанный полной "
                             "поставкой, продолжай её командами, а здесь начни новый — `scan --project <project> --new-run` без --profile",
    # --- the build is run by a human (materialize prints the command, ingest takes the reports) ---
    "EXEC_REPLACED": "exec заменён: `materialize --project <project> --run <run_id>` кладёт тесты в проект и печатает команду, человек её запускает, "
                     "`ingest --project <project> --run <run_id> --reports <каталог с отчётами>` принимает отчёты",
    "COMMAND_NOT_PRINTABLE": "переименуйте или переместите проект (или интерпретатор/модуль) в каталог без символов, которые назвал ответ, и начните прогон заново "
                             "(scan --new-run): путь входит в напечатанную команду, а в cmd.exe, PowerShell и sh они меняют её смысл",
    "INGEST_WITHOUT_MATERIALIZE": "сначала `run_pipeline materialize --project <project> --run <run_id>`: он кладёт тесты в проект и печатает команду для человека",
    "INGEST_REPORT_MISSING": "запустите команду из materialize (`materialize --run <run_id>` напечатает её снова) и повторите ingest; отчёты ищутся там, где напечатал "
                             "materialize, — `--reports` не нужен, а с ним путь абсолютный; сборка не дошла до тестов — `ingest --no-reports --reason BUILD_FAILED|TESTS_NOT_STARTED`",
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
    # --- the refusals of the cases-only path that had no fix ---
    "CONCURRENT_WRITER": "с этим прогоном работает другая команда: повтори свою через несколько секунд; команды одного прогона не запускай параллельно",
    "JOURNAL_CORRUPT": "журнал прогона повреждён: начни новый прогон (`scan --new-run`); этот прогон не правь и не удаляй — он нужен для разбора",
    "PUBLICATION_UNKNOWN": "исход записи в журнал неизвестен: повтори ту же команду с теми же входами один раз, затем `status` — он покажет, что записано",
    "INPUT_INVALID": "проверь --run-root (каталог прогона `<project>/.pilot-runs/<run_id>`) и входные файлы команды; повтори с исправленными аргументами",
    "PLAN_ANSWER_INVALID": 'файл `incoming/plan.json` — один JSON-объект {"subject": "<название предмета проверки по-русски>"}: непустая строка до 200 знаков '
                           "без управляющих символов, других ключей нет; исправь файл и повтори `submit --stage plan`",
    "GENERATOR_INPUT_CONFLICT": "у попытки уже есть другой такой вход (скелет, capabilities, план или header): продолжай с опубликованным, "
                                "`status` покажет следующий шаг; другое содержимое — новый прогон (`scan --new-run`)",
    "REVIEWER_SESSION_CLOSED": "сессия ревью закрыта: вердикт в неё не добавить; повтор `submit --stage tc-reviewer:canonical` выберет по записанному, "
                               "новое ревью — новый прогон (`scan --new-run`)",
    "REVIEWER_SESSION_OPEN_EVIDENCE": "запрос evidence ждёт ответа: ответь на него (ключ `evidence` с путями в проекте в слоте ответа, затем `submit`) или закрой сессию "
                                      "`submit --stage tc-reviewer:canonical --abort REVIEW_CONTEXT_LIMIT`",
    "REVIEWER_EVIDENCE_PATH_UNKNOWN": "этого файла нет в inventory попытки: `evidence` — только пути файлов проекта из inventory (файлы запросов и ответов прогона, например из model-stage-artifacts, туда не входят; уже выданные называет `status`); пустой `evidence` — убери ключ",
    "REVIEWER_EVIDENCE_DUPLICATE": "эти файлы уже выданы ревьюеру: не проси их снова, а новые пути — отдельным запросом",
    "REVIEWER_ABORT_REASON_INVALID": "`submit --abort` — REVIEW_CONTEXT_LIMIT (не хватило бюджета на evidence) или REVIEW_UNUSABLE "
                                     "(ответ дважды не прошёл проверку)",
    "REVIEW_UNUSABLE": "ревью не может закончиться вердиктом: закрой сессию `submit --stage tc-reviewer:canonical --abort REVIEW_UNUSABLE`, затем `finish`; "
                       "следующий заход — команда `next_run` из его вывода",
    "REVIEW_CANDIDATE_NOT_PUBLISHED": "этот кандидат не тот, что опубликован журналом: повтори `prepare --stage tc-reviewer:canonical` — "
                                      "он возьмёт кандидата из журнала",
    "REVIEW_ALREADY_REQUESTED": "ревью этой стадии уже запрошено для другого кандидата: продолжи его (`status` → state, next), новое — новый прогон",
    "REVIEW_INPUTS_UNAVAILABLE": "вход ревью не найден в журнале прогона: проверь --run-root (`status` называет шаг, которого не хватает) и выполни его",
    "CONTEXT_MARKER_PARTS_INCOMPLETE": "не все части разметки отвечены: `status` называет недостающую (next.stage); "
                                       "ответь на неё (`prepare`, `submit`) — последний `submit` повторит слияние",
    "CONTEXT_MARKER_REQUIREMENTS_CHANGED": "требования части не совпали с требованиями шага `context-marker:requirements`: часть их не пишет, `submit` подставляет их сам — "
                                           "значит, журнал прогона нарушен (или часть записана мимо `submit`): начни разметку заново — `scan --new-run`",
    "HARDLINK_UNAVAILABLE": "диск не поддерживает жёсткие ссылки в `<run>/published`: проект и прогон должны лежать на локальном диске с NTFS/ext4 — сообщи человеку",
    "OUTPUT_DIRECTORY_ERROR": "каталог публикации `<run>/published` недоступен (это не папка или нет прав записи): человеку проверить права на каталог прогона",
    "TARGET_INSTALL_FAILED": "публикацию не удалось завершить: ничего не правь руками, повтори команду с теми же аргументами; повторный отказ — "
                             "проверь права записи и свободное место в `<run>/published`",
    "TEMPORARY_WRITE_FAILED": "временный файл публикации не записан: проверь права и свободное место в `<run>/published`, повтори команду",
    "TARGET_READ_FAILED": "опубликованный файл не читается: проверь права доступа `<run>/published`, повтори команду с теми же аргументами",
    "OPENSPEC_SOURCE_CONFLICT": "один прогон — одно изменение OpenSpec: назови в --docs папку или файлы одного openspec/changes/<имя>/ и spec.md его областей "
                                "(остальные живые изменения и неназванный архив код в модель не отдаёт), повтори scan; если отказ пришёл после шага модели — scan --new-run; прочие причины — в сообщении",
    "NEED_DOCS_LIMIT": "документы слишком велики: назови в --docs меньше областей (папку одной области или файлы), повтори scan",
    "STEP_INPUT_UNRESOLVED": "не стоп: вход шага нашёлся не по журналу (или два файла с одним digest) — одна строка человеку, шаг продолжай",
}

# not a row of the table: ``MATERIALIZATION_CONFLICT`` has many causes, and the table is read for any refusal of that code — this text belongs to the held path of ``submit`` only
HELD_PATH_FIX = ("путь файла уже занят в проекте (он есть в описи прогона, `materialize` его не запишет): в ответе назови другой путь и другое имя класса; "
                 "или человек убирает прежние тесты из проекта и делает `scan --new-run` (опись замораживает scan)")

_SCHEMA_GENERIC = "приведи значение по пути path к схеме: что именно не так, сказано в message"


def fix_for(code: str | None, message: str = "") -> str | None:
    """The fix of a refusal code: the one of its cause when the message tells it (``OPENSPEC_REASONS``), the table, else the generic one of a ``SCHEMA_*`` code, else None."""
    if not code:
        return None
    if (reason := _reason(code, message)) and reason[1]:
        return reason[1]
    found = FIXES.get(code)
    if found is None and code.startswith("SCHEMA_"):
        return _SCHEMA_GENERIC
    return found


def delete_field_fix(name: str) -> str:
    """The fix of a field the code puts itself (a digest) that the answer gave with another value."""
    return _DELETE_FIELD.format(name=name)


# What the tester reads when the agent shows him a refusal: one phrase of what happened, one action (q7).  ``fix`` stays the agent's way
# out; the envelope carries this beside it as ``human``.  The one place for these words: no internal words, no commands, no flags.
_NO_AUTOTESTS = "В этой поставке нет автотестов, только тест-кейсы. Скажите агенту начать прогон только для кейсов."
_SELF_REVIEW = ("Ревью кейсов должен делать отдельный проверяющий (свежий субагент или новая сессия). Если его нет и вы согласны на проверку "
                "в той же сессии, скажите агенту «принять ревью в той же сессии» — в отчёте это отметят.")
HUMAN: dict[str, str] = {
    "SCOPE_PENDING": "Часть требований нечем проверить снаружи: у них нет видимого результата. Скажите агенту: исключить их из кейсов (и почему) или продолжать как есть.",
    "SCAN_RUN_IN_PROGRESS": "Для этих документов уже есть незаконченный прогон, новый не начат. Скажите агенту продолжить прежний — или начать новый, если прежний не нужен.",
    "FINISH_FACT_UNDETERMINED": "Прогон нельзя закрыть сейчас: не все его шаги закончены. Скажите агенту продолжить прогон — следующий шаг ему назовёт пакет.",
    "INGEST_REPORT_MISSING": "Отчёта о запуске тестов не нашлось. Запустите команду сборки, которую напечатал пакет, и скажите агенту, когда она закончится.",
    "MODE_B_NOT_IN_PACKAGE": _NO_AUTOTESTS,
    "PROFILE_NOT_IN_PACKAGE": _NO_AUTOTESTS,
    "PLAN_REVIEW_TOO_LARGE": "Документ слишком большой: ревью всех его кейсов разом не поместится в память модели, этот прогон дальше не пойдёт. "
                             "Скажите агенту вести документ частями — по разделам или файлам, прогоны по очереди.",
    "FRESH_CONTEXT_REQUIRED": _SELF_REVIEW,
    "INDEPENDENCE_UNVERIFIED": _SELF_REVIEW,
    "ANSWER_JSON_INVALID": "Ответ модели не читается как JSON: он целый, но в записи ошибка (лишний текст, пропущенная запятая или непарная кавычка). "
                           "Делать ничего не нужно: агент попросит модель отдать ответ заново одним JSON без текста вокруг.",
    "MODEL_OUTPUT_TRUNCATED": "Ответ модели оборвался на середине. Делать ничего не нужно: агент повторит его один раз; если и повтор оборвётся, прогон придётся начать заново тем же запросом.",
    "BATCH_SEMANTIC_CONFLICT": "Кейсы, которые написала модель, не прошли проверку формата. Делать ничего не нужно: агент исправит их по подсказке пакета.",
    "MODEL_STAGE_ARTIFACT_INVALID": "Ответ модели не подошёл под нужный формат. Делать ничего не нужно: агент поправит его и подаст ещё раз.",
    "REVIEWER_EVIDENCE_PATH_UNKNOWN": "Проверяющий попросил файл, которого нет среди файлов проекта для этого прогона. Делать ничего не нужно: агент попросит его ответить без этого файла.",
    "STEP_OVER_BUDGET": "Вход этого шага больше бюджета модели, но шаг идёт дальше. Делать ничего не нужно; в следующий раз ограничьте запрос одним модулем или областью.",
    "STEP_OUTPUT_LARGE": "Ответ модели получился очень большим — это только замер. Делать ничего не нужно, прогон идёт дальше.",
}


RETRY_SPENT = "повтор стадии исчерпан: ответ уже подан с --transport-attempts 2, третьего вызова модели нет; "  # the start of a fix once the one retry is spent
SPENT_HUMAN = ("Повтор уже был, а ответ модели всё равно не прошёл проверку: этот прогон дальше не пойдёт. "
               "Скажите агенту закрыть его и начать заново тем же запросом.")


def spent_abandon(stage: str) -> str | None:
    """The cause that leaves the run when the retry of ``stage`` is spent: before the review of the cases the run is left, the automation is abandoned; None — no command is promised."""
    family = stage.split(":", 1)[0]
    return "STAGE_RETRY_EXHAUSTED" if family in ("tc-to-autotest", "autotest-reviewer") else "LEFT_BEFORE_REVIEW" if family in ("context-marker", "tc-generator", "plan") else None


CLOSE_RUN_FIX = ("закрой прогон: `python -m tools.run_pipeline finish --project <проект> --run <id прогона> --abandon LEFT_BEFORE_REVIEW`, "
                 "затем тот же `scan` (простой scan начнёт новый прогон)")  # after a refused plan: the one command the run can be left by
_ONE_CHANGE = ("один прогон — одно изменение OpenSpec: спроси человека, какое проверять; назови в --docs папку одного openspec/changes/<имя>/ и spec.md его областей "
               "(остальные живые изменения и неназванный архив код в модель не отдаёт); остальные — отдельными прогонами; повтори scan, --new-run не нужен")
_WRITTEN_WRONG = ("В файле изменения или спецификации требование записано неверно: повтор, лишний или пропущенный раздел. "
                  "Агент назовёт файл и строку; попросите автора поправить и скажите агенту, когда готово.")
_TWO_CHANGES = "В проверку попали сразу два изменения OpenSpec, а за один прогон берётся одно. Скажите агенту, какое из них проверять; остальные — отдельными прогонами."
# OPENSPEC_SOURCE_CONFLICT has many causes (q8): by the start of the message, the fix of the cause (None: the raise site has its own) and
# the tester's phrase (None: the agent closes it alone)
OPENSPEC_REASONS: tuple[tuple[str, str | None, str | None], ...] = (
    ("source digest does not match content", "файл изменился после scan: повтори scan --new-run", None),
    ("unpaired rename", "в дельте RENAMED каждое переименование — две строки подряд: FROM: со старым заголовком и TO: с новым; исправляет автор изменения: "
                        "назови человеку файл из сообщения, после правки повтори scan", _WRITTEN_WRONG),
    ("duplicate or misplaced requirement", "в spec.md области требование названо дважды или лежит вне раздела `## Requirements`; исправляет автор спецификации: "
                                           "назови человеку файл и строку из сообщения, после правки повтори scan", _WRITTEN_WRONG),
    ("requirement has no delta operation", "в дельте требование лежит вне разделов `## ADDED Requirements`, `## MODIFIED Requirements`, `## REMOVED Requirements`; исправляет автор "
                                           "изменения: назови человеку файл и строку из сообщения, после правки повтори scan", _WRITTEN_WRONG),
    ("authorize one selected OpenSpec change", _ONE_CHANGE, _TWO_CHANGES),
    ("в --docs названы файлы нескольких живых изменений", _ONE_CHANGE, _TWO_CHANGES),
    ("в проекте несколько живых изменений", None, _TWO_CHANGES),
    ("rename does not match baseline", "в spec.md области должно быть только одно из двух имён переименования, старое или новое (в сообщении оба): назови человеку, что нашёл "
                                       "(нет ни одного или есть оба); после правки автором повтори scan",
     "Переименование в изменении не сходится со спецификацией: в ней должно быть только старое имя или только новое. Сверьте имена с автором изменения и скажите агенту, когда исправят."),
    ("ADDED has another text", "требование уже есть в spec.md с другим текстом, изменение и спецификация расходятся: покажи человеку оба текста (оба места — в сообщении: дельта и spec.md), "
                               "спроси автора изменения, какой верный; прогон не продолжай, после правки повтори scan",
     "Требование из раздела ADDED уже есть в спецификации, но с другим текстом: изменение и спецификация расходятся. Спросите автора, какой текст верный, и скажите агенту, когда исправят."),
    ("MODIFIED on an unmarked baseline", "в spec.md области нет ни одного раздела `### Requirement:` — база написана прозой, MODIFIED сверять не с чем: скажи человеку — владелец базы "
                                        "размечает требования разделами `### Requirement:`, или автор изменения оформляет дельту как ADDED; после правки повтори scan",
     "В базе области, которую правит изменение, нет размеченных требований — сверять MODIFIED не с чем. Попросите владельца базы разметить разделы «### Requirement:» "
     "или автора изменения оформить дельту как ADDED и скажите агенту, когда готово."),
    ("MODIFIED is not in the baseline", "MODIFIED называет требование, которого нет в spec.md области: сверь имя с `### Requirement:` там (файл:строка и ближайшие имена — в сообщении), "
                                        "спроси автора изменения; после правки повтори scan",
     "Изменение правит требование, которого нет в спецификации: имя не совпало. Сверьте имя с автором изменения и скажите агенту, когда исправят."),
    ("repeats a requirement", "одно требование названо в дельте дважды: автор изменения убирает повтор (файл:строка из сообщения); после правки повтори scan", _WRITTEN_WRONG),
    ("duplicate scenario", "у требования два сценария с одним названием: автор переименовывает один из них (файл из сообщения); после правки повтори scan", _WRITTEN_WRONG),
    ("разметка дала", "документ не делить: разметка этого прогона раздута, в этом прогоне её не исправить; " + CLOSE_RUN_FIX + "; в разметке нового прогона абзац без номера "
                       "в нумерованном документе — контекст (роль, шаг 2; список номеров — в файле вида)",
     "Разметка требований получилась раздутой: в ней больше требований, чем пронумерованных пунктов в документе. Документ делить не нужно. "
     "Скажите агенту закрыть этот прогон и начать заново тем же запросом."),
    ("имена дельты не найдены", None,
     "Требования изменения не найдены в названных файлах спецификации, судить по ним нечего. Проверьте, то ли изменение выбрано, и скажите агенту, где эти требования лежат сейчас."),
)


def _reason(code: object, message: str) -> tuple[str, str | None, str | None] | None:
    """The row of ``OPENSPEC_REASONS`` whose start the message of an OPENSPEC_SOURCE_CONFLICT or of a refused plan has (the message may begin with the code)."""
    text = message.removeprefix(f"{code}: ")
    return next((row for row in OPENSPEC_REASONS if text.startswith(row[0])), None) if code in ("OPENSPEC_SOURCE_CONFLICT", "PLAN_REVIEW_TOO_LARGE") else None


def human_for(code: object, message: str = "") -> str | None:
    """The tester's phrase of a refusal or a warning code (of its cause, when the message tells it), or None — the answer then carries no ``human``."""
    if (reason := _reason(code, message)) and reason[2]:
        return reason[2]
    return HUMAN.get(code) if isinstance(code, str) else None
