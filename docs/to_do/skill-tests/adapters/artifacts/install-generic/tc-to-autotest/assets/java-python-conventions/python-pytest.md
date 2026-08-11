# Необязательные соглашения Python/pytest для нового workspace

Использовать только когда пользователь выбрал Python/pytest и в проекте нет существующего test pattern.

- Pytest, стабильное имя/marker/parameter ID с `TC-*`.
- Выбрать sync или async client согласно production interface; не навязывать `httpx`.
- Создавать fixture только когда она нужна нескольким тестам и её данные не являются секретами.
- Передавать environment/auth через подтверждённый runtime mechanism.
- Проверять каждый oracle отдельным читаемым assertion с диагностическим контекстом.
- Создавать только test companions и не менять production/config/dependencies.
