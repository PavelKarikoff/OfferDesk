# Known issues

## Прокси OpenAI недоступен (2026-09-28)

**Симптом:** `OPENAI_PROXY=http://193.233.174.4:8888` →
`openai.APIConnectionError: Connection reset by peer`.

**Диагностика:**
- NL-Proxy (5.129.213.88) отвечает по SSH, но tinyproxy не установлен,
  порт 8888 никто не слушает;
- документированный 193.233.174.4:22 не отвечает (SSH timeout);
- SSH-туннель поднимается, но за ним нет tinyproxy.

**Влияние:** LLM-прогон golden set невозможен с локальной машины.
Пайплайн работает через regex-fallback (проверено: 53.3% exact etalon_score).

**План:** поднять tinyproxy на NL-Proxy или направить OPENAI_PROXY
на работающий прокси. До этого LLM-метрики в отчёте — гипотеза.

## transcript_parser_local теряет дробную часть бюджета

«6.5 млн» → «5 млн руб». Не блокер: fallback даёт грубую оценку,
точность — задача LLM-слоя.
