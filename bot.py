# bot.py
import asyncio
import json
import re
from pathlib import Path
from aiohttp import web

from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardMarkup,InlineQuery, InlineKeyboardButton, InlineQueryResultArticle, InputTextMessageContent
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
import uuid
from aiogram.utils.keyboard import InlineKeyboardBuilder


BOT_TOKEN = "8533360770:AAF7kZ024e0x3tdVaA0kenOA3tMXhSE4rVM"
SUPERADMIN_ID = 1781733822
DATA_FILE = "data.json"

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode="HTML")
)
dp = Dispatcher()

DATA_DEFAULT = {
    "users": {},  # userid -> {id, username, first_name, last_name}
    "admins": [SUPERADMIN_ID],  # list of admin ids (superadmin included)
    "superadmin": SUPERADMIN_ID,
    "required_channels": [],  # list of channel usernames like "@mychannel"
    "pending": {}  # admin_id -> {"action": "broadcast"|"add_admin"|..., "meta": {}}
}

DATA_PATH = Path(DATA_FILE)

# --- utility: load/save data ---
def load_data():
    if DATA_PATH.exists():
        return json.loads(DATA_PATH.read_text(encoding="utf-8"))
    else:
        DATA_PATH.write_text(json.dumps(DATA_DEFAULT, ensure_ascii=False, indent=2), encoding="utf-8")
        return json.loads(DATA_PATH.read_text(encoding="utf-8"))

def save_data(d):
    DATA_PATH.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

data = load_data()

# --- Morse tables ---
MORSE = {
    'A': '.-', 'B': '-...', 'C': '-.-.', 'D': '-..',
    'E': '.', 'F': '..-.', 'G': '--.', 'H': '....',
    'I': '..', 'J': '.---', 'K': '-.-', 'L': '.-..',
    'M': '--', 'N': '-.', 'O': '---', 'P': '.--.',
    'Q': '--.-', 'R': '.-.', 'S': '...', 'T': '-',
    'U': '..-', 'V': '...-', 'W': '.--', 'X': '-..-',
    'Y': '-.--', 'Z': '--..',
    '0': '-----', '1': '.----', '2': '..---', '3': '...--',
    '4': '....-', '5': '.....', '6': '-....', '7': '--...',
    '8': '---..', '9': '----.',
    '.': '.-.-.-', ',': '--..--', '?': '..--..', "'": '.----.',
    '!': '-.-.--', '/': '-..-.', '(': '-.--.', ')': '-.--.-',
    '&': '.-...', ':': '---...', ';': '-.-.-.', '=': '-...-',
    '+': '.-.-.', '-': '-....-', '_': '..--.-', '"': '.-..-.',
    '$': '...-..-', '@': '.--.-.'
}
# Reverse map
MORSE_REV = {v: k for k, v in MORSE.items()}

# --- helpers ---
def is_morse_text(s: str) -> bool:
    # allow dots, dashes, spaces and slash (/) as word separator
    return bool(re.fullmatch(r'[\s\.\-\/]+', s.strip()))

def encode_to_morse(text: str) -> str:
    text = text.upper()
    words = text.split()
    parts = []
    for w in words:
        letters = []
        for ch in w:
            if ch in MORSE:
                letters.append(MORSE[ch])
            # ignore unsupported chars
        parts.append(' '.join(letters))
    return ' / '.join(parts)  # slash between words

def decode_from_morse(text: str) -> str:
    # expect words separated by '/' or double spaces
    text = text.strip()
    # normalize separators
    text = text.replace('   ', ' / ')
    text = text.replace('  ', ' ')
    words = [w.strip() for w in re.split(r'[/]', text)]
    out_words = []
    for w in words:
        if not w:
            continue
        letters = []
        for token in w.split():
            if token in MORSE_REV:
                letters.append(MORSE_REV[token])
            else:
                # unknown token -> put '?'
                letters.append('?')
        out_words.append(''.join(letters))
    return ' '.join(out_words)

