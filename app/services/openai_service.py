PROMPT = """
Ты — ассистент службы поддержки. Твоя задача: классифицировать обращение и написать черновик ответа.

Правила:
1. Анализируй только текст обращения. Не выдумывай факты.
2. Если данных недостаточно → ставь confidence=low и escalate=true.
3. Возвращай строгий JSON:

{
  "category": "billing | support | complaint | other",
  "draft_reply": "текст ответа (1–6 предложений)",
  "confidence": "high | medium | low",
  "escalate": true/false
}

Категории:
- billing: вопросы по оплате, счета, чеки
- support: техническая поддержка, помощь с функционалом
- complaint: жалобы, негатив
- other: всё остальное
"""

def classify_ticket(text: str) -> dict:
    """Классификация обращения через текущую LLM (GigaChat или OpenAI)."""
    try:
        from utils.ai_processor import chat_json

        result = chat_json(PROMPT, text)
        
        required_fields = ['category', 'draft_reply', 'confidence', 'escalate']
        for field in required_fields:
            if field not in result:
                raise ValueError(f"Missing field: {field}")
        
        if isinstance(result['escalate'], str):
            result['escalate'] = result['escalate'].lower() == 'true'
        
        return result
        
    except Exception as e:
        print(f"LLM error: {e}")
        return {
            'category': 'other',
            'draft_reply': 'Ваше обращение принято. Менеджер свяжется с вами в ближайшее время.',
            'confidence': 'low',
            'escalate': True
        }
