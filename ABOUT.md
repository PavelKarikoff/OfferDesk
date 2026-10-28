# About

**OfferDesk v1.3.0** ([репозиторий](https://github.com/PavelKarikoff/OfferDesk))

**ИЖС-квалификатор лидов:** заявка с сайта / Авито / Циан / звонка → JSON → квалификация A/B/C → эскалация → сделка в CRM → задача менеджеру. Рабочее место — веб-CRM «Дом-Мастер» (не чат-бот): эталон, PDF-КП, email. Telegram — канал доставки, не интерфейс продукта.

Текстовые запросы идут в **GigaChat** или **OpenAI через ProxyAPI**. Имя, фамилия, телефон, почта и ник Telegram заменяются токенами до отправки провайдеру и возвращаются в ответ сразу после модели. Заявка, транскрибация и файл КП выгружаются в **Bitrix24** по входящему вебхуку. «Дом-Мастер» — первый заказчик и бренд в КП. amoCRM и WhatsApp — roadmap.

---

**Для поля About на GitHub (коротко, ≤350 символов):**

```
OfferDesk v1.3.0 — квалификатор лидов ИЖС: заявка → JSON → A/B/C → CRM «Дом-Мастер» → PDF-КП. Тексты: GigaChat или OpenAI через ProxyAPI, персональные данные обезличиваются. Выгрузка в Bitrix24. Flask · Docker.
```

**Topics (теги):** `lead-qualification` · `llm` · `gigachat` · `crm` · `bitrix24` · `sales` · `construction` · `openai` · `flask` · `docker` · `pdf` · `weasyprint`
