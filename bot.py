"""Telegram-бот предзаписи на нутрициологический разбор.

Анкета идёт по шагам через FSM (состояния в памяти процесса).
Свободные слоты читаются через GET Google Apps Script Web App,
готовая строка отправляется POST на тот же URL:
GOOGLE_WEBAPP_URL.
"""

from __future__ import annotations

import asyncio
import logging
import os

import aiohttp
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from dotenv import load_dotenv

# .env подхватывается локально. Уже заданные переменные окружения
# (например, из docker run --env-file) не перезаписываются.
load_dotenv()

logger = logging.getLogger(__name__)

# Текст /start — дословно, без дополнений.
START_TEXT = (
    "Здравствуйте! Я виртуальный помощник Арюны. Здесь вы можете заполнить "
    "анкету предзаписи на нутрициологический разбор питания, анализов и "
    "образа жизни."
)

STEP_COMPLAINTS = (
    "Кратко опишите, что вас беспокоит (симптомы, лишний вес, проблемы с ЖКТ)?"
)
STEP_ANALYSES = (
    "Делали ли вы недавно УЗИ брюшной полости или сдавали анализы крови?"
)
STEP_TIME = "Выберите удобное время для онлайн-консультации:"
STEP_NAME = "Напишите ваши Фамилию и Имя:"
STEP_PHONE = "Нажмите кнопку ниже, чтобы оставить контактный номер телефона:"
NO_SLOTS_TEXT = "К сожалению, сейчас нет свободных окошек. 😔"
SLOT_TAKEN_TEXT = (
    "Это время уже занято. Пожалуйста, выберите другое удобное время:"
)
PHONE_BUTTON = "📱 Отправить мой номер"

# Подписи кнопок — те же строки попадают в таблицу.
ANALYSES_OPTIONS = (
    "Да, есть свежие",
    "Нет, ничего не сдавал",
    "Сдавал давно",
)

DATA_COMPLAINTS = "complaints"
DATA_ANALYSES = "analyses"
DATA_SLOT = "slot_time"
DATA_NAME = "full_name"


class AppointmentForm(StatesGroup):
    """Шаги анкеты. Пока состояние установлено, бот ждёт ответ именно этого шага."""

    complaints = State()
    analyses = State()
    slot_time = State()
    full_name = State()
    phone = State()


# Роутеры обрабатываются в порядке подключения. Команды идут первыми,
# чтобы /start и /cancel срабатывали на любом шаге и не записывались как ответ.
commands_router = Router(name="commands")
form_router = Router(name="form")
fallback_router = Router(name="fallback")


def env_value(name: str, default: str = "") -> str:
    """Читает переменную в момент вызова, а не только при импорте модуля."""
    return os.getenv(name, default).strip()


async def fetch_available_slots() -> list[str]:
    """GET свободных слотов из листа «Расписание» через Web App.

    Ожидает JSON {status, slots}. Любая ошибка сети/парсинга → [].
    """
    webapp_url = env_value("GOOGLE_WEBAPP_URL")
    if not webapp_url:
        logger.error("GOOGLE_WEBAPP_URL пустой, слоты не загружены")
        return []

    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(webapp_url) as response:
                response.raise_for_status()
                body = await response.json(content_type=None)
    except Exception:
        logger.exception("Не удалось получить слоты через Google Web App")
        return []

    if not isinstance(body, dict) or body.get("status") != "success":
        logger.warning("Web App вернул неуспешный ответ на GET слотов: %r", body)
        return []

    raw_slots = body.get("slots")
    if not isinstance(raw_slots, list):
        logger.warning("Поле slots не список: %r", raw_slots)
        return []

    slots: list[str] = []
    for item in raw_slots:
        label = str(item).strip()
        if label:
            slots.append(label)
    return slots


async def append_appointment(
    slot_time: str,
    full_name: str,
    phone: str,
    complaints: str,
    analyses: str,
    username: str,
) -> None:
    """Отправляет одну запись в Google Apps Script Web App.

    Payload: slot_time, full_name, phone, complaints, analyses, username.
    Исключение не глотаем — шаг телефона должен остаться, чтобы человек повторил номер.
    """
    webapp_url = env_value("GOOGLE_WEBAPP_URL")
    payload = {
        "slot_time": slot_time,
        "full_name": full_name,
        "phone": phone,
        "complaints": complaints,
        "analyses": analyses,
        "username": username,
    }
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(webapp_url, json=payload) as response:
            response.raise_for_status()
            body = await response.json(content_type=None)

    if not isinstance(body, dict) or body.get("status") != "success":
        raise RuntimeError(f"Web App вернул неуспешный ответ: {body!r}")


