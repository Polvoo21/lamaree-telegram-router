La Marée Telegram Router Bot

Финальная логика:
1. Бот слушает общую группу заявок:
   -1003970343773

2. Для ресторанов ищет поле:
   restoraunt:

3. Для кейтеринга и бортового кейтеринга ищет:
   Form Name:
   и/или ссылку в заявке.

Маршруты:
- La Marée на Петровке -> Петровка
- La Marée на Малой Грузинской -> Малая Грузинская
- La Marée в Жуковке -> Жуковка
- La Marée на Смоленке -> Смоленка
- La Marée в Санкт-Петербурге -> Санкт-Петербург
- Form Name: Заявка на кейтеринг -> Кейтеринг
- Form Name: Заявка на бортовой кейтеринг -> Бортовой Кейтеринг

Запуск локально:
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
notepad .env
python bot.py

Для хостинга:
- Залить bot.py, requirements.txt, routes.json в GitHub.
- Токен и настройки указать в переменных окружения.
- Главный файл: bot.py
- Python: 3.11 или 3.12
- Локация: Нидерланды/Европа.

Важно:
.env не заливать в публичный GitHub.
