# Канонические тест-кейсы и проекции Zephyr Scale

Статус: дизайн для пользовательской проверки
Дата: 2026-08-12
Ветка: `codex/zephyr-test-case-projection`

## 1. Решаемая проблема

Текущий контракт описывает тест-кейс как один шаг с полями `action` и
`expected_result`. Он удобен как небольшой JSON-артефакт, но недостаточен для
реальных последовательных сценариев:

- договорённость скилла требует ровно один шаг, хотя JSON Schema допускает массив;
- данные, полученные на одном шаге, нельзя типизированно передать следующему;
- человеческое ожидание не связано с машинной проверкой;
- CSV хранит весь массив шагов как JSON в одной ячейке и не является проекцией
  Zephyr Scale;
- трассировка заканчивается на уровне тест-кейса и не доказывает реализацию
  каждого шага;
- после `AUTO_FIX_APPLIED` reviewer может вернуть только часть кейсов, тогда как
  последующие этапы воспринимают её как полный набор;
- проекция создаётся до reviewer, поэтому исправленный JSON и экспорт могут
  разойтись.

Нужно получить один канонический семантический тест-кейс, из которого без ручной
синхронизации создаются три артефакта одной ревизии:

1. JSON — единственный машинный источник истины и формат передачи между AI-скиллами;
2. Markdown — человекочитаемая форма в подтверждённом пользователем формате;
3. Zephyr Scale CSV — пошаговая внешняя проекция.

## 2. Источники требований

Дизайн основан на четырёх независимых источниках:

1. текущих схемах, скиллах, инструментах и документации репозитория;
2. старом `tc-generator` v2.2, который задаёт удачные правила человеческого
   тест-дизайна;
3. двух фотографиях с подтверждённой структурой Markdown;
4. реальном XLSX-экспорте Zephyr Scale с 24 колонками и моделью «одна строка на
   шаг, метаданные кейса только в первой строке».

XLSX подтверждает форму экспорта Zephyr Scale, но не доказывает совместимость
CSV-импорта конкретного tenant. До контролируемого round-trip теста запрещено
называть CSV гарантированно импортируемым.

Все примеры и fixtures в репозитории должны быть синтетическими. Внутренние
идентификаторы из фотографий не переносятся.

## 3. Цели и нецели

### 3.1. Цели

- Сохранить естественные, конкретные и технически точные человеческие формулировки.
- Поддержать от одного до любого необходимого числа последовательных шагов без
  искусственного верхнего предела.
- Явно моделировать подготовку данных, входы, выходы и ссылки на результаты
  предыдущих шагов.
- Связать каждый автоматизируемый человеческий ожидаемый факт с машинными
  assertions.
- Не создавать две независимо редактируемые семантические копии кейса.
- Всегда создавать Markdown и Zephyr CSV для каждой валидной generated/reviewer
  ревизии; downstream использовать только явно выбранную effective revision.
- Сохранить стабильные идентификаторы при редактировании текста и изменении порядка.
- Довести трассировку до уровня
  `requirement -> case -> step -> expectation -> assertion -> file -> symbol -> run`.
- Честно представить ручные проверки и неполную готовность к автоматизации.

### 3.2. Нецели

- Обратное восстановление канонического JSON из Markdown или CSV.
- Двунаправленная синхронизация Jira и репозитория.
- Автоматическое обогащение старого JSON техническими полями путём разбора прозы.
- Обещание совместимости с CSV-импортом Zephyr до реальной проверки.
- Поддержка всех возможных Zephyr tenant/custom-field конфигураций без профиля.
- Привязка assertion DSL к pytest, JUnit или другому конкретному фреймворку.
- Добавление новой runtime dependency без отдельного доказательства необходимости и
  пользовательского согласования.

## 4. Выбранная архитектура

Используется один канонический семантический документ. Внутри одного шага рядом
хранятся две связанные стороны одного намерения:

- человеческое `action` и техническая `operation` с `inputs`/`outputs`;
- человеческая `expectation.text` и принадлежащие ей машинные `assertions`.

Это контролируемая семантическая избыточность, а не две копии артефакта.
Качественный текст нельзя надёжно получить из низкоуровневых assertions, а
assertions нельзя безопасно восстановить из прозы. Структурное соседство,
идентификаторы, semantic validator и reviewer обязаны проверять их согласованность.

Отклонённые варианты:

- человеческий JSON плюс отдельный technical overlay — создаёт два жизненных цикла
  и риск рассинхронизации;
- нормализованный машинный граф с генерацией текста — усложняет последовательную
  модель Zephyr и ухудшает человеческий стиль.

## 5. Версионирование и выбранная ревизия

Изменение несовместимо с `2.1.0`, поэтому все затронутые stage schemas получают
версию `3.0.0`.

Канонический документ содержит:

- `document_id` — стабильная идентичность набора кейсов;
- `revision` — положительный целочисленный номер;
- `parent_sha256` — `null` у первой ревизии и digest родителя у преемника;
- metadata, требования и полный набор тест-кейсов.

Digest и payload `.json` вычисляются по одним и тем же bytes канонического документа:
эквивалент Python `json.dumps(document, ensure_ascii=False, sort_keys=True,
separators=(",", ":"), allow_nan=False).encode("utf-8")`. UTF-8 BOM и terminal LF
не добавляются, Unicode normalization не выполняется. Нефинитные числа запрещены.
Digest не хранится внутри самого объекта и поэтому не самореферентен.
Каждое digest field использует единственный lexical format
`^sha256:[0-9a-f]{64}$`: literal `sha256:` плюс lowercase hexadecimal SHA-256.

Serializer сортирует только object keys и никогда не переставляет array elements.
До hashing/publication semantic validator требует canonical physical order:

- в `requirements`, `test_cases`, `steps`, `inputs`, `outputs`, `expectations` и
  `assertions` элемент с zero-based index `i` имеет `display_order == i + 1`;
- `operation_capabilities` отсортированы по `capability_id`, их `arguments` и
  `results` — по `name`, `automation_blockers` — по `blocker_id`, всё по возрастающему
  Unicode code-point order (их ID/name grammars не допускают normalization aliases);
- `requirement_ids` уникальны и следуют `display_order` referenced requirements;
- `categories` уникальны и следуют закрытому V3 precedence:
  `positive, negative, boundary, authorization, security, concurrency, idempotency,
  observability, functional`.

Остальные arrays (`documentation`, `provenance`, `preconditions`, components,
labels, external links и custom-field arrays) являются намеренно order-significant:
их validated input order сохраняется в bytes и проекциях, не трактуется как set и не
нормализуется. Таким образом одна валидная canonical revision имеет ровно один array
order; переставленный `display_order`/set-like array отклоняется, а не получает иной
digest при тех же проекциях.

Bundle `.json` содержит ровно bare canonical document с top-level полями
`document_id`, `revision`, `parent_sha256`, `metadata`, `operation_capabilities`,
`requirements` и `test_cases`. Stage envelopes (`schema_version`, `stage`,
`artifacts`, `warnings` и reviewer verdict) являются transport/control messages,
ссылаются на common canonical-document schema, но никогда не входят в bundle bytes
или revision identity. Все термины `document_sha256`, `parent_sha256`, reviewer base
digest, automation `source_digest`, trace `source_digest` и JSON-part bundle receipt
означают SHA-256 одних и тех же exact bare-document bytes, описанных выше. Envelope
digest под этими именами запрещён.

Reviewer никогда не возвращает «патч-массив» исправленных кейсов. Его контракт:

- `ПРИНЯТО`: зафиксировать `document_id`, `revision` и digest проверенной ревизии;
- `AUTO_FIX_APPLIED`: вернуть полный документ-преемник, у которого `revision = N+1`
  и `parent_sha256` равен digest проверенного документа;
- `ТРЕБУЕТ ДОРАБОТКИ`: не создавать преемника и остановить pipeline.

Selection validator для `AUTO_FIX_APPLIED` до публикации successor требует
одновременно `successor.document_id == candidate.document_id`,
`successor.revision == candidate.revision + 1` и `successor.parent_sha256 ==
sha256(candidate)`. Нарушение любого guard даёт `FAIL`: новый `document_id` не может
неявно начать другую lineage и не становится effective revision.

`AUTO_FIX_APPLIED` является недеструктивным для уже выданных stable identities.
Selection validator строит identity graph candidate и требует, чтобы successor
содержал ровно один экземпляр каждой существующей сущности под тем же родителем:
operation capabilities, requirements и cases под document; capability arguments и
results под capability; steps под case; inputs, outputs и blockers под step;
expectations под step; assertions под expectation. Argument/result являются
локальными identities с ключом `capability_id + role(argument|result) + name`.
Сопоставление выполняется по stable ID, для локальных IDs — по
`parent stable ID + local ID`, а не по позиции.
Текст, поля и `display_order` разрешено исправлять; новые сущности разрешено
добавлять только с новыми global/local identity keys. Удаление, переименование
identity key или reparenting хотя бы одной существующей сущности не публикуется как
AUTO_FIX: reviewer возвращает
`ТРЕБУЕТ ДОРАБОТКИ` с точным diagnostic. Так «полный successor» становится
проверяемым свойством, а не обещанием prompt.

Контроллер выбирает ровно одну полную ревизию (`effective canonical document`).
Именно её digest записывается в automation output, trace и проверочные receipts.
Ни один downstream stage не выполняет merge самостоятельно.

## 6. Каноническая модель

### 6.1. Документ

Сокращённый иллюстративный bare canonical document (это не готовый fixture; полный
валидный кейс раскрыт в следующих подразделах):

```json
{
  "document_id": "TCDOC-orders-confirmation",
  "revision": 1,
  "parent_sha256": null,
  "metadata": {
    "subject": {
      "kind": "http_endpoint",
      "method": "POST",
      "path": "/orders/{order_id}/confirm"
    },
    "documentation": ["https://example.invalid/spec"],
    "project": "PROJECT",
    "author": "AUTHOR",
    "date": "2026-08-12"
  },
  "operation_capabilities": [],
  "requirements": [
    {
      "requirement_id": "REQ-order-confirmation",
      "display_order": 1,
      "text": "Созданный заказ можно подтвердить",
      "provenance": ["https://example.invalid/spec#confirmation"]
    }
  ],
  "test_cases": []
}
```

