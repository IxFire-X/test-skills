# NOT_RUNNABLE E2E

Fixture `D:/AI-Projects/subscription-renewal-service` содержит только `target/` и не имеет исходников, project manifest или `.skillsrc` на commit `42549f78b140b26c3d5263d99da3f263706cea89`.

Проверка не изменила fixture и не создала `.skillsrc`. До и после запуска совпали commit, полный список каталогов и отсутствие файлов. Scanner вернул ожидаемый `status=error`: отсутствуют build manifests и target-код. Execution Gate вернул `NOT_RUNNABLE` с конкретной причиной `language_detection`, а не ложный `PASS` или общий `FAIL`.