def admin_notification(
    full_name: str,
    slot_time: str,
    complaints: str,
    phone: str,
) -> str:
    return (
        "🔔 Новая предзапись!\n"
        f"Имя: {full_name}\n"
        f"Время: {slot_time}\n"
        f"Жалобы: {complaints}\n"
        f"Контакт: {phone}"
    )


async def notify_admin(
    bot: Bot,
    full_name: str,
    slot_time: str,
    complaints: str,
    phone: str,
) -> None:
    """Пишет админу только после успешной строки в таблице.

    Пустой ADMIN_ID — это не ошибка пациента: в лог уходит предупреждение,
    анкета для человека уже считается сохранённой. Сбой отправки сюда тоже
    не отменяет запись и не возвращает человека на шаг телефона.
    """
    admin_id = env_value("ADMIN_ID")
    if not admin_id:
        logger.warning(
            "ADMIN_ID пустой, уведомление о предзаписи не отправлено"
        )
        return

    try:
        chat_id = int(admin_id)
    except ValueError:
        logger.warning(
            "ADMIN_ID задан, но это не числовой id чата; уведомление пропущено"
        )
        return

    try:
        await bot.send_message(
            chat_id,
            admin_notification(full_name, slot_time, complaints, phone),
        )
    except Exception:
        logger.exception("Не удалось отправить уведомление администратору")


def start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Заполнить анкету и выбрать время",
                    callback_data="start_form",
                )
            ]
        ]
    )


def analyses_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=option)] for option in ANALYSES_OPTIONS],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def slots_keyboard(slots: list[str]) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    for slot in slots:
        builder.button(text=slot)
    builder.adjust(1)
    return builder.as_markup(resize_keyboard=True, one_time_keyboard=True)


def phone_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=PHONE_BUTTON, request_contact=True)]
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def telegram_username(message: Message) -> str:
    """@username либо «нет», если у аккаунта нет публичного имени."""
    user = message.from_user
    if user is not None and user.username:
        return f"@{user.username}"
    return "нет"


async def hide_reply_keyboard(message: Message) -> None:
    """Снимает reply-клавиатуру, не подменяя inline-кнопки следующего сообщения.

    У сообщения Telegram может быть только одна разметка. Поэтому сначала
    уходит служебное сообщение со снятием клавиатуры, затем оно удаляется.
    """
    service = await message.answer("·", reply_markup=ReplyKeyboardRemove())
    try:
        await service.delete()
    except Exception:
        logger.debug("Не удалось удалить служебное сообщение", exc_info=True)


async def send_to_chat(callback: CallbackQuery, text: str, **kwargs: object) -> None:
    """Отвечает в тот же чат и из обычного сообщения, и из inline-кнопки."""
    if isinstance(callback.message, Message):
        await callback.message.answer(text, **kwargs)
        return
    if callback.bot is None or callback.from_user is None:
        logger.error("Некуда отправить ответ на callback %s", callback.data)
        return
    await callback.bot.send_message(callback.from_user.id, text, **kwargs)


async def offer_slot_selection(message: Message, state: FSMContext) -> None:
    """Показывает актуальные свободные слоты или завершает анкету, если их нет."""
    slots = await fetch_available_slots()
    if not slots:
        await state.clear()
        await message.answer(NO_SLOTS_TEXT, reply_markup=ReplyKeyboardRemove())
        return

    await state.set_state(AppointmentForm.slot_time)
    await message.answer(STEP_TIME, reply_markup=slots_keyboard(slots))


@commands_router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    """Показывает приветствие и сбрасывает незаконченную анкету.

    Иначе ответы прошлого прохода могли бы смешаться с новым.
    """
    await state.clear()
    await hide_reply_keyboard(message)
    await message.answer(START_TEXT, reply_markup=start_keyboard())