Для не-HTTP задач `subject` использует generic `kind`, `name` и при необходимости
явные атрибуты. Поля HTTP не заполняются фиктивными значениями.

`operation_capabilities` — единственный внутри документа registry подтверждённых
`project_action`. Capability — closed object ровно с `capability_id`, `adapter`,
`action`, `arguments`, `results`, `provenance`. `adapter`/`action` — непустые ASCII
identifiers по `^[A-Za-z_][A-Za-z0-9_.:-]*$`; их пара уникальна. Argument — closed
object ровно с `name`, `semantic_type`, boolean `required`; result — ровно с `name`,
`semantic_type`. Names используют `^[A-Za-z_][A-Za-z0-9_.-]*$` и уникальны внутри
своего role. `provenance` непуст и указывает переданный technical context. Capability
не создаётся по догадке: если contract аргументов/результатов не подтверждён, шаг
получает blocker вместо фиктивной capability. HTTP использует встроенный контракт и
записи registry не требует.

`requirements` и `test_cases` являются непустыми массивами. Requirement содержит
стабильный `requirement_id`, `display_order`, человеческий `text` и непустой массив
`provenance`. `provenance` указывает только предоставленные источники и не
подменяется догадкой.

`coverage` не хранится второй таблицей связей. Покрытие вычисляется из
`test_cases[].requirement_ids`; semantic validator требует, чтобы каждое требование
было покрыто хотя бы одним кейсом. Это устраняет расхождение между двумя картами.

### 6.2. Тест-кейс

```json
{
  "case_id": "TC-order-confirmation-success",
  "display_order": 1,
  "requirement_ids": ["REQ-order-confirmation"],
  "title": "Успешное подтверждение созданного заказа",
  "objective": "Проверить подтверждение заказа и сохранение нового состояния",
  "categories": ["positive", "functional"],
  "priority": "HIGH",
  "preconditions": [
    "Пользователь авторизован и имеет право подтверждать заказы"
  ],
  "management": {
    "status": null,
    "folder": null,
    "components": [],
    "labels": [],
    "owner": null,
    "estimated_time": null,
    "external_keys": {},
    "external_links": {"issues": [], "pages": []},
    "custom_fields": {}
  },
  "steps": []
}
```

`expected_outcome` удаляется: итог кейса определяется последовательностью
ожидаемых фактов шагов. Хранить ещё одну пересказывающую строку запрещено.

`management` содержит только данные управления тестом, необходимые внешним
проекциям. `custom_fields` допускает только строки, числа, booleans и массивы таких
скалярных значений. Технический blueprint туда не помещается.

V3 `priority` — closed enum `CRITICAL|HIGH|MEDIUM|LOW`; legacy `BLOCKER` как priority
не переносится (automation blockers моделируются отдельно). `categories` — непустой
unique subset закрытого precedence из раздела 5.

Типы management закрыты: `status`, `folder`, `owner` и `estimated_time` —
`string|null`; `components` и `labels` — массивы строк в canonical order;
`external_keys` имеет единственный optional key `zephyr_scale` со строкой;
`external_links` имеет ровно массивы строк `issues` и `pages`; `custom_fields` — map
по точному case-sensitive external header name. Пустые external values представлены
`null`, отсутствующим optional key или пустым массивом, но profile никогда не
подставляет internal IDs вместо них.

### 6.3. Шаг

```json
{
  "step_id": "STEP-confirm-order",
  "display_order": 2,
  "action": "Отправить запрос POST /orders/{order_id}/confirm для заказа, созданного на шаге 1.",
  "manual_only": false,
  "manual_reason": null,
  "automation_blockers": [],
  "operation": {
    "kind": "http",
    "binding_profile": "http-binding-v1",
    "base_url_source": {
      "kind": "environment",
      "name": "API_BASE_URL",
      "provenance": ["https://example.invalid/runtime-config#API_BASE_URL"]
    },
    "method": "POST",
    "path": "/orders/{order_id}/confirm"
  },
  "inputs": [],
  "outputs": [],
  "expectations": []
}
```

Шаг является одной последовательной операцией. Несколько запросов, кликов или
наблюдений, между которыми существует новый результат, оформляются отдельными
шагами.

`operation` — nullable closed discriminated union (`additionalProperties: false`):

- `http` содержит ровно `kind`, константу `binding_profile: "http-binding-v1"`,
  `base_url_source`, `method` по `^[A-Z][A-Z0-9_-]*$` и absolute-path template
  `path`;
- `project_action` содержит ровно `kind` и `capability_id`, разрешаемый в единственную
  document-level capability для project-native fixture, UI-драйвера, БД, очереди
  или другого подтверждённого механизма. `adapter`/`action` в step не дублируются.

`base_url_source` — closed object ровно с `kind: "environment"`, `name` по
`^[A-Za-z_][A-Za-z0-9_]*$` и непустым order-significant `provenance`. В canonical
JSON хранится только имя runtime setting, но не его значение. Другие source kinds,
literal URL, userinfo, credential и query token в operation запрещены.

Сам step — closed object ровно с полями примера. `operation: null` допустим только у
manual-only либо blocked step; готовый automatable step требует полный union variant.

Автоматизируемый готовый шаг обязан иметь `operation`. Свободный executable code в
JSON не хранится. Каждый кейс содержит минимум один шаг, каждый шаг — минимум одну
expectation; верхнего предела у массива шагов нет.

#### `http-binding-v1`

Профиль определяет один language-neutral abstract request:
`(method, absolute_url, ordered_headers, body_bytes|null)`. Python/Java adapter может
использовать любой HTTP client, но обязан передать именно эти semantics; transport-
specific casing/order служебных HTTP/1.1 или HTTP/2 headers не является частью
контракта. External providers уже разрешены общим execution preflight раздела 6.5.
После preflight и до вызова client все runtime `step_output` bindings разрешаются и
type-check. Их `MISSING`, нарушение runtime type или невозможность построить request
дают terminal `ERROR` и итоговый `FAIL`; consumer request при этом не отправляется.

`base_url_source.name` разрешается один раз на execution preflight. Отсутствующее или
пустое setting даёт `NOT_RUNNABLE` до запуска required symbols. Значение обязано быть
ASCII origin точной формы `http://host[:port]` либо `https://host[:port]`: scheme и
DNS host lowercase, userinfo/path/query/fragment/trailing slash отсутствуют. DNS
label соответствует `[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?`, общая длина host не
превышает 253; также разрешён canonical dotted-decimal IPv4 без leading zero, каждый
octet `0..255`. Host из четырёх полностью decimal labels рассматривается только как
IPv4 и не может fallback в DNS name. IPv6 в V3 не поддерживается. Port — decimal
`1..65535` без leading zero. Setting не trim, decode или normalize. Невалидное
доступное значение является preflight `NOT_RUNNABLE`; canonical document и artifacts
его не сохраняют. `absolute_url` строится только как exact concatenation этого origin
и rendered path, затем optional query.

Path template начинается с `/`, не содержит query/fragment, whitespace, control,
backslash или braces вне placeholders. Placeholder — literal `{`, name по
`^[A-Za-z_][A-Za-z0-9_.-]*$` и literal `}`; остальные literal bytes — ASCII RFC 3986
`pchar` и `/`, а `%` допустим только в uppercase triplet `%[0-9A-F]{2}`. Literal
path segments `.` и `..` запрещены. Множество placeholder names в точности равно
множеству `path` input targets; повтор одного placeholder в template разрешён и
использует тот же binding. Resolved string кодируется как UTF-8 bytes: ASCII unreserved
`A-Z a-z 0-9 - . _ ~` остаются, каждый другой byte становится `%HH` с uppercase hex.
Поэтому `/`, `%`, `?` и `#` внутри значения не меняют структуру URL. Если после
substitution какой-либо complete path segment равен `.` или `..`, значение является
profile-invalid и отклоняется в фазе своего source по разделу 6.5, всегда до client
call.

Query bindings берутся в physical canonical order массива `inputs`; каждый target
даёт ровно одну пару, duplicate name запрещён. Name и scalar value кодируются тем же
UTF-8 percent-encoder, space всегда `%20`, никогда `+`; пары соединяются `&` после
одного `?`. String используется буквально, `null` становится `null`, booleans —
`true|false`, integer/finite number — exact canonical JSON scalar lexeme раздела 5.
Array/object query value запрещён. Если query bindings отсутствуют, `?` не добавляется.

