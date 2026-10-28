"""Обезличивание имени, фамилии, телефона и почты перед отправкой в LLM."""

from __future__ import annotations

import csv
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.ai_processor import chat_json  # noqa: E402
from utils.anonymizer import anonymize, restore, restore_data  # noqa: E402

_TRANSCRIPT = (
    "Здравствуйте, меня зовут Сергей Петров. "
    "Телефон +7 (916) 123-45-67, почта sergey.petrov@mail.ru. "
    "Участок есть, 12 соток в Московской области. "
    "Хочу дом из газобетона, 150 квадратов. Бюджет около 9 миллионов."
)


class AnonymizerTests(unittest.TestCase):
    def test_masks_name_phone_email_and_keeps_object_facts(self):
        masked, mapping = anonymize(_TRANSCRIPT)

        self.assertNotIn("Сергей", masked)
        self.assertNotIn("Петров", masked)
        self.assertNotIn("916", masked)
        self.assertNotIn("sergey.petrov@mail.ru", masked)
        self.assertIn("[ИМЯ_1]", masked)
        self.assertIn("[ТЕЛЕФОН_1]", masked)
        self.assertIn("[ПОЧТА_1]", masked)
        self.assertIn("Московской области", masked)
        self.assertIn("12 соток", masked)
        self.assertIn("газобетона", masked)
        self.assertIn("9 миллионов", masked)
        self.assertEqual(restore(masked, mapping), _TRANSCRIPT)

    def test_masks_telegram_nick_and_keeps_email(self):
        text = (
            "Телеграм: sergey_petrov. "
            "Дублирую @sergey_petrov и https://t.me/sergey_petrov. "
            "Почта sergey.petrov@mail.ru."
        )
        masked, mapping = anonymize(text)
        self.assertNotIn("sergey_petrov", masked.casefold())
        self.assertNotIn("sergey.petrov@mail.ru", masked)
        self.assertEqual(masked.count("[НИК_1]"), 3)
        self.assertNotIn("[НИК_2]", masked)
        self.assertIn("[ПОЧТА_1]", masked)
        self.assertIn("@[НИК_1]", masked)
        self.assertIn("t.me/[НИК_1]", masked)
        self.assertEqual(restore(masked, mapping), text)
        self.assertEqual(mapping["[НИК_1]"], "sergey_petrov")

    def test_same_phone_is_one_token(self):
        text = "Телефон +7 916 123-45-67, потом ещё раз 8 (916) 123-45-67."
        masked, mapping = anonymize(text)
        self.assertEqual(masked.count("[ТЕЛЕФОН_1]"), 2)
        self.assertNotIn("[ТЕЛЕФОН_2]", masked)
        self.assertEqual(len(mapping), 1)

    def test_placeholder_and_sentence_after_label_stay(self):
        text = (
            "Клиент: Заказчик\n"
            "Клиент интересуется энергоэффективными решениями.\n"
            "Менеджер: Светлана"
        )
        masked, mapping = anonymize(text)
        self.assertIn("Клиент: Заказчик", masked)
        self.assertIn("Клиент интересуется энергоэффективными решениями.", masked)
        self.assertNotIn("Светлана", masked)
        self.assertIn("[ИМЯ_1]", masked)
        self.assertEqual(set(mapping), {"[ИМЯ_1]"})

    def test_restore_data_walks_nested_json(self):
        _, mapping = anonymize("Меня зовут Анна. Почта anna@example.com")
        restored = restore_data(
            {"client": {"name": "[ИМЯ_1]"}, "notes": ["писать на [ПОЧТА_1]"]},
            mapping,
        )
        self.assertEqual(restored["client"]["name"], "Анна")
        self.assertEqual(restored["notes"], ["писать на anna@example.com"])

    def test_log_has_tokens_without_source_values(self):
        from utils.anonymization_log import write_anonymization_log

        masked, mapping = anonymize(_TRANSCRIPT)
        path = Path(self.id() + ".csv")
        try:
            with patch("utils.anonymization_log.LOG_PATH", path):
                write_anonymization_log(masked, mapping)
            with path.open(encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
        finally:
            path.unlink(missing_ok=True)
        self.assertEqual(len(rows), 1)
        blob = rows[0]["anonymized_text"] + rows[0]["tokens"]
        self.assertNotIn("Сергей", blob)
        self.assertNotIn("sergey.petrov@mail.ru", blob)
        self.assertNotIn("916", blob)
        self.assertIn("ИМЯ", rows[0]["tokens"])
        self.assertIn("ТЕЛЕФОН", rows[0]["tokens"])
        self.assertIn("ПОЧТА", rows[0]["tokens"])


class ChatJsonAnonymizationTests(unittest.TestCase):
    def test_provider_sees_tokens_caller_sees_person(self):
        masked, mapping = anonymize(_TRANSCRIPT)
        model_json = json.dumps(
            {
                "client_name": "[ИМЯ_1]",
                "phone": "[ТЕЛЕФОН_1]",
                "email": "[ПОЧТА_1]",
                "plot": "12 соток в Московской области",
            },
            ensure_ascii=False,
        )
        with (
            patch("utils.ai_processor._complete", return_value=model_json) as chat,
            patch("utils.ai_processor._remember_anonymization") as remember,
        ):
            result = chat_json("system", _TRANSCRIPT)

        sent = chat.call_args.args[1]
        self.assertNotIn("Сергей", sent)
        self.assertNotIn("916", sent)
        self.assertNotIn("sergey.petrov@mail.ru", sent)
        self.assertIn("[ИМЯ_1]", sent)
        self.assertIn("[ТЕЛЕФОН_1]", sent)
        self.assertIn("Московской области", sent)
        self.assertIn("Переноси токены", sent)
        self.assertEqual(chat.call_args.args[0], "system")
        remember.assert_called_once()
        self.assertEqual(result["client_name"], "Сергей Петров")
        self.assertEqual(result["phone"], mapping["[ТЕЛЕФОН_1]"])
        self.assertEqual(result["email"], "sergey.petrov@mail.ru")
        self.assertIn("Московской области", result["plot"])

    def test_plain_text_is_sent_as_is(self):
        prompt = "Площадь 150 м², бюджет 9 млн, участок в Московской области."
        with (
            patch("utils.ai_processor._complete", return_value='{"area": "150"}') as chat,
            patch("utils.ai_processor._remember_anonymization") as remember,
        ):
            result = chat_json("system", prompt)

        self.assertEqual(chat.call_args.args[1], prompt)
        remember.assert_not_called()
        self.assertEqual(result, {"area": "150"})

    def test_openai_path_also_masks_user_text(self):
        message = MagicMock()
        message.content = '{"client_name": "[ИМЯ_1]"}'
        choice = MagicMock()
        choice.message = message
        response = MagicMock()
        response.choices = [choice]
        client = MagicMock()
        client.chat.completions.create.return_value = response
        with (
            patch("utils.ai_processor.llm_provider", return_value="openai"),
            patch("utils.ai_processor.get_openai_client", return_value=client),
            patch("utils.ai_processor._remember_anonymization"),
        ):
            result = chat_json("system", "Меня зовут Анна Смирнова.")

        sent = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        self.assertNotIn("Анна", sent)
        self.assertNotIn("Смирнова", sent)
        self.assertEqual(
            client.chat.completions.create.call_args.kwargs["messages"][0]["content"],
            "system",
        )
        self.assertEqual(result["client_name"], "Анна Смирнова")


if __name__ == "__main__":
    unittest.main()