@commands_router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    """Полностью сбрасывает анкету, на каком бы шаге человек ни остановился."""
    await state.clear()
    await message.answer(
        "Анкета сброшена. Чтобы заполнить её заново, отправьте /start.",
        reply_markup=ReplyKeyboardRemove(),
    )


@commands_router.callback_query(F.data == "start_form")
async def start_form(callback: CallbackQuery, state: FSMContext) -> None:
    """Кнопка под приветствием открывает шаг 1 — жалобы."""
    await state.clear()
    await state.set_state(AppointmentForm.complaints)
    await callback.answer()
    if isinstance(callback.message, Message):
        await hide_reply_keyboard(callback.message)
    await send_to_chat(callback, STEP_COMPLAINTS)


@form_router.message(AppointmentForm.complaints, F.text)
async def process_complaints(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    # Команды сюда не записываем: /start и /cancel забирает другой роутер,
    # а неизвестная команда не должна становиться текстом жалоб.
    if not text or text.startswith("/"):
        await message.answer(
            "Пожалуйста, опишите жалобы обычным текстовым сообщением."
        )
        return

    await state.update_data({DATA_COMPLAINTS: text})
    await state.set_state(AppointmentForm.analyses)
    await message.answer(STEP_ANALYSES, reply_markup=analyses_keyboard())


@form_router.message(AppointmentForm.complaints)
async def process_complaints_invalid(message: Message) -> None:
    """Фото, стикер и прочие типы на этом шаге не принимаем. Состояние то же."""
    await message.answer(
        "Пожалуйста, опишите жалобы обычным текстовым сообщением."
    )


@form_router.message(AppointmentForm.analyses, F.text.in_(ANALYSES_OPTIONS))
async def process_analyses(message: Message, state: FSMContext) -> None:
    await state.update_data({DATA_ANALYSES: message.text})
    await offer_slot_selection(message, state)


@form_router.message(AppointmentForm.analyses)
async def process_analyses_invalid(message: Message) -> None:
    await message.answer(
        "Пожалуйста, выберите один из вариантов на клавиатуре: "
        "«Да, есть свежие», «Нет, ничего не сдавал» или «Сдавал давно»."
    )


@form_router.message(AppointmentForm.slot_time, F.text)
async def process_slot(message: Message, state: FSMContext) -> None:
    slot_time = (message.text or "").strip()
    if not slot_time or slot_time.startswith("/"):
        await message.answer(
            "Пожалуйста, выберите время одной из кнопок на клавиатуре."
        )
        return

    await state.update_data({DATA_SLOT: slot_time})
    await state.set_state(AppointmentForm.full_name)
    await message.answer(STEP_NAME, reply_markup=ReplyKeyboardRemove())


@form_router.message(AppointmentForm.slot_time)
async def process_slot_invalid(message: Message, state: FSMContext) -> None:
    slots = await fetch_available_slots()
    if not slots:
        await state.clear()
        await message.answer(NO_SLOTS_TEXT, reply_markup=ReplyKeyboardRemove())
        return
    await message.answer(
        "Пожалуйста, выберите время одной из кнопок на клавиатуре.",
        reply_markup=slots_keyboard(slots),
    )


@form_router.message(AppointmentForm.full_name, F.text)
async def process_full_name(message: Message, state: FSMContext) -> None:
    full_name = (message.text or "").strip()
    if not full_name or full_name.startswith("/"):
        await message.answer("Пожалуйста, напишите фамилию и имя текстом.")
        return

    await state.update_data({DATA_NAME: full_name})
    await state.set_state(AppointmentForm.phone)
    await message.answer(STEP_PHONE, reply_markup=phone_keyboard())


@form_router.message(AppointmentForm.full_name)
async def process_full_name_invalid(message: Message) -> None:
    await message.answer("Пожалуйста, напишите фамилию и имя текстом.")


@form_router.message(AppointmentForm.phone, F.contact)
async def process_phone_contact(message: Message, state: FSMContext) -> None:
    contact = message.contact
    phone = "" if contact is None else (contact.phone_number or "").strip()
    if not phone:
        await message.answer(
            "Не вижу номер в контакте. Напишите его текстом или нажмите "
            f"«{PHONE_BUTTON}» ещё раз."
        )
        return
    await finish_appointment(message, state, phone)


@form_router.message(AppointmentForm.phone, F.text)
async def process_phone_text(message: Message, state: FSMContext) -> None:
    phone = (message.text or "").strip()
    # Нажатие подписи кнопки без передачи контакта номером не считается.
    if not phone or phone.startswith("/") or phone == PHONE_BUTTON:
        await message.answer(
            "Пожалуйста, отправьте номер телефона текстом или нажмите "
            f"«{PHONE_BUTTON}»."
        )
        return
    await finish_appointment(message, state, phone)


@form_router.message(AppointmentForm.phone)
async def process_phone_invalid(message: Message) -> None:
    await message.answer(
        "Пожалуйста, отправьте номер телефона текстом или нажмите "
        f"«{PHONE_BUTTON}».",
        reply_markup=phone_keyboard(),
    )


async def finish_appointment(message: Message, state: FSMContext, phone: str) -> None:
    """Пишет строку через Web App и только потом благодарит человека.

    Перед записью ещё раз проверяем, что слот свободен.
    Ошибка Web App не очищает шаг «телефон»: номер можно отправить ещё раз.
    Уведомление админу уходит лишь когда запись уже сохранена.
    """
    data = await state.get_data()
    required = (DATA_SLOT, DATA_NAME, DATA_COMPLAINTS, DATA_ANALYSES)
    if any(not data.get(key) for key in required):
        await message.answer(
            "Не хватает ответов анкеты. Отправьте /start и заполните её ещё раз."
        )
        return

    slot_time = str(data[DATA_SLOT])
    full_name = str(data[DATA_NAME])
    complaints = str(data[DATA_COMPLAINTS])

    free_slots = await fetch_available_slots()
    if slot_time not in free_slots:
        await state.update_data({DATA_SLOT: None})
        if not free_slots:
            await state.clear()
            await message.answer(NO_SLOTS_TEXT, reply_markup=ReplyKeyboardRemove())
            return
        await state.set_state(AppointmentForm.slot_time)
        await message.answer(
            SLOT_TAKEN_TEXT,
            reply_markup=slots_keyboard(free_slots),
        )
        return

    try:
        await append_appointment(
            slot_time,
            full_name,
            phone,
            complaints,
            str(data[DATA_ANALYSES]),
            telegram_username(message),
        )
    except Exception:
        logger.exception("Не удалось записать анкету через Google Web App")
        await message.answer(
            "Не получилось сохранить анкету. Попробуйте отправить номер ещё раз."
        )
        return

    if message.bot is not None:
        await notify_admin(message.bot, full_name, slot_time, complaints, phone)
    else:
        logger.error("Нет экземпляра бота, уведомление администратору не отправлено")

    await state.clear()
    await message.answer(
        f"✅ Спасибо! Ваша анкета сохранена. Вы записаны на {slot_time}. "
        "Врач ознакомится с вашими ответами и свяжется с вами.",
        reply_markup=ReplyKeyboardRemove(),
    )


@fallback_router.message()
async def fallback_message(message: Message) -> None:
    """Сообщения вне анкеты не сбрасывают состояние — его просто нет."""
    await message.answer("Чтобы заполнить анкету, отправьте /start.")


@fallback_router.callback_query()
async def fallback_callback(callback: CallbackQuery, state: FSMContext) -> None:
    """Чужие или устаревшие кнопки не должны крутить индикатор загрузки.

    Если анкета уже идёт, состояние не трогаем — человек продолжает текущий шаг.
    """
    if await state.get_state() is None:
        await callback.answer(
            "Эта кнопка уже неактивна. Отправьте /start, чтобы начать анкету.",
            show_alert=True,
        )
        return
    await callback.answer(
        "Сейчас эта кнопка не нужна. Ответьте на последний вопрос анкеты.",
        show_alert=True,
    )


async def main() -> None:
    token = env_value("BOT_TOKEN")
    if not token:
        raise SystemExit("Задайте BOT_TOKEN в окружении или в файле .env")
    if not env_value("GOOGLE_WEBAPP_URL"):
        raise SystemExit("Задайте GOOGLE_WEBAPP_URL в окружении или в файле .env")

    # MemoryStorage достаточно для MVP: состояния живут, пока запущен процесс.
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(commands_router)
    dispatcher.include_router(form_router)
    dispatcher.include_router(fallback_router)

    bot = Bot(token=token)
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(main())