Request header target name обязан храниться в canonical lowercase ASCII RFC 9110
`tchar`, exact pattern ``^[!#$%&'*+.^_`|~0-9a-z-]+$``; uppercase name отклоняется,
duplicate detection выполняется exact. Resolved value обязан быть ASCII без CR, LF,
TAB, других controls/DEL и без leading/trailing space; внутренняя space сохраняется.
Targets `host`, `content-length`, `transfer-encoding`,
`connection`, `content-type`, `accept-encoding` и `user-agent` зарезервированы и
запрещены. User headers идут в physical input order. Adapter затем добавляет ровно
`accept-encoding: identity`, `user-agent: test-skills-http-binding-v1` и, если body
присутствует, `content-type: application/json`; без body content-type автоматически
не добавляется. Другие application headers, implicit cookies, session auth и client
defaults запрещены; transport может добавить только `host` и framing metadata,
однозначно производные от URL/body.

Body pointer использует RFC 6901: `""` означает весь JSON document; non-root pointer
начинается с `/`, а `~` встречается только как `~0` или `~1`. Единственный root
binding предоставляет body целиком и по правилу overlap исключает другие body
bindings. Иначе сборка начинается с empty object; decoded tokens всегда являются
object member names, intermediate containers создаются как objects, а leaf получает
resolved JSON value. Числовой token не означает array index: `/0` создаёт key `"0"`.
Array можно передать только целым resolved value в leaf/root. Shared prefixes
объединяются, а equal/ancestor overlap уже запрещён semantic validator. Полученный
JSON сериализуется exact canonical serializer раздела 5 в UTF-8 без BOM и terminal
LF. Без body bindings `body_bytes` равен `null`, то есть request body отсутствует.

Одна HTTP operation выполняет ровно одну network attempt. Automatic redirect follow и
automatic retry на status, connection reset, timeout или stale pooled connection
запрещены. Первый полученный response, включая любой `3xx`, `429` или `5xx`, является
единственным наблюдаемым response; transport failure до response даёт terminal
`ERROR`/`FAIL` без повторной попытки.

Response observation также закрыта. Status — integer. Header lookup выполняется
case-insensitively по raw received fields: отсутствие даёт `MISSING`, единственное
значение после удаления outer SP/HTAB даёт `VALUE(string)`, несколько одноимённых
fields дают terminal `ERROR` (V3 не выдумывает их объединение). Response entity —
bytes после удаления transfer framing, но до любого HTTP content-coding decode;
client-side automatic decompression запрещена. Для объявленного `http_body` либо
body output `Content-Encoding` должен отсутствовать или быть единственным token
`identity` case-insensitively; любое другое/составное/повторное значение даёт terminal
`ERROR`. Zero-length entity даёт `MISSING`; иначе entity обязана быть UTF-8 JSON без
BOM, NaN/Infinity и duplicate object keys, иначе terminal `ERROR`. Pointer `""`
возвращает весь JSON value; для object token является exact key, для array — canonical
decimal index без leading zero (кроме `0`), а `-`, out-of-range или невозможный
traversal дают `MISSING`. JSON `null` остаётся `VALUE(null)`.

### 6.4. Входы и выходы

Input — closed object с обязательными полями `input_id`, `display_order`, `target`,
`source`, `semantic_type` и conditional `type_provenance`; другие поля запрещены.
`target` — closed discriminated union:

| `location` | Обязательные поля | Запрещённые поля |
|---|---|---|
| `path`, `query`, `header` | `location`, непустой `name`, boolean `sensitive` | `pointer` |
| `body` | `location`, RFC 6901 `pointer`, boolean `sensitive` | `name` |
| `arg` | `location`, capability argument `name`, boolean `sensitive` | `pointer` |

`source` — closed discriminated union:

| `kind` | Обязательные поля кроме `kind` | Смысл |
|---|---|---|
| `literal` | JSON `value` | несекретное literal value |
| `step_output` | `step_id`, `output_id` | output строго более раннего шага |
| `fixture` | непустой `name` | project-native fixture |
| `environment` | непустой `name` | несекретная настройка среды |
| `secret_handle` | непустые `handle`, `safe_label` | opaque ссылка и безопасная подпись без secret value |

У каждого target/source variant `additionalProperties: false`; listed fields
обязательны, остальные запрещены. `semantic_type` расположен только на top-level
input. `type_provenance` расположен только там же: он обязателен и непуст для
`fixture|environment|secret_handle`, запрещён для `literal|step_output`.
`sensitive: true` разрешён тогда и только тогда, когда source — `secret_handle`;
секретный `handle` никогда не попадает в human projection или stdout, используется
только `safe_label`.

Выход — closed object с обязательными `output_id`, `display_order`, `source`,
`semantic_type` и conditional `type_provenance`; другие поля запрещены. `source` —
closed union:

| `kind` | Обязательные поля кроме `kind` | Допустимая operation |
|---|---|---|
| `http_status` | нет | `http` |
| `http_header` | непустой `name` | `http` |
| `http_body` | RFC 6901 `pointer` | `http` |
| `project_result` | capability result `name` | `project_action` |

`semantic_type` расположен только на top-level output. Для `http_status` он обязан
быть JSON integer, для `http_header` — JSON string, для `project_result` — точно
совпадать с capability result. Только `http_body` требует непустой top-level
`type_provenance`; остальные variants запрещают это поле.
Имя `http_header` в output и assertion actual также хранится только как canonical
lowercase ASCII RFC 9110 `tchar`; response lookup при этом остаётся case-insensitive.

Type descriptor — closed union двух форм: JSON descriptor имеет ровно
`kind: "json"` и `type` из
`null|boolean|integer|number|string|array|object`; named descriptor имеет ровно
`kind: "named"`, ASCII `name` по pattern `^[A-Za-z_][A-Za-z0-9_.-]*$` и
`representation` из того же JSON-type enum. Stored descriptor проверяется
относительно source: `literal` выводится из JSON value; `step_output` совпадает с
producer output; fixture/environment/secret подтверждаются их provenance; capability
arguments/results используют тот же descriptor. Named types совместимы только при
полном совпадении `name` и representation; JSON types — при равенстве либо
единственном widening `integer -> number`. Неизвестный тип не обозначается `any`: он
создаёт blocker на source/type field и не считается готовым binding.

Ссылка на результат всегда имеет пару `step_id + output_id`. Номер отображения в
ссылках не используется.

Пример:

```json
{
  "input_id": "INPUT-confirm-order-id",
  "display_order": 1,
  "target": {"location": "path", "name": "order_id", "sensitive": false},
  "source": {
    "kind": "step_output",
    "step_id": "STEP-create-order",
    "output_id": "order_id"
  },
  "semantic_type": {"kind": "named", "name": "order_id", "representation": "string"}
}
```

В примере stored `semantic_type` обязан в точности совпасть с resolved type
`STEP-create-order + order_id`; это проверяемое дублирование для локальной
диагностики, а не второй источник type truth.

### 6.5. Ожидаемые факты и assertions

Ожидаемый результат шага — упорядоченный массив самостоятельных проверяемых
фактов:

```json
{
  "expectation_id": "EXP-confirmed-status",
  "display_order": 2,
  "text": "В ответе поле status имеет значение confirmed.",
  "assertions": [
    {
      "assertion_id": "ASSERT-confirmed-status",
      "display_order": 1,
      "actual": {
        "kind": "http_body",
        "pointer": "/status",
        "semantic_type": {"kind": "json", "type": "string"},
        "type_provenance": ["https://example.invalid/spec#/status"]
      },
      "operator": "equals",
      "expected": {"kind": "literal", "value": "confirmed"}
    }
  ]
}
```

Assertion DSL остаётся семантическим. Начальный закрытый набор операторов включает
`equals`, `not_equals`, `exists`, `not_exists`, `contains`, `matches`,
`greater_than`, `greater_or_equal`, `less_than`, `less_or_equal`, `length_equals`
и `schema_matches`. Операторы, которым не нужен expected operand, запрещают его;
остальные требуют.

Closed union `actual` содержит только следующие exact objects:

- `{kind:"http_status"}`, resolved type integer;
- `{kind:"http_header", name}`, resolved type string;
- `{kind:"http_body", pointer, semantic_type, type_provenance}` с RFC 6901 pointer и
  непустой provenance;
- `{kind:"project_result", name}` с result referenced step capability;
- `{kind:"step_output", step_id, output_id}` строго более раннего шага.

Current-result variant обязан совпадать с `operation.kind`; earlier output уже
проверен относительно producer. Иных `actual.kind` нет.

Closed union `expected` содержит только следующие exact objects:

- `{kind:"literal", value}`; type выводится из JSON value;
- `{kind:"step_output", step_id, output_id}` более раннего шага;
- `{kind:"fixture"|"environment", name, semantic_type, type_provenance}` с непустой
  provenance;
- `{kind:"secret_handle", handle, safe_label, semantic_type, type_provenance}` с
  непустыми строками и provenance; secret value в JSON не попадает;
- `{kind:"regex", dialect:"portable-regex-v1", pattern}`;
- `{kind:"schema_ref", uri, sha256, draft:"2020-12", provenance}` с непустой
  provenance и digest lexical format раздела 5.

У каждого actual/expected variant `additionalProperties: false`; listed fields
обязательны, отсутствующие в exact object поля запрещены. Сам assertion — closed
object ровно с `assertion_id`, `display_order`, `actual`, `operator` и conditional
`expected`; expectation — closed object ровно с `expectation_id`, `display_order`,
`text`, `assertions`.

`regex` и `schema_ref` разрешены только назначенными ниже operators. Удалённый schema
не загружается во время генерации: bytes уже присутствуют в разрешённом context и
совпадают с `sha256`.

`portable-regex-v1` — отдельный language-neutral dialect, а не обещание одинакового
поведения Python/Java host regex. Pattern содержит 1..512 Unicode scalar values;
unpaired surrogates и raw CR/LF/tab запрещены. Полная грамматика:

```text
pattern      := alternative ("|" alternative)*
alternative  := piece+
piece        := scalar_atom quantifier? | "(" pattern ")"
scalar_atom  := literal | "." | char_class
quantifier   := "?" | "*" | "+" | "{" uint "}" | "{" uint "," uint "}"
char_class   := "[" class_item+ "]"
```

`literal` — любой scalar, кроме ``.\|()[]?*+{}``, либо один из этих metacharacters
после `\`. Разрешены ровно escapes `\\`, escaped metacharacter, `\n`, `\r`, `\t`;
другой escape невалиден. Class не имеет negation; item — literal scalar, escape
`\]`, `\-`, `\\`, `\n`, `\r`, `\t` либо ASCII range `x-y` с возрастающими printable
ASCII endpoints. Quantifier нельзя применять к group. Decimal `uint` не имеет
leading zero, кроме `0`, и удовлетворяет `0 <= m <= n <= 1000`.

Match всегда full-string над Unicode scalar values. `.` совпадает с одним scalar,
кроме LF U+000A; newline совпадает только через `\n` или class item. Group только
группирует и не создаёт observable capture. Boolean semantics определяет Thompson
NFA (не backtracking/leftmost capture); `?`, `*`, `+`, `{m}`, `{m,n}` имеют обычные
count semantics. Anchor constructs отсутствуют: `^` и `$` вне class — обычные
literals. Empty alternatives/groups, negated classes, shorthand classes,
backreferences, lookaround, named/capturing output, inline flags, Unicode properties
и любые иные constructs запрещены. Adapter, не реализующий этот exact dialect либо
JSON Schema Draft 2020-12, требует blocker, а не подменяет semantics.

Operator contract закрыт:

- `equals/not_equals`: expected kind из
  `literal|step_output|fixture|environment|secret_handle` и compatible resolved
  types;
- `exists/not_exists`: expected отсутствует;
- `contains`: actual имеет string type, expected kind из пяти value variants выше
  также разрешается в string; array semantics в V3 намеренно отсутствует;
- `matches`: actual string, expected только `regex`;
- `greater_than/greater_or_equal/less_than/less_or_equal`: actual resolved type
  numeric; expected kind из пяти value variants выше тоже numeric с разрешённым
  `integer -> number` widening;
- `length_equals`: actual string/array/object, expected только non-negative integer
  literal;
- `schema_matches`: actual object/array, expected только `schema_ref`.

Любая другая пара kind/operator является schema либо semantic error. Каждый
assertion value source проходит те же type-resolution rules, что input/output.
Equality является JSON-deep equality без string/boolean coercion: arrays сравниваются
порядково, objects — по unordered key/value map, finite integer/number — по точному
математическому значению. `contains` ищет contiguous Unicode-code-point substring;
`length_equals` считает Unicode code points, array elements или object properties.
Эти правила одинаковы для Python и Java adapters и покрываются shared fixtures.

Runtime observable разрешается в tagged state `VALUE(value)` либо в отдельный
`MISSING`, который не является JSON `null` и никогда не сериализуется как data.
Отсутствующий HTTP header/body pointer, отсутствующий declared project result или
producer `step_output == MISSING` дают `MISSING` только после успешно завершившейся
operation. Неисполненная operation и недоступный value provider являются failure, а
не sentinel.

Граница execution phases нормативна и не зависит от framework lifecycle:

1. До публикации semantic validator полностью проверяет `literal`; невалидный
   literal/type/target останавливает pipeline и до execution не доходит.
2. Общий preflight до запуска любого required symbol разрешает все distinct
   `base_url_source` и все `fixture|environment|secret_handle`, на которые ссылаются
   inputs либо assertion expected values, а также проверяет доступность referenced
   project adapters. Provider не может зависеть от результата case step; такая
   зависимость моделируется отдельным `project_action` и `step_output`.
3. Missing/inaccessible provider, denied secret access, provider setup exception,
   empty required setting, type mismatch либо target/profile-invalid provider value
   дают run-level `NOT_RUNNABLE`, zero required-symbol starts и zero HTTP/project
   operation calls. Значения остаются transient in-memory и не попадают в artifacts,
   stdout, trace или evidence. Adapter не вправе отложить этот preflight source до
   symbol execution и переклассифицировать ту же причину в `ERROR`.
4. `step_output` разрешается только после producer operation. Когда он является
   source input binding, его `MISSING`, runtime type mismatch либо target/profile-
   invalid value дают terminal `ERROR` required pair consumer и итоговый `FAIL`;
   consumer operation не вызывается. Когда `step_output` является assertion
   actual/expected value source, `MISSING` обрабатывается только operator contract
   ниже и не переклассифицируется автоматически в execution error. Unhandled
   adapter/operation/transport error после начала required symbol даёт terminal
   `ERROR` и `FAIL`.

`MISSING` существует только во внутреннем runtime evaluator: он никогда не
сериализуется в canonical JSON, automation artifact, trace, receipt или evidence
payload. Если уже объявленный input со source `step_output` разрешился в `MISSING`,
consumer operation не вызывается, а каждый required runtime pair, отвечающий за этот
consumer step, получает terminal `ERROR`; общий итог — `FAIL`. Если producer и
consumer реализованы одним symbol, единственная evidence record этой пары также
завершается `ERROR`. Это runtime failure, обнаруживаемый после выполнения producer,
а не preflight `NOT_RUNNABLE`.

Optional capability argument представляется отсутствием input binding. Однажды
объявленный input не пропускается и не заменяется default из-за `MISSING`, даже если
его capability argument optional; те же правила действуют для HTTP binding.

`exists` проходит только для `VALUE`, включая `VALUE(null)`; `not_exists` — только
для `MISSING`. Для `MISSING` любой другой operator, включая `not_equals`, создаёт
failed assertion и итоговый `FAIL`, без coercion. Поэтому отсутствие поля никогда не
смешивается с JSON null и не превращается в ложный PASS.

Если сохранение состояния можно доказать только новым чтением, добавляется новый
шаг `GET`. Assertion другого запроса нельзя прятать в предыдущий шаг.

### 6.6. Контракт человеческого тест-дизайна

- Бизнес-сценарии берутся из аналитики; код используется как справочник точных
  имён, контрактов, ошибок, таблиц, логов и метрик.
- Один кейс имеет одну проверяемую цель, но столько шагов, сколько нужно для её
  достижения и доказательства.
- Title конкретно называет сценарий и результат, без формулировок вроде «Тест API».
- Action является самостоятельной инструкцией человеку: называет операцию, объект,
  значимые данные и источник результата предыдущего шага.
- При `step_output` action человеческим языком говорит, что значение получено на
  предыдущем шаге; техническая связь остаётся в structured input.
- Каждая expectation описывает один детерминированный факт. Вариант «A или B»
  разбивается на отдельные кейсы или уточняется по требованиям.
- Ожидания описывают наблюдаемый ответ и состояние, а не абстрактное «всё успешно».
- Неизвестные поля, коды, ошибки и артефакты не выдумываются.
- Дублирующие кейсы под разными названиями запрещены.
- Жёсткие ограничения длины и максимум десять шагов не переносятся: предоставленный
  Zephyr export их не подтверждает. Текст остаётся кратким по правилу качества, а не
  за счёт недоказанного schema limit.

## 7. Идентичность и порядок

- `case_id`, `step_id`, `expectation_id`, `assertion_id` и `output_id` не зависят от
  позиции в массиве и от digest изменяемого текста.
- Первичные IDs отражают устойчивую семантическую роль, например
  `TC-order-confirmation-success` и `STEP-confirm-order`.
- `ТК-N` и `№` являются только вычисляемыми отображаемыми номерами.
- В каждой области `display_order` уникален и образует непрерывный диапазон `1..N`.
- Physical array order совпадает с `display_order` по правилу раздела 5; consumers
  не исправляют и не пересортировывают невалидный document.
- При изменении текста или перестановке сущность сохраняет ID.
- При разделении сущности ближайший семантический потомок сохраняет исходный ID,
  остальные получают новые.
- При наличии предыдущей ревизии generator/reviewer обязаны сопоставлять сущности и
  сохранять IDs; самовольная массовая перенумерация является blocking finding.
- `document_id` одновременно является semantic ID и частью имени файла, поэтому
  имеет отдельный filename-safe и case-fold-safe контракт: фиксированный uppercase
  prefix и только lowercase suffix, pattern
  `^TCDOC-[a-z0-9](?:[a-z0-9_.-]*[a-z0-9_-])?$`, `maxLength: 102` (не более 96
  символов после prefix). Значение с uppercase-символом в suffix, `:`,
  slash/backslash, trailing dot или превышением длины не нормализуется, а
  отклоняется schema validation.
- `revision` ограничен положительным integer `1..2147483647`; вместе с лимитом
  `document_id` это сохраняет каждое имя bundle-файла заметно короче 255 символов.
- Остальные ID используют соответствующий prefix (`CAP-`, `REQ-`, `TC-`, `STEP-`,
  `EXP-`, `ASSERT-`, `INPUT-`, `BLOCK-`) и символы `[A-Za-z0-9_.:-]`; display
  number в ID запрещён как единственная семантика.
- `output_id` является локальным binding name, а не глобальным prefixed ID; он
  использует pattern `^[A-Za-z_][A-Za-z0-9_.-]*$` и разрешается только вместе с
  родительским `step_id`. Поэтому ссылка всегда остаётся парой
  `step_id + output_id`.

## 8. Предусловия, manual-only и блокеры автоматизации

Предусловие — уже истинный факт до запуска кейса. Любая операция создания или
изменения состояния является шагом.

`manual_only` и недостаток контекста — разные состояния:

- `manual_only: true` означает, что шаг действительно нельзя автоматизировать;
  `manual_reason` обязателен, `automation_blockers` пуст, assertions запрещены;
  известная `operation` может сохраняться только как структурное описание и не
  создаёт implementation relation;
- `manual_only: false` и пустой `automation_blockers` означают готовый к
  автоматизации шаг: обязательны operation и как минимум один assertion у каждой
  expectation;
- `manual_only: false` и непустой `automation_blockers` означают, что человеческий
  кейс допустим, но технический blueprint неполон. Причины должны назвать
  конкретно отсутствующий контекст. `tc-to-autotest` обязан остановиться, а не
  придумывать данные и не превращать шаг в manual-only.

Каждый blocker — closed object (`additionalProperties: false`) ровно с полями
`blocker_id`, `code`, `field_path`, `reason`, `provenance`. `blocker_id` подчиняется
правилам раздела 7; `reason` — непустая trimmed human-readable строка;
`provenance` — непустой order-significant массив предоставленных источников. Поле
`code` является закрытым enum:

- `UNRESOLVED_OPERATION`;
- `UNSUPPORTED_AUTOMATION_CAPABILITY`;
- `UNRESOLVED_INPUT_BINDING`;
- `UNRESOLVED_OUTPUT_BINDING`;
- `UNRESOLVED_ASSERTION`;
- `UNCONFIRMED_TYPE`;
- `UNSUPPORTED_ASSERTION_DIALECT`.

`field_path` — RFC 6901 pointer относительно owning step, причём разрешены только
`/operation`, `/inputs`, `/outputs` и
`/expectations/<zero-based-index>/assertions`. Index записывается canonical decimal
без leading zero, кроме самого `0`, и обязан указывать существующую expectation в
physical canonical order (`display_order == index + 1`). Допустимые пары закрыты:

| `code` | Допустимый `field_path` |
|---|---|
| `UNRESOLVED_OPERATION`, `UNSUPPORTED_AUTOMATION_CAPABILITY` | `/operation` |
| `UNRESOLVED_INPUT_BINDING` | `/inputs` |
| `UNRESOLVED_OUTPUT_BINDING` | `/outputs` |
| `UNRESOLVED_ASSERTION`, `UNSUPPORTED_ASSERTION_DIALECT` | `/expectations/<index>/assertions` |
| `UNCONFIRMED_TYPE` | `/inputs`, `/outputs` или `/expectations/<index>/assertions` |

Blocker указывает контейнер отсутствующего технического элемента; `reason` называет
сам binding/output/assertion и недостающий факт, а `provenance` доказывает, какой
предоставленный контекст оказался недостаточным. Любая иная форма, code/path pair
или pointer является schema/semantic error. Свободная warning-строка blocker не
заменяет.

Незавершённые technical objects никогда не публикуются наполовину. Каждый
присутствующий `operation`, input, output и assertion обязан полностью проходить
свою closed schema и semantic validation. Неразрешённая operation представляется
`operation: null` плюс blocker на `/operation`; неразрешённый input/output целиком
отсутствует в соответствующем массиве плюс blocker на `/inputs` или `/outputs`;
неразрешённый assertion отсутствует, а blocker указывает на assertions конкретной
expectation. `null`, placeholder и неизвестные поля внутри union variant запрещены.

Поле `assertions` присутствует у каждой expectation всегда. У manual-only шага все
эти массивы пусты и blockers отсутствуют. У готового automatable шага каждый массив
непуст. У blocked шага массив содержит только полностью валидные assertions и может
быть пустым, но каждая expectation с пустым массивом обязана иметь хотя бы один
blocker на свой точный `/expectations/<index>/assertions`. Если у blocked step
`operation: null`, среди blockers обязательно присутствует совместимый blocker на
`/operation`.

Частично ручной шаг разделяется на автоматизируемый и ручной шаг. Автоматизируемый
шаг не может зависеть от результата ручного шага без явного альтернативного fixture
или environment source.

Итоговые статусы не скрывают ручной остаток:

- `PASS` — все ожидания автоматизируемы, трассированы и успешно выполнены;
- `PASS_WITH_MANUAL_REMAINDER` — автоматизируемая часть прошла, но существуют
  обоснованные manual-only шаги;
- `MANUAL_ONLY` — автоматизируемых шагов нет, pipeline ничего не запускал;
- `BLOCKED` — присутствует хотя бы один automation blocker; automation output и
  запуск не создаются;
- `FAIL` — проверка, трассировка или выполненный тест завершились неуспешно;
- `NOT_RUNNABLE` — автоматизируемые шаги существуют, но среда/runner не позволили
  выполнить их.

`BLOCKED` имеет приоритет над результатами готовых шагов. `PASS_WITH_MANUAL_REMAINDER`
и `MANUAL_ONLY` не утверждают прохождение ручных проверок.

## 9. Semantic validator

JSON Schema проверяет форму. Отдельный validator проверяет межобъектные инварианты:

- уникальность всех IDs в их областях;
- непрерывность и уникальность `display_order`;
- canonical physical order всех arrays по правилам раздела 5;
- существование requirement links;
- полное покрытие требований;
- существование step/output references;
- ссылки только на более ранние шаги;
- отсутствие циклов;
- разрешимость каждого input/output/value-source semantic type и compatibility по
  правилам раздела 6.4;
- exact `http-binding-v1`, валидность base source, path template, соответствие
  placeholders их inputs, query/header rules, body assembly и уникальность effective
  input targets;
- совместимость input target, output source и current-result assertion `actual` с
  `operation.kind`;
- существование `project_action.capability_id`, уникальность `adapter + action` и
  соответствие каждого arg/result имени и semantic type capability contract;
- ровно один binding каждого required capability argument, не более одного binding
  любого argument и запрет undeclared arguments/results;
- запрет raw secret values для чувствительных targets;
- валидность operator/operand;
- принадлежность каждого assertion ровно одной expectation;
- assertions для каждого автоматизируемого готового ожидания;
- условия manual-only и automation blockers;
- структуру blocker, наличие evidence и соответствие field path реально
  отсутствующему/неподтверждённому техническому элементу;
- запрет зависимости автоматизированного шага от ручного без альтернативного
  источника.

Для `http` допустимы только targets `path/query/header/body` и current-result sources
status/header/body; `project_result` запрещён. Для `project_action` допустимы только
`arg` targets и `project_result`, объявленные referenced capability; HTTP sources
запрещены. Cross-kind mismatch всегда является schema/semantic error. Отсутствующий
или неподтверждённый project contract не маскируется произвольным source: operation
остаётся unresolved, а готовность шага выражается точным blocker field path.

В одном HTTP step каждый effective target имеет ровно один binding. `path` и `query`
names сравниваются как точные case-sensitive строки; request header names обязаны
уже находиться в canonical lowercase и сравниваются exact. Body targets
сначала разбираются на RFC 6901 token arrays (`~0`/`~1` декодируются); равные token
arrays и пары ancestor/descendant запрещены, включая root pointer как ancestor любого
не-root pointer. Поэтому `/a` конфликтует с `/a/b`, а `/a` не конфликтует с `/ab`.
Для `project_action` exact argument name уже подчиняется правилу одного binding.

HTTP validator дополнительно применяет все compile-time правила
`http-binding-v1`: exact profile/source shape, grammar path и placeholder set,
lowercase `tchar` header names, reserved-header ban, scalar query values и valid RFC
6901 body pointers. Base setting value и resolved runtime values проверяются adapter
до отправки по preflight/runtime правилам профиля; ни одна такая ошибка не может
деградировать в пропуск binding или `MISSING`-PASS.

Target type contract: HTTP `path` и `header` принимают string либо named type со
string representation; `query` принимает JSON scalar либо named scalar; `body` —
любой resolvable type. `project_action` argument принимает только type, compatible с
descriptor capability argument. Несовместимость является semantic error, а
неявное преобразование string/number/boolean запрещено.

Semantic validator запускается после каждого создания или исправления канонической
ревизии и до создания проекций.

## 10. Жизненный цикл трёх артефактов

Для каждой ревизии резервируются три пути:

- `<document-id>.r<revision>.json`;
- `<document-id>.r<revision>.md`;
- `<document-id>.r<revision>.zephyr-scale.csv`.

`<document-id>` и десятичный `<revision>` подставляются в имена буквально после
schema validation; sanitizing, case folding и иное неинъективное преобразование
запрещены.

Четвёртый manifest-файл не создаётся. Projection CLI возвращает в stdout JSON
receipt с `document_id`, `revision`, `csv_profile`, тремя путями,
`document_sha256`, `markdown_sha256` и `csv_sha256`; verify-only заново строит все
три payload в памяти и сравнивает точные bytes каждого target.

Один bundle publisher принимает валидный canonical object и строит все три payload:

- JSON — canonical serialization, определённая в разделе 5;
- Markdown — правила раздела 11;
- CSV — правила раздела 12 и фиксированный для V3 профиль
  `zephyr-scale-step-row-24-v1`.

Publisher до записи вычисляет все три digest и проверяет все целевые пути. Если
существующий файл отличается, операция останавливается до любых изменений. Затем
все недостающие payload записываются во временные файлы в целевом каталоге.

Установка выполняется в фиксированном порядке JSON -> Markdown -> CSV атомарной
операцией create-if-absent. Нормативная реализация создаёт hard link из temporary
file в target через `os.link`: на одной файловой системе операция атомарна и не
заменяет существующий target. `FileExistsError` обрабатывается чтением target:
идентичные bytes считаются успехом для этой части, отличающиеся — конфликтом с
немедленной остановкой до следующего target. После успешной установки temporary
link удаляется. `os.replace` и любой overwrite существующего revision path
запрещены.

Перед публикацией tool проверяет поддержку atomic hard-link в целевом каталоге на
одноразовой паре temporary probe files и удаляет их. Если primitive недоступен,
publication завершается до записи bundle; молчаливого fallback на небезопасный
overwrite нет.

Файловая система не даёт общей атомарной операции для трёх файлов. Поэтому контракт
является recoverable publish: если процесс остановился между установками, повторный
запуск проверяет идентичные части и дописывает недостающие. Фиксированный порядок и
create-if-absent гарантируют, что конкурентный publisher с другим JSON остановится
на первом target, а publisher с тем же JSON и другими projection bytes остановится
на первой отличающейся проекции, не перезаписав победившие bytes. Receipt считается
успешным только после read-back и повторной проверки bytes всех трёх файлов.

Publication и selection — разные события. Каждая schema+semantic-valid candidate
revision, включая исходный generator output, получает неизменяемый трёхфайловый
bundle: это выполняет требование «после генерации всегда экспортировать Markdown и
CSV». Reviewer затем выбирает этот bundle либо создаёт, валидирует и публикует полный
successor bundle. При `ТРЕБУЕТ ДОРАБОТКИ` candidate bundle остаётся только audit
evidence и никогда не передаётся downstream. В каждый момент downstream pipeline
использует только bundle одной явно выбранной ревизии.

Порядок pipeline:

1. generator создаёт candidate canonical revision;
2. schema + semantic validation;
3. publisher создаёт её JSON + Markdown + CSV bundle и проверяет три digest;
4. reviewer проверяет полный candidate document и его digest;
5. при `ПРИНЯТО` candidate назначается effective revision;
6. при `AUTO_FIX_APPLIED` полный successor проходит schema+semantic validation и
   selection guards идентичности, revision и parent digest, затем трёхфайловую
   publication и только после неё назначается effective revision;
7. при `ТРЕБУЕТ ДОРАБОТКИ` selection не выполняется;
8. effective revision передаётся в `tc-to-autotest`, autotest reviewer, runner и
   trace;
9. automation и trace сверяют `document_id`, `revision` и JSON digest effective
   revision; Markdown/CSV receipts сверяются перед завершением orchestration.

## 11. Markdown-проекция

Для HTTP subject формат фиксирован:

```markdown
# Тест-кейсы метода [HTTP_METHOD] [ПУТЬ]

