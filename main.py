import asyncio
import random
import time
import secrets
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import aiosqlite
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ===== КОНФИГ =====
BOT_TOKEN = "8546786613:AAF60Ujigmqh1SsHD2aBjvnwShX49i5anoU"
ADMIN_ID = 8953762615
DB_PATH = "data.db"

# ===== ХРАНИЛИЩЕ КОДОВ =====
pending_codes = {}

# ===== БАЗА ДАННЫХ =====
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

# ===== ОБРАБОТЧИКИ БОТА =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Чтобы войти на сайт, нажми кнопку 'Получить код' на сайте.\n"
        "Я пришлю тебе код в этот чат."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in pending_codes and time.time() - pending_codes[user_id][1] < 30:
        await update.message.reply_text("⏳ Подожди 30 секунд перед новым запросом.")
        return
    code = f"{random.randint(100000, 999999)}"
    pending_codes[user_id] = (code, time.time())
    await update.message.reply_text(
        f"🔑 Твой код для входа: `{code}`\nДействителен 5 минут.",
        parse_mode="Markdown"
    )

# ===== СОЗДАНИЕ ПРИЛОЖЕНИЯ БОТА (глобально) =====
bot_app = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot_app
    # Создаём Application (в PTB 21.x нет Updater, всё через Application)
    bot_app = Application.builder().token(BOT_TOKEN).build()
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    # Запускаем поллинг в фоновой задаче (не блокируем)
    asyncio.create_task(bot_app.run_polling())
    await init_db()
    yield
    # Здесь можно остановить бота при завершении, но для простоты пропустим

# ===== FASTAPI =====
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
        # Используем глобальный объект bot_app для отправки
        await bot_app.bot.send_message(
            chat_id=user_id,
            text=f"🔑 Твой код для входа: `{code}`\nДействителен 5 минут.",
            parse_mode="Markdown"
        )
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

@app.get("/", response_class=HTMLResponse)
async def index():
    with open("static/index.html", "r", encoding="utf-8") as f:
        return f.read()

app.mount("/static", StaticFiles(directory="static"), name="static")
