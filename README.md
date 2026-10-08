# Поиск  по ИНН


Tavily ищет по ИНН, названию и городу, при необходимости загружает текст страницы. Groq оценивает принадлежность сайта, код проверяет источник и наличие ИНН.

## Установка

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

В `.env` указать `TAVILY_API_KEY` и `LLM_API_KEY` (Groq). Модель задаётся через `LLM_MODEL`, по умолчанию — `openai/gpt-oss-120b`.

## Запуск

```powershell
.\.venv\Scripts\python.exe -m inn_website 7721581040
```

Пример ответа: `{"domain":"dadata.ru"}`. Вместо `7721581040` передать другой ИНН.

JSON на входе, объяснение и сохранение источников:

```powershell
'{"inn":"7721581040"}' | .\.venv\Scripts\python.exe -m inn_website
.\.venv\Scripts\python.exe -m inn_website 7812014560 --explain --trace reports/trace.json
```

Примеры:

- DaData `7721581040` | `dadata.ru` 
- МТС `7740000076` | `mts.ru` 
- МегаФон `7812014560` | `megafon.ru` 
- Яндекс `7736207543` | `yandex.ru` 
- DNS `2540167061` | `dns-shop.ru` 
- Додо `1101140415` | `dodopizza.ru` 

## Проверка

Тесты без сети и ключей:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Проверка на реальных API расходует их лимиты, отчёт сохраняется в `reports/evaluation.json`:

```powershell
.\.venv\Scripts\python.exe -m inn_website.evaluate examples/cases.json
```