**Документация:** [ССЫЛКА]
**Project:** [PROJECT]
**Автор:** [AUTHOR]
**Дата:** [DATE]

---

## ТК-1. [Описание]

**Цель:** [Цель]

**Предусловия:**
- [Предусловие]

**Шаги:**

| № | Действие | Ожидаемый результат |
|---|---|---|
| 1 | ... | ... |
```

Нормативные bytes: UTF-8 без BOM, перенос строк LF, ровно один LF в конце файла,
никаких пробелов в конце строк. После metadata идёт одна пустая строка, `---`, одна
пустая строка и первый кейс. Между кейсами идёт одна пустая строка, `---`, одна
пустая строка. После последнего кейса разделителя нет.

Правила содержимого:

- кейсы и шаги читаются в уже проверенном canonical physical order без re-sort;
- `ТК-N` и `№` вычисляются и не заменяют стабильные IDs;
- отдельной колонки Test Data нет;
- action берётся из канонического человеческого текста, а не синтезируется из
  operation;
- тексты expectations объединяются в одной ячейке через `<br>` в display order;
- `|` экранируется как `\|`, физические переводы строк внутри ячейки — как `<br>`;
- при отсутствии предусловий выводится `- Не требуются.`;
- несколько documentation links объединяются через `<br>`; пустой список
  отображается как `Не предоставлена` и сопровождается JSON warning, а не фиктивной
  ссылкой;
- project/author/date берутся только из canonical metadata; обязательные для запуска
  metadata не заменяются placeholder;
- generic subject получает заголовок `# Тест-кейсы: <subject.name>`.