def ensure_user(user: types.User):
    uid = user.id
    if str(uid) not in data["users"]:
        data["users"][str(uid)] = {
            "id": uid,
            "username": user.username or "",
            "first_name": user.first_name or "",
            "last_name": user.last_name or ""
        }
        save_data(data)

async def check_required_channels(user_id: int) -> (bool, str):
    """
    Returns (True, "") if user is member of all required_channels.
    Otherwise (False, message) where message tells which channels to join.
    """
    missing = []
    for ch in data.get("required_channels", []):
        try:
            mem = await bot.get_chat_member(chat_id=ch, user_id=user_id)
            if mem.status in ["left", "kicked"]:
                missing.append(ch)
        except Exception:
            # if get_chat_member fails (e.g. bot not admin or channel username invalid), treat as missing
            missing.append(ch)
    if missing:
        msg = "Iltimos quyidagi kanallarga obuna bo'ling va keyin yana urinib ko'ring:\n"
        for ch in missing:
            # create link
            ch_display = ch if ch.startswith("@") else ("@" + ch)
            msg += f"- {ch_display}\n"
        return False, msg
    return True, ""

def is_admin(user_id: int) -> bool:
    return user_id in data.get("admins", [])

def is_superadmin(user_id: int) -> bool:
    return user_id == data.get("superadmin")

# --- keyboards for admin panel ---
def admin_panel_kb():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Foydalanuvchilar roʻyxati", callback_data="users")],
        [InlineKeyboardButton(text="Statistika", callback_data="stats")],
        [InlineKeyboardButton(text="Hammaga xabar (Broadcast)", callback_data="broadcast")],
        [InlineKeyboardButton(text="Majburiy kanallar", callback_data="channels")],
        [InlineKeyboardButton(text="Adminlarni boshqarish", callback_data="admins")],
    ])
    return kb

def channels_kb():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Qo'shish", callback_data="chan_add")],
        [InlineKeyboardButton(text="Olib tashlash", callback_data="chan_remove")],
        [InlineKeyboardButton(text="Orqaga", callback_data="admin_back")],
    ])
    return kb

