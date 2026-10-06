# Аналитика: StudentController (независимый прогон run2)

> Источник: исходный код `src/main/java/net/javaguides/springboot/controller/StudentController.java`,
> `src/main/java/net/javaguides/springboot/bean/Student.java`,
> `src/main/java/net/javaguides/springboot/controller/HelloWorldController.java`,
> `pom.xml` (Spring Boot 3.4.2, Java 17).

## 1. Общее описание

Проект `springboot-rest-api` — демонстрационное REST API на Spring Boot. Модуль `StudentController`
предоставляет CRUD-подобные эндпоинты для ресурса `Student` (id, firstName, lastName),
а `HelloWorldController` — простой GET-эндпоинт.

Стек тестирования: `spring-boot-starter-test` (JUnit 5, MockMvc, AssertJ). Сборка — Maven (`mvn test`).

## 2. Модель данных

`Student` (`net.javaguides.springboot.bean.Student`):

| Поле | Тип | JSON |
|---|---|---|
| id | int | `"id"` |
| firstName | String | `"firstName"` |
| lastName | String | `"lastName"` |

Точный порядок полей JSON определяется сериализацией Jackson (по getter-методам):
`id`, `firstName`, `lastName`.

## 3. Перечень эндпоинтов и контракты API

### 3.1. GET `/student`
- Описание: возвращает один объект Student с фиксированными значениями.
- Параметры: нет.
- Ответ: `200 OK`, `Content-Type: application/json`.
- Тело: `{"id": 1, "firstName": "Ramseh", "lastName": "Mishra"}`.
- Примечание: в исходном коде firstName = `"Ramseh"` (с опечаткой), это важно для точной проверки.

### 3.2. GET `/students`
- Описание: возвращает список из 4 студентов.
- Параметры: нет.
- Ответ: `200 OK`, JSON-массив из 4 элементов.
- Тело (детерминированный порядок):
  1. `{"id": 1, "firstName": "Ramesh", "lastName": "Mishra"}`
  2. `{"id": 2, "firstName": "Umesh", "lastName": "Mishra"}`
  3. `{"id": 3, "firstName": "Ram", "lastName": "Mishra"}`
  4. `{"id": 4, "firstName": "Sanjay", "lastName": "Mishra"}`

### 3.3. GET `/students/{id}` (Path Variable)
- Описание: возвращает Student по идентификатору из пути.
- Параметры пути: `id` (int).
- Ответ: `200 OK`, тело `{"id": <id>, "firstName": "Ramsesh", "lastName": "Mishra"}`.
- Примечание: метод не проверяет существование — всегда возвращает объект с переданным id.

### 3.4. GET `/students/query?id={id}` (Request Param)
- Описание: возвращает Student по query-параметру `id`.
- Query-параметры: `id` (int, обязательный).
- Ответ: `200 OK`, тело `{"id": <id>, "firstName": "Ramesh", "lastName": "Mishra"}`.
- Ошибка: отсутствие параметра `id` → `400 Bad Request` (Spring).

### 3.5. POST `/students/create`
- Описание: принимает Student в теле запроса, логирует поля и возвращает его же.
- Тело запроса: `application/json` `{"id": int, "firstName": string, "lastName": string}`.
- Ответ: `201 Created` (аннотация `@ResponseStatus(HttpStatus.CREATED)`), тело — эхо запроса.
- Особенность: частично заполненный JSON (например, без lastName) даст `null` в соответствующем поле ответа.

### 3.6. PUT `/students/{id}/update`
- Описание: принимает Student в теле и id в пути, логирует имя/фамилию, возвращает тело.
- Параметры пути: `id` (int).
- Тело запроса: `application/json` Student.
- Ответ: `200 OK`, тело — эхо запроса.
- Примечание: id из пути НЕ подставляется в возвращаемый объект — возвращается тело как есть.

### 3.7. DELETE `/students/{id}/delete`
- Описание: логирует id и возвращает сообщение об удалении.
- Параметры пути: `id` (int).
- Ответ: `200 OK`, текст `Student Successfully Deleted!`.

### 3.8. GET `/hello-world`
- Описание: возвращает строку приветствия.
- Ответ: `200 OK`, текст `Hello World!`.

## 4. Критерии приёмки (Acceptance Criteria)

- AC-1: GET `/student` возвращает 200 и JSON `{"id":1,"firstName":"Ramseh","lastName":"Mishra"}`.
- AC-2: GET `/students` возвращает 200 и массив из 4 объектов с идентификаторами 1..4.
- AC-3: GET `/students/5` возвращает 200 и объект с `id=5`, firstName `Ramsesh`.
- AC-4: GET `/students/query?id=7` возвращает 200 и объект с `id=7`, firstName `Ramesh`.
- AC-5: POST `/students/create` с валидным JSON возвращает `201 Created` и эхо запроса.
- AC-6: PUT `/students/10/update` с валидным JSON возвращает `200 OK` и эхо тела запроса.
- AC-7: DELETE `/students/3/delete` возвращает `200 OK` и текст `Student Successfully Deleted!`.
- AC-8: GET `/hello-world` возвращает `200 OK` и текст `Hello World!`.

## 5. Ограничения и риски

- Эндпоинты не используют БД и не хранят состояние: все ответы детерминированы кодом.
- Опечатка `"Ramseh"` в GET `/student` — намеренно фиксируется как ожидаемое значение, чтобы тест проверял фактическое поведение.
- Метод PUT не подставляет path-variable в объект — проверять именно эхо тела.
- Приложение стартует на стандартном порту 8080 (для MockMvc порт не требуется).