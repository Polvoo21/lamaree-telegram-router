import unittest
from pathlib import Path

import bot


class TildaMessageFormattingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.routes = bot.load_routes(str(Path(__file__).parents[1] / "routes.json"))

    def test_onboard_request_is_translated_and_routes_by_restaurant(self) -> None:
        pairs = [
            ("restoraunt", "La Marée в Санкт-Петербурге (Санкт-Петербург, Суворовский просп., 34)"),
            ("company", "Андрей Хрещенюк"),
            ("date", "24/07/2026"),
            ("time", "22:22"),
            ("place", "Москва"),
            ("contact", "Андрей"),
            ("massage-type", "phone"),
            ("massage-id", "+7 (977) 981-00-98"),
            ("checkbox", "yes"),
            ("tranid", "14706126:8556981243"),
            ("formid", "form2022232801"),
            ("Form Name", "Заявка на бортовой кейтеринг"),
            ("url", "https://example.test/onboard-catering"),
        ]

        display_text, routing_text, transaction_id = bot.payload_to_tilda_message(pairs)

        self.assertIn("Новая заявка: бортовой кейтеринг", display_text)
        self.assertIn("Компания: Андрей Хрещенюк", display_text)
        self.assertIn("Способ связи: Телефон", display_text)
        self.assertIn("Согласие на обработку данных: Да", display_text)
        self.assertIn("Номер заявки: 14706126:8556981243", display_text)
        self.assertIn("Страница: https://example.test/onboard-catering", display_text)
        self.assertNotIn("Request details", display_text)
        self.assertNotIn("massage-type", display_text)
        self.assertEqual(transaction_id, "14706126:8556981243")

        route = bot.find_route(routing_text, self.routes)
        self.assertIsNotNone(route)
        self.assertEqual(route.name, "La Marée — Санкт-Петербург — Бортовое питание")

    def test_restaurant_request_keeps_routing_data_out_of_display_labels(self) -> None:
        pairs = [
            ("restoraunt", "La Marée на Петровке (Москва, ул. Петровка, д.28, стр.1)"),
            ("name_2", "Никита"),
            ("date", "23/07/2026"),
            ("time", "11:11"),
            ("quantity", "25"),
            ("massage-type", "telegram"),
            ("massage-id", "@nikita"),
            ("Textarea", "Тестовая заявка"),
            ("checkbox", "yes"),
            ("tranid", "test:petrovka"),
        ]

        display_text, routing_text, _ = bot.payload_to_tilda_message(pairs)

        self.assertIn("Ресторан: La Marée на Петровке", display_text)
        self.assertIn("Имя: Никита", display_text)
        self.assertIn("Количество гостей: 25", display_text)
        self.assertIn("Способ связи: Telegram", display_text)
        self.assertIn("Комментарий: Тестовая заявка", display_text)
        route = bot.find_route(routing_text, self.routes)
        self.assertIsNotNone(route)
        self.assertEqual(route.name, "La Marée — Петровка — Брони")

    def test_unknown_tilda_field_is_not_lost(self) -> None:
        display_text, _, _ = bot.payload_to_tilda_message([("delivery_window", "12:00-14:00")])

        self.assertIn("Delivery window: 12:00-14:00", display_text)

    def test_all_nine_destinations_match_restaurant_and_form_type(self) -> None:
        route_cases = [
            ("La Marée на Петровке", "", "La Marée — Петровка — Брони"),
            ("La Marée на Малой Грузинской", "", "La Marée — Малая Грузинская — Брони"),
            ("La Marée в Жуковке", "", "La Marée — Жуковка — Брони"),
            ("La Marée на Смоленке", "", "La Marée — Смоленка — Брони"),
            ("La Marée в Санкт-Петербурге", "", "La Marée — Санкт-Петербург — Брони"),
            (
                "La Marée на Петровке",
                "Заявка на бортовой кейтеринг",
                "La Marée — Петровка — Бортовое питание",
            ),
            (
                "La Marée в Жуковке",
                "Заявка на бортовой кейтеринг",
                "La Marée — Жуковка — Бортовое питание",
            ),
            (
                "La Marée в Санкт-Петербурге",
                "Заявка на бортовой кейтеринг",
                "La Marée — Санкт-Петербург — Бортовое питание",
            ),
        ]

        for restaurant, form_name, expected_route_name in route_cases:
            with self.subTest(route=expected_route_name):
                pairs = [("restoraunt", restaurant)]
                if form_name:
                    pairs.append(("Form Name", form_name))
                _, routing_text, _ = bot.payload_to_tilda_message(pairs)
                route = bot.find_route(routing_text, self.routes)
                self.assertIsNotNone(route)
                self.assertEqual(route.name, expected_route_name)

    def test_catering_without_restaurant_routes_to_smolenka(self) -> None:
        _, routing_text, _ = bot.payload_to_tilda_message(
            [("Form Name", "Заявка на кейтеринг")]
        )

        route = bot.find_route(routing_text, self.routes)
        self.assertIsNotNone(route)
        self.assertEqual(route.name, "La Marée — Смоленка — Кейтеринг")

    def test_catering_with_stale_restaurant_value_still_routes_to_smolenka(self) -> None:
        _, routing_text, _ = bot.payload_to_tilda_message(
            [
                ("restoraunt", "La Marée на Петровке"),
                ("Form Name", "Заявка на кейтеринг"),
            ]
        )

        route = bot.find_route(routing_text, self.routes)
        self.assertIsNotNone(route)
        self.assertEqual(route.name, "La Marée — Смоленка — Кейтеринг")

    def test_misspelled_tilda_address_is_translated(self) -> None:
        display_text, _, _ = bot.payload_to_tilda_message([("adress", "Петровка, 28")])

        self.assertIn("Адрес: Петровка, 28", display_text)


if __name__ == "__main__":
    unittest.main()
