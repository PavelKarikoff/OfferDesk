PARSER_PROMPT = """
Ты — ассистент отдела продаж строительной компании. Твоя задача — проанализировать протокол телефонного разговора и извлечь структурированные данные.

Извлеки следующие поля из текста:
1. client_name — имя клиента
2. client_phone — телефон (если есть, иначе null)
3. client_email — email (если есть, иначе null)
4. client_telegram — Telegram (если есть, иначе null)
5. budget — бюджет (сумма, например "7-8 млн руб.")
6. area — площадь дома (например "120-140 м2")
7. material — материал стен (газобетон, кирпич, брус, если указан)
8. timeline — сроки старта (например "август 2026")
9. work_scope — объём работ (что нужно от компании)
10. funding_source — источник финансирования (свои/ипотека/маткапитал)
11. status — статус сделки ("new", если нет других данных)

Верни ТОЛЬКО JSON в формате:
{
  "client_name": "...",
  "client_phone": "...",
  "client_email": "...",
  "client_telegram": "...",
  "budget": "...",
  "area": "...",
  "material": "...",
  "timeline": "...",
  "work_scope": "...",
  "funding_source": "...",
  "status": "new"
}

Если поле не найдено, поставь null.
"""

def parse_transcript(text: str) -> dict:
    """
    Парсит транскрибацию через текущую LLM (GigaChat или OpenAI).
    """
    try:
        import sys
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from utils.ai_processor import chat_json

        return chat_json(PARSER_PROMPT, text[:4000])
        
    except Exception as e:
        print(f"Ошибка парсинга: {e}")
        return {
            "client_name": None,
            "client_phone": None,
            "client_email": None,
            "client_telegram": None,
            "budget": None,
            "area": None,
            "material": None,
            "timeline": None,
            "work_scope": None,
            "funding_source": None,
            "status": "new"
        }
