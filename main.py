import asyncio
import random
import time
import secrets
import sqlite3
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
import aiosqlite

# ===== КОНФИГ =====
BOT_TOKEN = "8546786613:AAF60Ujigmqh1SsHD2aBjvnwShX49i5anoU"
ADMIN_ID = 8953762615
DB_PATH = "data.db"

# ===== ИНИЦИАЛИЗАЦИЯ БОТА =====
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ===== ХРАНИЛИЩЕ КОДОВ В ПАМЯТИ =====
pending_codes = {}

# ===== БАЗА ДАННЫХ (SQLite) =====
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                name TEXT,
                subscription_type TEXT,
                expires INTEGER,
                created_at INTEGER
            )
        """)
        await db.commit()

async def get_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id, name, subscription_type, expires FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return {
                    "user_id": row[0],
                    "name": row[1],
                    "subscription_type": row[2],
                    "expires": row[3]
                }
            return None

async def create_user(user_id: int, name: str = "User"):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, name, created_at) VALUES (?, ?, ?)",
            (user_id, name, int(time.time()))
        )
        if user_id == ADMIN_ID:
            await db.execute(
                "UPDATE users SET subscription_type = 'eternal' WHERE user_id = ?",
                (user_id,)
            )
        await db.commit()

async def update_subscription(user_id: int, days: int):
    async with aiosqlite.connect(DB_PATH) as db:
        expires = int(time.time()) + days * 86400
        await db.execute(
            "UPDATE users SET subscription_type = 'days', expires = ? WHERE user_id = ?",
            (expires, user_id)
        )
        await db.commit()

async def get_all_users():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id, name, subscription_type, expires FROM users") as cursor:
            rows = await cursor.fetchall()
            users = []
            for row in rows:
                users.append({
                    "user_id": row[0],
                    "name": row[1],
                    "subscription_type": row[2],
                    "expires": row[3]
                })
            return users

def is_subscription_active(user_id: int, user_data: dict):
    if user_data["subscription_type"] == "eternal":
        return True
    if user_data["subscription_type"] == "days" and user_data["expires"]:
        return time.time() < user_data["expires"]
    return False

# ===== КОМАНДЫ БОТА =====
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await message.answer(
        "👋 Привет! Чтобы войти на сайт, нажми кнопку 'Получить код' на сайте.\n"
        "Я пришлю тебе код в этот чат."
    )

@dp.message()
async def handle_code_request(message: types.Message):
    user_id = message.from_user.id
    if user_id in pending_codes and time.time() - pending_codes[user_id][1] < 30:
        await message.answer("⏳ Подожди 30 секунд перед новым запросом.")
        return
    code = f"{random.randint(100000, 999999)}"
    pending_codes[user_id] = (code, time.time())
    await message.answer(f"🔑 Твой код для входа: `{code}`\nДействителен 5 минут.", parse_mode="Markdown")

# ===== FASTAPI =====
@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(dp.start_polling(bot))
    await init_db()
    yield

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class CodeRequest(BaseModel):
    user_id: int

class VerifyRequest(BaseModel):
    user_id: int
    code: str

class GrantRequest(BaseModel):
    user_id: int
    days: int

@app.post("/send-code")
async def send_code(req: CodeRequest):
    user_id = req.user_id
    if user_id in pending_codes and time.time() - pending_codes[user_id][1] < 30:
        raise HTTPException(400, "Подождите 30 секунд")
    code = f"{random.randint(100000, 999999)}"
    pending_codes[user_id] = (code, time.time())
    try:
        await bot.send_message(user_id, f"🔑 Твой код для входа: `{code}`\nДействителен 5 минут.", parse_mode="Markdown")
        return {"ok": True}
    except Exception as e:
        raise HTTPException(400, f"Не удалось отправить код: {e}")

@app.post("/verify-code")
async def verify_code(req: VerifyRequest):
    user_id = req.user_id
    code = req.code.strip()
    if user_id not in pending_codes:
        raise HTTPException(400, "Сначала запроси код")
    stored_code, timestamp = pending_codes[user_id]
    if time.time() - timestamp > 300:
        del pending_codes[user_id]
        raise HTTPException(400, "Код истёк. Запросите новый")
    if stored_code != code:
        raise HTTPException(400, "Неверный код")
    del pending_codes[user_id]
    user_data = await get_user(user_id)
    if not user_data:
        await create_user(user_id, f"User_{user_id}")
        user_data = await get_user(user_id)
    return {
        "ok": True,
        "user": {
            "id": user_id,
            "name": user_data["name"] if user_data else "User"
        }
    }

@app.post("/admin/users")
async def admin_users(req: Request):
    data = await req.json()
    admin_id = data.get("admin_id")
    if admin_id != ADMIN_ID:
        raise HTTPException(403, "Доступ запрещён")
    users = await get_all_users()
    for u in users:
        u["subscription_active"] = is_subscription_active(u["user_id"], u)
    return {"users": users}

@app.post("/admin/grant")
async def grant_subscription(req: GrantRequest, request: Request):
    data = await request.json()
    admin_id = data.get("admin_id")
    if admin_id != ADMIN_ID:
        raise HTTPException(403, "Доступ запрещён")
    target_id = req.user_id
    days = req.days
    if days < 1:
        raise HTTPException(400, "Дни должны быть >= 1")
    user_data = await get_user(target_id)
    if not user_data:
        await create_user(target_id, f"User_{target_id}")
    await update_subscription(target_id, days)
    return {"ok": True, "message": f"Выдано {days} дн. пользователю {target_id}"}

# Отдача HTML-страницы
@app.get("/", response_class=HTMLResponse)
async def index():
    with open("static/index.html", "r", encoding="utf-8") as f:
        return f.read()

# Статические файлы
app.mount("/static", StaticFiles(directory="static"), name="static")
