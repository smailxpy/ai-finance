import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonWebApp,
    ReplyKeyboardRemove,
    WebAppInfo,
)
from dotenv import load_dotenv

from db import init_db

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def _open_app_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="Open Financial Assistant",
            web_app=WebAppInfo(url=WEBAPP_URL),
        )
    ]])


@dp.startup()
async def on_startup(**_):
    init_db()
    if WEBAPP_URL:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="Open App",
                web_app=WebAppInfo(url=WEBAPP_URL),
            )
        )
        logging.info("Menu button set to Mini App: %s", WEBAPP_URL)
    else:
        logging.warning("WEBAPP_URL not set — menu button not configured")


@dp.message(Command("start"))
async def cmd_start(msg: types.Message) -> None:
    # Remove any leftover reply keyboard from the old version
    await msg.answer(
        "AI Financial Assistant",
        reply_markup=ReplyKeyboardRemove(),
    )
    if WEBAPP_URL:
        await msg.answer(
            "Tap below to open the app, or use the button next to the text field.",
            reply_markup=_open_app_keyboard(),
        )
    else:
        await msg.answer("WEBAPP_URL is not set in .env — cannot open Mini App.")


@dp.message(Command("app"))
async def cmd_app(msg: types.Message) -> None:
    if not WEBAPP_URL:
        await msg.answer("WEBAPP_URL is not configured.")
        return
    await msg.answer("Open the Mini App:", reply_markup=_open_app_keyboard())


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