def admins_kb(can_manage: bool):
    # can_manage - True if superadmin
    buttons = []
    if can_manage:
        buttons.append([InlineKeyboardButton(text="Admin qo'shish", callback_data="admin_add")])
        buttons.append([InlineKeyboardButton(text="Admin ozod qilish", callback_data="admin_remove")])
    buttons.append([InlineKeyboardButton(text="Orqaga", callback_data="admin_back")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    return kb

# --- handlers ---

@dp.message(Command(commands=["start"]))
async def cmd_start(message: types.Message):
    ensure_user(message.from_user)
    await message.reply("Salom! Morse ↔ Lotin botiga xush kelibsiz.\nBotga matn yuboring: agar xabar faqat '.' va '-' bo'lsa — u tarjima qilinadi; aks holda lotindan morsega.")
    # You can also show admin panel button for admins
    if is_admin(message.from_user.id):
        await message.answer("Admin panel ochish uchun /panel buyrug'ini yuboring.")

@dp.message(Command(commands=["panel"]))
async def cmd_panel(message: types.Message):
    uid = message.from_user.id
    if not is_admin(uid):
        await message.reply("Siz admin emassiz.")
        return
    kb = admin_panel_kb()
    await message.reply("Admin panel — tugmalar orqali boshqarish.", reply_markup=kb)



def to_morse(text: str):
    out = []
    for ch in text.upper():
        out.append(MORSE.get(ch, '?'))
    return ' '.join(out)

# Inline query handler
async def inline_handler(query: InlineQuery):
    text = query.query.strip()

    if not text:
        return

    morse = to_morse(text)

    result = [
        InlineQueryResultArticle(
            id=str(uuid.uuid4()),
            title="🔎 Morzega o‘girish",
            description=morse,
            input_message_content=InputTextMessageContent(
                message_text=morse
            )
        )
    ]

    await query.answer(results=result, cache_time=0)


# General message handler (no commands) — core behavior for users
@dp.message()
async def general_handler(message: types.Message):
    ensure_user(message.from_user)

    # check channel membership if required channels set
    ok, note = await check_required_channels(message.from_user.id)
    if not ok:
        await message.reply(note)
        return

    text = message.text or ""
    if not text.strip():
        return

    # if admin has pending action, handle separately
    uid = message.from_user.id
    pending = data.get("pending", {}).get(str(uid))
    if pending:
        act = pending.get("action")
        if act == "broadcast":
            # send message to all users
            content = message.text
            await message.reply("Broadcast yuborilmoqda... (jo'natilmoqda)")
            cnt = 0
            errs = 0
            for ustr, uinfo in data.get("users", {}).items():
                try:
                    await bot.send_message(int(ustr), content)
                    cnt += 1
                except Exception:
                    errs += 1
            # clear pending
            data["pending"].pop(str(uid), None)
            save_data(data)
            await message.reply(f"Yuborildi: {cnt} ta, xatoliklar: {errs}")
            return
        elif act == "chan_add":
            ch = text.strip()
            if not ch.startswith("@"):
                ch = "@" + ch
            if ch not in data.get("required_channels", []):
                data["required_channels"].append(ch)
                save_data(data)
                await message.reply(f"Majburiy kanal qo'shildi: {ch}")
            else:
                await message.reply(f"{ch} oldin qo'shilgan.")
            data["pending"].pop(str(uid), None)
            save_data(data)
            return
        elif act == "chan_remove":
            ch = text.strip()
            if not ch.startswith("@"):
                ch = "@" + ch
            if ch in data.get("required_channels", []):
                data["required_channels"].remove(ch)
                save_data(data)
                await message.reply(f"Majburiy kanal olib tashlandi: {ch}")
            else:
                await message.reply(f"{ch} majburiy kanallar ro'yxatida yo'q.")
            data["pending"].pop(str(uid), None)
            save_data(data)
            return
        elif act == "admin_add":
            try:
                new_id = int(text.strip())
                if new_id in data.get("admins", []):
                    await message.reply("Bu foydalanuvchi allaqachon admin.")
                else:
                    data["admins"].append(new_id)
                    save_data(data)
                    await message.reply(f"Admin qo'shildi: {new_id}")
                    try:
                        await bot.send_message(new_id, "Sizga admin huquqlari berildi.")
                    except:
                        pass
            except:
                await message.reply("Iltimos faqat numeric user id kiriting.")
            data["pending"].pop(str(uid), None)
            save_data(data)
            return
        elif act == "admin_remove":
            try:
                rem_id = int(text.strip())
                if rem_id == data.get("superadmin"):
                    await message.reply("Superadminni o'chirish mumkin emas.")
                elif rem_id not in data.get("admins", []):
                    await message.reply("Bu id admin emas.")
                else:
                    data["admins"].remove(rem_id)
                    save_data(data)
                    await message.reply(f"Admin olib tashlandi: {rem_id}")
                    try:
                        await bot.send_message(rem_id, "Sizdan admin huquqlari olib tashlandi.")
                    except:
                        pass
            except:
                await message.reply("Iltimos faqat numeric user id kiriting.")
            data["pending"].pop(str(uid), None)
            save_data(data)
            return
        else:
            # unknown pending -> clear
            data["pending"].pop(str(uid), None)
            save_data(data)

    # No pending admin action: normal encode/decode behavior
    if is_morse_text(text):
        decoded = decode_from_morse(text)
        await message.reply(f"🔤 Tarjima (Morse→Lotin):\n<code>{decoded}</code>")
    else:
        encoded = encode_to_morse(text)
        await message.reply(f"⚡ Aylantirish (Lotin→Morse):\n<code>{encoded}</code>")

# Callback queries for admin panel buttons
@dp.callback_query(
    F.data.startswith("admin_")
    | F.data.startswith("chan_")
    | F.data.in_(["users", "stats", "broadcast", "channels", "admins", "admin_back"])
)
async def cb_admins(cq: types.CallbackQuery):
    uid = cq.from_user.id
    if not is_admin(uid):
        await cq.answer("Siz admin emassiz.", show_alert=True)
        return
    data_local = data  # reference
    await cq.answer()

    if cq.data == "users":
        users = data_local.get("users", {})
        text = f"Foydalanuvchilar soni: {len(users)}\n\n"
        # limit list to 100 to avoid long messages
        for i, (uidk, info) in enumerate(users.items()):
            if i >= 100:
                text += f"\n... va boshqalar ({len(users)-100})\n"
                break
            uname = info.get("username") or f"{info.get('first_name','')}"
            text += f"- {info.get('id')} | {uname}\n"
        await cq.message.answer(text)

    elif cq.data == "stats":
        users = data_local.get("users", {})
        admins = data_local.get("admins", [])
        channels = data_local.get("required_channels", [])
        text = f"📊 Statistika:\n- Foydalanuvchilar: {len(users)}\n- Adminlar: {len(admins)}\n- Majburiy kanallar: {len(channels)}\n"
        await cq.message.answer(text)

    elif cq.data == "broadcast":
        # set pending for this admin
        data_local.setdefault("pending", {})[str(uid)] = {"action": "broadcast"}
        save_data(data_local)
        await cq.message.answer("Hammaga yuboriladigan xabar matnini yuboring (oddiy chatda yuboring).")

    elif cq.data == "channels":
        await cq.message.answer("Majburiy kanallar boshqaruvi:", reply_markup=channels_kb())

    elif cq.data == "chan_add":
        data_local.setdefault("pending", {})[str(uid)] = {"action": "chan_add"}
        save_data(data_local)
        await cq.message.answer("Qo'shmoqchi bo'lgan kanal username'ini yuboring (masalan: @mychannel yoki mychannel).")

    elif cq.data == "chan_remove":
        data_local.setdefault("pending", {})[str(uid)] = {"action": "chan_remove"}
        save_data(data_local)
        await cq.message.answer("Olib tashlamoqchi bo'lgan kanal username'ini yuboring (masalan: @mychannel yoki mychannel).")

    elif cq.data == "admins":
        can_manage = is_superadmin(uid)
        await cq.message.answer("Adminlarni boshqarish:", reply_markup=admins_kb(can_manage))

    elif cq.data == "admin_add":
        if not is_superadmin(uid):
            await cq.message.answer("Faqat superadmin qo'shishi mumkin.")
            return
        data_local.setdefault("pending", {})[str(uid)] = {"action": "admin_add"}
        save_data(data_local)
        await cq.message.answer("Yangi adminning Telegram user id'sini yuboring (raqam ko'rinishida).")

    elif cq.data == "admin_remove":
        if not is_superadmin(uid):
            await cq.message.answer("Faqat superadmin olib tashlashi mumkin.")
            return
        data_local.setdefault("pending", {})[str(uid)] = {"action": "admin_remove"}
        save_data(data_local)
        await cq.message.answer("Olib tashlamoqchi bo'lgan admin user id'sini yuboring (raqam ko'rinishida).")

    elif cq.data == "admin_back":
        await cq.message.edit_text("Admin panel — tugmalar orqali boshqarish.", reply_markup=admin_panel_kb())

# Fallback: gracefully handle errors
@dp.errors()
async def errors_handler(update, exception):
    # minimal error logging
    print(f"Error: {exception}")
    return True

async def handle(request):
    return web.Response(text="Bot is alive ✅")

async def start_web():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 10000)  # Render port, agar boshqacha bo‘lsa .env orqali olishingiz mumkin
    await site.start()


# Run
async def main():
    await start_web()
    print("Bot ishga tushmoqda...")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
