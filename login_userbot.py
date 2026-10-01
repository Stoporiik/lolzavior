# -*- coding: utf-8 -*-
"""
Одноразовый вход аккаунта-наблюдателя (юзербота) для NFT-слежки.

Запусти ОДИН раз в терминале:
    .venv\\Scripts\\python.exe login_userbot.py

Твои данные уже вписаны ниже (API_ID / API_HASH / PHONE / SUPPORT).
При запуске скрипт спросит ТОЛЬКО код из Telegram и (если включена) пароль 2FA —
их заранее задать нельзя, код приходит в момент входа.

ЧТО ПОЛУЧИШЬ ПОСЛЕ ВХОДА (запусти ДОМА):
  1) на экране — СТРОКА-СЕССИЯ (длинный код). Её можно вставить в bot.py в
     USERBOT_SESSION, если хочешь без файлов.
  2) рядом появится файл userbot_session.json — в нём уже всё (api_id, api_hash,
     сессия, саппорт).

КАК ЗАЛИТЬ НА ХОСТИНГ (вход там делать НЕ нужно, код вводить НЕ нужно):
  — просто положи userbot_session.json рядом с bot.py на хостинге, ИЛИ
  — вставь строку-сессию в USERBOT_SESSION внутри bot.py.
Бот сам подхватит сессию и подключится.

Где взять API ID / API HASH: https://my.telegram.org → API development tools.
ВАЖНО: строка-сессия и userbot_session.json = ПОЛНЫЙ доступ к аккаунту.
Никому их не пересылай; на хостинге держи в закрытом доступе.
"""
import asyncio
import json
import os

from telethon import TelegramClient
from telethon.sessions import StringSession

import bot as b  # переиспользуем DB-настройки бота (init_db / set_setting / DB_PATH)

# ===========================================================================
# ТВОИ ДАННЫЕ (можешь поменять прямо здесь, в кавычках):
# ===========================================================================
API_ID = "заполнить"
API_HASH = "заполнить"
PHONE = "заполнить"      # номер аккаунта-наблюдателя, с которого входим
SUPPORT = "заполнить"            # кому продавец должен отдавать NFT (саппорт)
# ===========================================================================


async def main():
    print("=" * 56)
    print(" Вход аккаунта-наблюдателя (юзербот) для NFT-слежки")
    print("=" * 56)
    api_id = API_ID.strip()
    api_hash = API_HASH.strip()
    phone = PHONE.strip()
    support = (SUPPORT or b.SUPPORT_USERNAME_DEFAULT).strip().lstrip("@")

    if not (api_id.isdigit() and api_hash and phone):
        raise SystemExit("Проверь API_ID (только цифры), API_HASH и PHONE вверху файла.")
    print(f"API ID: {api_id} | телефон: {phone} | саппорт: @{support}")

    client = TelegramClient(StringSession(), int(api_id), api_hash)
    # client.start спросит код из Telegram, а при включённой 2FA — пароль.
    await client.start(phone=phone)

    me = await client.get_me()
    session_str = client.session.save()
    await client.disconnect()

    # 1) сохраняем в bot.db (чтобы заработало сразу на этой машине)
    b.init_db()
    b.set_setting("userbot_api_id", api_id)
    b.set_setting("userbot_api_hash", api_hash)
    b.set_setting("userbot_session", session_str)
    b.set_setting("support_username", support)

    # 2) пишем переносимый файл userbot_session.json (его можно залить на хостинг)
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "userbot_session.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"api_id": api_id, "api_hash": api_hash,
                   "session": session_str, "support": support}, f, ensure_ascii=False, indent=2)

    uname = f"@{me.username}" if getattr(me, "username", None) else "(без username)"
    print()
    print("✅ Готово! Вошли как:", me.first_name, uname, "id =", me.id)
    print("🛟 Саппорт для проверки: @" + support)
    print("💾 Сохранено в bot.db и в файл:", out_path)
    print()
    print("=" * 56)
    print(" СТРОКА-СЕССИЯ (для USERBOT_SESSION в bot.py, если без файла):")
    print("=" * 56)
    print(session_str)
    print("=" * 56)
    print("Скопируй строку выше ИЛИ просто залей userbot_session.json на хостинг.")
    print("Запуск локально:  .venv\\Scripts\\python.exe bot.py")


if __name__ == "__main__":
    asyncio.run(main())