Одна функция `escape_inline` используется во всех пользовательских значениях в
заголовках, metadata, objective, preconditions и table cells. Она выполняет операции
в фиксированном порядке: нормализует CRLF/CR в LF, удваивает backslash, ставит
backslash перед `` ` * _ [ ] < > # | ``, затем заменяет LF на созданный renderer-ом
`<br>`. Сгенерированные Markdown markers и `<br>` не проходят повторное escaping.
Tool никогда не редактирует исходный canonical text.

Preconditions — строки без собственного `display_order`; generator сохраняет их в
canonical array уже в требуемом человеку порядке. Markdown выводит их отдельными
bullet lines строго в порядке массива, без сортировки и дедупликации; пустой список
даёт единственную строку `- Не требуются.`. Expectations объединяются в table cell
через `<br>` без дополнительной пунктуации. Golden fixtures фиксируют полный файл с
одним и несколькими кейсами, минимум двумя предусловиями, специальными символами и
terminal LF.

## 12. Zephyr Scale CSV-проекция

V3 поддерживает ровно один встроенный профиль
`zephyr-scale-step-row-24-v1`, воспроизводящий наблюдённый порядок 24 колонок:

1. `Key`
2. `Name`
3. `Status`
4. `Precondition`
5. `Objective`
6. `Folder`
7. `Priority`
8. `Component`
9. `Labels`
10. `Owner`
11. `Estimated Time`
12. `Coverage (Issues)`
13. `Coverage (Pages)`
14. `АС`
15. `Автоматизирован`
16. `Вид тестирования`
17. `Команда`
18. `Приоритет теста`
19. `Статус`
20. `Test Script (Step-by-Step) - Step`
21. `Test Script (Step-by-Step) - Test Data`
22. `Test Script (Step-by-Step) - Expected Result`
23. `Test Script (Plain Text)`
24. `Test Script (BDD)`

Одна каноническая step создаёт одну CSV-строку. Поля кейса заполняются только в
первой строке, continuation rows оставляют колонки 1–19 пустыми.

Кейсы и steps читаются в проверенном canonical physical order без re-sort. Пустых
разделительных строк между кейсами нет. Каждый кейс имеет
минимум один шаг, поэтому metadata row всегда существует.

Нормативный mapping всех колонок (`empty` означает пустую CSV cell, а не пробел):

| № | Header | Canonical source / constant | Renderer и default | Строки |
|---:|---|---|---|---|
| 1 | `Key` | `management.external_keys.zephyr_scale` | string; absent -> empty; `case_id` не подставляется | только первая step row кейса |
| 2 | `Name` | `title` | required string | только первая |
| 3 | `Status` | `management.status` | string; `null` -> empty | только первая |
| 4 | `Precondition` | `preconditions` | strings в canonical array order, join LF; empty array -> empty | только первая |
| 5 | `Objective` | `objective` | required string | только первая |
| 6 | `Folder` | `management.folder` | string; `null` -> empty | только первая |
| 7 | `Priority` | `priority` | `CRITICAL -> Highest`, `HIGH -> High`, `MEDIUM -> Normal`, `LOW -> Low`; иные значения schema запрещает | только первая |
| 8 | `Component` | `management.components` | standard-list renderer; empty array -> empty | только первая |
| 9 | `Labels` | `management.labels` | standard-list renderer; empty array -> empty | только первая |
| 10 | `Owner` | `management.owner` | string; `null` -> empty | только первая |
| 11 | `Estimated Time` | `management.estimated_time` | string; `null` -> empty | только первая |
| 12 | `Coverage (Issues)` | `management.external_links.issues` | standard-list renderer; empty array -> empty; `REQ-*` не подставляются | только первая |
| 13 | `Coverage (Pages)` | `management.external_links.pages` | standard-list renderer; empty array -> empty | только первая |
| 14 | `АС` | `management.custom_fields["АС"]` | custom-scalar renderer; absent -> empty | только первая |
| 15 | `Автоматизирован` | `management.custom_fields["Автоматизирован"]` | custom-scalar renderer; absent -> empty | только первая |
| 16 | `Вид тестирования` | `management.custom_fields["Вид тестирования"]` | custom-scalar renderer; absent -> empty | только первая |
| 17 | `Команда` | `management.custom_fields["Команда"]` | custom-scalar renderer; absent -> empty | только первая |
| 18 | `Приоритет теста` | `management.custom_fields["Приоритет теста"]` | custom-scalar renderer; absent -> empty | только первая |
| 19 | `Статус` | `management.custom_fields["Статус"]` | custom-scalar renderer; absent -> empty | только первая |
| 20 | `Test Script (Step-by-Step) - Step` | `step.action` | required string | каждая step row |
| 21 | `Test Script (Step-by-Step) - Test Data` | `step.inputs` | Test Data renderer ниже; empty array -> empty | каждая step row |
| 22 | `Test Script (Step-by-Step) - Expected Result` | `step.expectations[].text` | validated physical order, join LF | каждая step row |
| 23 | `Test Script (Plain Text)` | constant | empty | каждая step row |
| 24 | `Test Script (BDD)` | constant | empty | каждая step row |

Continuation rows всегда оставляют колонки 1–19 empty независимо от management.
У профиля нет скрытых defaults: единственное преобразование enum — таблица Priority
выше. Значение `Normal` подтверждено предоставленным экспортом; остальные три пары —
явное V3 design decision и проверяются golden fixtures, но не выдаются за доказанный
tenant import contract. Custom-field keys, не перечисленные в строках 14–19, в этом
профиле не экспортируются и дают warning с их именами; они не переназначаются по
похожему написанию или позиции.

Step-output input отображается как
`<target> = <output_id>, полученный на шаге <display_order>`. Literal values
сериализуются компактно; secret handle отображается безопасным label без значения.

Точный renderer Test Data:

- inputs читаются в validated physical order и объединяются LF без re-sort;
- target рендерится как `path:<name>`, `query:<name>`, `header:<name>`,
  `body:<json-pointer>` или `arg:<name>`;
- `literal` рендерится canonical compact JSON с Unicode и сортировкой object keys;
- `step_output` — `<output_id>, полученный на шаге <display_order>`;
- `fixture` — `fixture:<name>`;
- `environment` — `env:<name>`;
- `secret_handle` — `secret:<safe_label>` без значения секрета.

Итоговая строка input имеет вид `<target> = <rendered-source>`. Номера предыдущих
шагов вычисляются по ссылочному `step_id`; отсутствующая ссылка является semantic
error, а не пустой CSV cell.

Standard list fields (`Component`, `Labels`, Coverage) сохраняют canonical order и
объединяются `, `. Custom-field scalar renderer задаёт: string без изменения,
boolean как `true`/`false`, integer как base-10, finite number как canonical JSON
number. Массив scalar values сохраняет порядок и объединяется `, `. `null`, object и
NaN/Infinity в custom fields запрещены schema; пустую cell означает отсутствующий
key, а не `null`.

Профиль задаёт порядок колонок, mapping custom fields и canonical-enum mappings,
delimiter, newline и encoding. XLSX не содержит доказательств CSV encoding или
delimiter. Для детерминированного первого профиля проектным решением выбираются
UTF-8 с BOM, запятая, CRLF и RFC 4180 quoting; golden-тесты фиксируют bytes, но это
решение не выдаётся за доказанный import contract.

Неизвестный `csv_profile` отклоняется до publication. Будущий tenant-specific
профиль получает новый явный ID и breaking bundle namespace/contract; он не может
молча записать иные CSV bytes в путь V3 той же ревизии.

Каждая CSV record, включая последнюю, заканчивается CRLF. Пустое значение — пустая
строка. Перед CSV quoting применяется защита от spreadsheet formula injection:
renderer проверяет первый символ после ведущих ASCII space/tab/CR/LF; если он равен
`=`, `+`, `-` или `@`, renderer добавляет один apostrophe перед исходным значением.
Ячейка, начинающаяся с tab/CR/LF и не содержащая последующего текста, также получает
apostrophe. Числовые management values проходят numeric renderer и не считаются
формулой. Golden fixtures покрывают опасные префиксы с пробелами и без них; tenant
profile может выбрать другой доказанный safe policy, но только под новым breaking
profile ID и не может отключить защиту.

CSV является намеренно неполной внешней проекцией: operation, output bindings и
assertions туда не копируются. CSV никогда не используется для восстановления JSON.

## 13. Автоматизация и трассировка

`tc-to-autotest` получает выбранный canonical document напрямую и записывает его
`document_id`, `revision` и digest.

Automation output содержит:

- generated files с globally unique `file_id`, path/language/framework/digest;
- generated symbols с `symbol_id`, уникальным внутри `file_id`, и явным locator:
  - module-level function;
  - class method с qualified class name и method name;
- атомарные implementation relations двух видов:
  - `operation`: `case_id + step_id -> file_id + symbol_id`;
  - `assertion`: `case_id + step_id + expectation_id + assertion_id -> file_id +
    symbol_id`;
- manual dispositions для manual-only steps.

Relation не содержит массивов semantic IDs. До runner и построения trace validator
разрешает каждую relation относительно exact effective canonical document:

- для обоих видов `step_id` принадлежит `case_id`, step готов к автоматизации;
- у `assertion` relation `expectation_id` принадлежит этому step, а `assertion_id` —
  ровно этой expectation;
- у `operation` relation expectation/assertion fields отсутствуют;
- `symbol_id` принадлежит указанному generated file, а file и symbol существуют;
- полный relation key `(kind, case_id, step_id, expectation_id|null,
  assertion_id|null, file_id, symbol_id)` уникален; exact duplicates запрещены.

Relations сериализуются детерминированно: case/step по canonical `display_order`,
затем `operation` перед `assertion`, expectation/assertion по `display_order`, затем
`file_id`, `symbol_id`. Порядок не меняет AND-семантику, но фиксирует output bytes.

Runtime identity symbol всегда является парой `(file_id, symbol_id)`; одинаковый
локальный `symbol_id` в двух файлах означает две разные identities и не схлопывается.
Один runtime symbol может реализовать несколько semantic targets через несколько
атомарных relations. Один target может иметь несколько relations на разные runtime
symbols; это AND, а не alternatives: все такие symbols являются required. Coverage
вычисляется как set distinct validated relations. Каждый automatable step требует
хотя бы одну
`operation` relation, каждое automatable assertion — хотя бы одну `assertion`
relation. Строка с существующими, но взятыми из чужих родителей IDs является `FAIL`
до запуска и ничего не добавляет в coverage.

Manual disposition проходит отдельную parent-chain validation: её `step_id`
принадлежит указанному `case_id`, canonical step имеет `manual_only: true`, и для
каждого manual-only step существует ровно одна disposition. Disposition для
automatable либо blocked step запрещена; reason остаётся в canonical step и не
переопределяется второй редактируемой строкой automation output.

При наличии blocker успешный automation output не создаётся: `tc-to-autotest`
возвращает blocking diagnostic, а orchestrator завершает попытку как `BLOCKED`.
Полностью manual-only документ, напротив, создаёт валидный output с manual
dispositions и пустыми generated files/symbols.

Один test method может реализовать несколько последовательных шагов и assertions;
каждая цель получает отдельную relation на тот же runtime symbol. Неявный декартов продукт
«все requirements кейса × все методы кейса» удаляется.

Runner отдаёт evidence по обязательной паре `file_id + symbol_id`. Дублировать
`step_id` в runtime evidence не требуется: trace выполняет точный join через
проверенные atomic relations.
Физический locator обязан различать одноимённые методы в разных классах одного файла
и явно представлять Java FQN, Python module function или Python class method.

V3 не вводит symbol-content digest: надёжное извлечение одинакового исходного текста
символа из Python/Java потребовало бы отдельного language-specific контракта. Перед
выполнением runner сверяет SHA-256 полных bytes каждого generated file с
`generated_files[].content_digest` и доказывает существование уникального locator в
этом файле. Trace сохраняет `file_id`, file digest, locator и `symbol_id`;
несовпадение digest, отсутствующий или неоднозначный locator блокируют запуск.

Required-symbol set — distinct `(file_id, symbol_id)` всех validated implementation
relations. Для каждой required pair runner нормализует ровно одну terminal evidence
record текущего `run_id`, содержащую оба ID. Trace принимает её только при совпадении
`run_id`, canonical source digest, `(file_id, symbol_id)` и проверенного file digest.
`PASSED` удовлетворяет все relations этой pair; `FAILED`/`ERROR` дают `FAIL`; missing
либо `SKIPPED` дают
`NOT_RUNNABLE` и никогда не считаются PASS. Несколько symbols одной semantic цели
должны все иметь `PASSED` (AND). Duplicate или противоречащие terminal records для
одной required pair являются `FAIL`, а stale evidence другого run игнорируется и
оставляет current evidence missing.

Trace registry сохраняет requirements, cases, steps, expectations, assertions,
files и symbols. Полнота означает:

- каждое автоматизируемое assertion связано хотя бы с одним symbol;
- каждый автоматизируемый step связан хотя бы с одним operation symbol;
- каждый runtime symbol существует в заявленном файле;
- каждая required `(file_id, symbol_id)` имеет ровно одно валидное current-run
  evidence и все
  required relations удовлетворены;
- каждый manual-only шаг имеет причину и не требует symbol;
- blocker не может быть преобразован в PASS или manual-only;
- source digest одинаков в canonical selection, automation output и trace.

## 14. Поведение скиллов

### 14.1. `tc-generator`

- Аналитика остаётся источником бизнес-сценариев; код — техническим справочником.
- Один кейс проверяет одну цель, но может иметь много последовательных шагов.
- Названия, действия и ожидания формулируются конкретно и по-человечески.
- Технические имена из контекста сохраняются точно.
- Подготовка данных становится шагами.
- Неизвестные операции/assertions не выдумываются: создаются blockers.
- Generator возвращает только JSON; deterministic projection tool создаёт Markdown
  и CSV.

### 14.2. `tc-reviewer`

- Проверяет полный документ и все шаги в порядке.
- Проверяет data flow, человеческую ясность и соответствие assertion каждому
  expectation.
- Не синтезирует недостающие ожидаемые результаты без evidence.
- При AUTO_FIX возвращает полный successor document и сохраняет IDs неизменённых
  сущностей; не удаляет и не переносит существующие stable entities между
  родителями. Исправление, требующее такой операции, возвращается как
  `ТРЕБУЕТ ДОРАБОТКИ`.

### 14.3. `tc-to-autotest`

- Не разбирает Markdown/CSV и не извлекает технику из human text.
- Реализует operation, input bindings, outputs и assertions выбранной ревизии.
- Останавливается на blockers.
- Создаёт атомарные operation/assertion relations для каждого готового automatable
  step и assertion без array/cartesian mappings.

### 14.4. `autotest-reviewer` и `orchestrate`

- Autotest reviewer проверяет step/assertion coverage и symbol locators.
- Orchestrator публикует трёхфайловый bundle каждого валидного generated candidate и
  валидного AUTO_FIX successor, затем отдельно выбирает одну effective revision и
  передаёт только её digest всем downstream stages.
- Ни один последующий этап не читает Markdown или CSV как источник истины.
- Pipeline contract получает breaking version bump и явно передаёт selected document
  в automation и trace вместо исходного generator artifact.

## 15. Ошибки и безопасность

- Schema/semantic error: проекции не публикуются, pipeline останавливается.
- Projection collision: существующие отличающиеся bytes не перезаписываются.
- Verify mismatch: ненулевой exit code и список различающихся артефактов.
- Reviewer base digest mismatch: successor отклоняется как stale/conflicting.
- Missing technical context: explicit blocker, не галлюцинация и не manual-only.
- Raw secrets запрещены; используются handles.
- В stdout не печатаются secret values или полный чувствительный payload.
- Присланные рабочие примеры используются только как структурное evidence и не
  сохраняются в репозитории.

## 16. Миграция с 2.1

Автоматическая семантическая миграция в этой работе не реализуется. V3 tools обязаны
явно отклонять v2.1 artifact с сообщением о breaking change.

Будущий диагностический converter может сохранить точные human fields и существующие
IDs, но обязан пометить operation, bindings, outputs и assertions как unresolved и
остановить автоматизацию. Парсить старую прозу и выдавать догадку за технический
blueprint запрещено.

Документация и встроенные fixtures репозитория переводятся на v3 согласованно;
поддержка одновременного mixed v2.1/v3 pipeline не заявляется.

## 17. Компоненты и границы ответственности

1. Общая schema canonical document — единственное определение тест-кейса.
   Generator и reviewer stage schemas ссылаются на неё.
2. Semantic validator — единственное место cross-reference и readiness правил.
3. Projection module — один фасад с Markdown и Zephyr adapters; callers не
   дублируют parsing/mapping.
4. Reviewer revision selection — полный successor и base-digest guard.
5. Automation model — atomic step/expectation/assertion relations и locators.
6. Trace model — точный join без декартовых предположений.
7. Orchestration — выбор effective revision и публикация трёх артефактов.
8. Документация и fixtures меняются после стабилизации executable contracts.

Работа остаётся внутри isolated worktree. Файлы и commits ветки
`automatic-skillsrc-discovery` не изменяются.

## 18. Стратегия проверки

### 18.1. TDD для программных контрактов

До production changes создаются failing tests и fixtures. Минимальный набор:

- valid 1-, 11- и 100-step documents;
- duplicate IDs/orders; permutation отдельно для requirements, cases, steps, inputs,
  outputs, expectations и assertions отклоняется при тех же `display_order`;
- неверный sort capabilities/arguments/results/blockers/requirement_ids/categories
  отклоняется; order-significant arrays сохраняют заданный порядок и меняют digest;
- missing, forward и cyclic output refs;
- positive/negative exact-shape fixtures для обоих `operation` variants, capability,
  capability argument и capability result: правильный discriminator, каждый
  missing/extra/forbidden field, wrong scalar type и invalid identifier; HTTP fixtures
  отдельно требуют exact `binding_profile` и closed `base_url_source`;
- `operation: null` принимается только для корректного manual-only либо blocked step;
  ready automatable step с null и blocked null без совместимого `/operation` blocker
  отклоняются;
- positive/negative schema fixtures каждого input target/source и output source
  exact shape: missing/extra field, `name` против `pointer`, обязательный `sensitive`,
  conditional `type_provenance`, secret handle/safe label;
- mismatched stored/resolved types для literal и step_output; missing provenance или
  incompatible types отдельно для fixture, environment и secret_handle;
- positive и negative fixtures для каждого output variant, каждого closed
  assertion `actual`/`expected` variant, body `type_provenance`, operator/type pair,
  portable regex и pinned schema reference; array `contains` отклоняется;
- `MISSING` против `VALUE(null)` для header, body, project result и step output:
  полная operator matrix доказывает, что только `not_exists` проходит MISSING;
- declared downstream `step_output` input, разрешившийся в `MISSING`, не вызывает
  consumer operation и даёт terminal `ERROR`/общий `FAIL` как для отдельного symbol,
  так и для одного symbol, реализующего producer и consumer; optional argument без
  input binding остаётся допустим, объявленный missing binding не пропускается;
- shared phase matrix проверяет каждый source: invalid `literal` отклоняется до
  publication; missing/inaccessible/setup/type/target failures отдельно для
  `fixture`, `environment`, `secret_handle` и base setting дают `NOT_RUNNABLE`, zero
  required-symbol starts и zero operation/client calls; те же runtime failures для
  `step_output` input binding дают consumer `ERROR`/`FAIL` после producer без
  consumer call, тогда как assertion-source `MISSING` остаётся в operator matrix;
- shared Python/Java regex/equality/length corpus покрывает every grammar production,
  dot-vs-LF, escapes, ASCII ranges, Unicode scalars, bounds и каждый forbidden
  construct; оба adapters дают одинаковый boolean/verdict;
- missing/duplicate path/query target, uppercase/non-`tchar`/reserved либо duplicate
  lowercase header и equal либо
  ancestor/descendant body pointer;
- shared Python/Java `http-binding-v1` golden corpus проверяет abstract request tuple:
  valid/invalid base origins и port, missing base setting (`NOT_RUNNABLE`, zero client
  calls), placeholder repetition и UTF-8 `%HH`, encoded slash/percent/space, literal
  percent-triplets, rendered dot-segment rejection, query order и scalar lexemes,
  header lowercase/reserved/control/
  duplicate rules, отсутствие body, full root scalar/array/object body, non-root
  `/0` как object key, shared object prefixes, pointer escapes, exact canonical body
  bytes и exact injected accept-encoding/user-agent/content-type; runtime
  type/MISSING errors дают zero client calls;
- local HTTP fixture подтверждает одинаковые method/target/header semantics и body
  bytes обоих reference adapters, ровно одну attempt для redirect/retryable status и
  transport failure, отсутствие follow/retry/default cookie/auth; raw gzip fixture
  доказывает отсутствие automatic content decode и deterministic `ERROR` при body
  observation. Response fixtures также покрывают missing header/body, identity
  content-coding, JSON null, object/array pointer traversal, invalid UTF-8/JSON,
  duplicate JSON keys/headers и exact `MISSING` против terminal `ERROR`;
- cross-kind HTTP/project output и assertion source, missing capability, undeclared
  или duplicate argument/result и unbound required capability argument;
- manual-only без причины;
- blocker, ошибочно превращённый в manual-only;
- blocker exact-shape fixtures проверяют каждый enum code, лишнее/отсутствующее поле,
  code/path mismatch, неканонический либо несуществующий expectation index, пустые
  reason/provenance, partial technical union object и разрешённые representations с
  omitted unresolved object; каждая пустая assertions array blocked-step требует
  свой exact-path blocker;
- automatable expectation без assertion;
- raw secret literal;
- reviewer partial successor на уровне case и вложенных step/expectation, удаление
  или reparenting input/output/assertion/blocker, удаление/rename/reparent capability
  argument/result, stale base digest и изменённый successor `document_id`;
- selected-revision mismatch между automation и trace;
- automation relations со step другого case, expectation соседнего step, assertion
  соседней expectation и symbol чужого file; exact duplicate relation;
- несколько symbols одной цели (AND), missing/skipped/stale current-run evidence и
  duplicate/conflicting terminal evidence;
- одинаковый local `symbol_id` в двух files остаётся двумя required pairs; duplicate
  symbol внутри одного file и evidence без/с чужим `file_id` отклоняются; PASS требует
  две отдельные current-run evidence records, отсутствие любой даёт `NOT_RUNNABLE`;
- manual disposition для step другого case, automatable/blocked step, duplicate и
  missing disposition;
- same-named methods в разных classes;
- mixed automated/manual final verdicts.

Каждый тест сначала должен завершиться ожидаемым RED по отсутствующей возможности,
затем GREEN после минимальной реализации.

### 18.2. Golden-проверки проекций

- canonical JSON bytes и digest: sorted keys, Unicode без normalization, no BOM,
  no terminal LF и отказ на NaN/Infinity;
- один golden bare document проверяет exact bytes/hash; отдельно каждый carrier
  (`parent_sha256`, reviewer base, automation source, trace source, receipt document)
  принимает этот digest и отвергает digest stage envelope;
- byte-stable Markdown в подтверждённом формате;
- 24 CSV headers в точном порядке;
- все 24 mappings в одной полностью заполненной first step row и одной continuation
  row; exact empty/default behavior, четыре Priority mappings, шесть exact custom
  keys и warning для unmapped custom key;
- одна строка на шаг;
- case metadata только в первой строке;
- Unicode, commas, quotes, pipes, backslashes и multiline values;
- пустые optional fields;
- schema rejection `null`/object/NaN/Infinity custom-field values; absent custom key
  остаётся единственным способом получить empty cell;
- безопасные secret labels;
- одинаковый input даёт идентичные bytes и hashes;
- verify-only обнаруживает изменение любого из трёх artifacts;
- частично опубликованный revision bundle восстанавливается повторным запуском;
- formula-leading CSV cells безопасны и детерминированы;
- два синхронно стартующих publisher с одинаковыми payload получают один и тот же
  валидный bundle;
- два publisher с разными JSON или projection bytes не перезаписывают друг друга и
  ни один не возвращает успешный receipt для смешанного bundle;
- отсутствие atomic hard-link support останавливает publication до bundle writes;
- filename-unsafe `document_id` (как минимум uppercase suffix, `:`, `/`, `\\` и
  trailing dot), case-варианты, overlength `document_id` и revision вне диапазона
  отклоняются до probe и bundle writes; два разных ID никогда не сводятся case
  folding или sanitizing к одному пути;
- fixture минимум с двумя различимыми предусловиями подтверждает их исходный
  canonical array order после projection-specific escaping и в Markdown, и в CSV;
- неизвестный CSV profile отклоняется до writes и не может переиспользовать bundle
  path профиля `zephyr-scale-step-row-24-v1`.

### 18.3. TDD для скиллов

Каждый изменяемый skill проверяется отдельно:

1. свежие агенты получают синтетический сценарий с текущим skill — RED baseline;
2. фиксируются реальные нарушения и формулировки, а не ожидаемый ответ;
3. меняется только guidance, необходимый для выявленных failures;
4. тот же сценарий запускается на свежих агентах с новым skill — GREEN;
5. новые обходы закрываются и проверяются повторно.

Для каждого control/guidance варианта выполняется не менее пяти fresh-context
samples; каждый результат читается вручную. Один удачный ответ не считается
доказательством устойчивого поведения.

Контрольные сценарии включают:

- создание заказа, переиспользование `order_id` и подтверждение тремя шагами;
- смешанный automatic/manual кейс;
- недостаток технического контекста под давлением «всё равно сгенерировать»;
- reviewer AUTO_FIX одного кейса в наборе из нескольких;
- давление на reviewer вернуть частичный successor или удалить stable entity через
  AUTO_FIX;
- реализацию нескольких шагов одним test method без потери mappings;
- точный человеческий Markdown без видимой колонки Test Data.

Baseline outputs хранятся как временные eval evidence и не загрязняют skill assets.

### 18.4. Интеграционные gates

- все stage schemas и representative fixtures валидны;
- semantic validator проходит positive suite и отвергает negative suite;
- contract check и doctor проходят;
- pipeline docs перегенерированы из canonical pipeline contract;
- generator -> reviewer accepted path использует исходную ревизию;
- generator -> reviewer AUTO_FIX path использует полный successor в проекциях,
  automation и trace;
- trace покрывает каждое автоматизируемое assertion;
- full diff содержит только согласованные файлы;
- fresh Sol reviewer возвращает `ship`;
- реальный Zephyr import/export round trip проводится отдельно до заявления import
  compatibility.

## 19. Критерии приёмки

Работа считается завершённой только если одновременно выполнено следующее:

1. JSON Schema допускает любое необходимое число последовательных шагов и не имеет
   искусственного maxItems.
2. Semantic validator доказывает корректность ID, порядка, data flow, manual-only и
   assertion ownership.
3. Один selected canonical document является единственным входом downstream skills.
4. Markdown точно соответствует подтверждённой трёхколоночной форме.
5. CSV соответствует наблюдённому Zephyr Scale step-row профилю и не заявляет
   неподтверждённый import round trip.
6. Любой reviewer successor проходит identity-graph preservation: существующие
   stable entities не удалены и не reparented, после чего создаются его собственные
   revision-specific проекции.
7. Automation и trace содержат атомарные parent-chain-valid operation/assertion
   relations с явным file/symbol locator; чужие IDs, duplicate rows и required symbol
   pair без успешного current-run evidence не дают coverage или PASS.
8. Ручной остаток никогда не выдаётся за полный PASS.
9. V2.1 не принимается молча и не обогащается догадками.
10. Программные tests, skill behavior evals, full repository checks и fresh Sol
    review пройдены с фактическими evidence.
11. Bundle receipt получен только после read-back точных JSON, Markdown и CSV bytes;
    candidate и effective revision не перепутаны.

## 20. Остаточные риски

1. Конкретный Zephyr tenant может требовать другой import template или правила
   custom fields. Это закрывается отдельным profile и реальным sandbox round trip.
2. Assertion DSL может оказаться недостаточным для нового протокола. Расширение
   выполняется новым discriminated variant/operator без framework-specific code в
   canonical JSON.
3. Stable IDs требуют передачи предыдущей ревизии при редактировании. Без неё можно
   создать новый документ, но нельзя честно обещать identity continuity.
4. Полностью исключить семантическое расхождение human text и assertion статической
   схемой невозможно; это закрывается structural ownership, reviewer и behavior evals.
