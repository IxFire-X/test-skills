# Необязательные соглашения Java/JUnit 5 для нового workspace

Использовать только когда пользователь выбрал Java/JUnit 5 и в проекте нет существующего test pattern.

- JUnit 5, один стабильный display name с `TC-*` на исполнимый сценарий.
- Выбрать один client (`MockMvc`, RestAssured, `WebTestClient`) явно; не смешивать их без причины.
- Передавать URL, auth и environment через подтверждённую runtime-конфигурацию.
- Не создавать `BaseApiTest`, DTO, WireMock или Testcontainers, если они не нужны входным кейсам.
- Отражать каждый oracle конкретным assertion; не использовать `Thread.sleep` и shared mutable state.
- Создавать только test companions и не менять production/config/dependencies.
