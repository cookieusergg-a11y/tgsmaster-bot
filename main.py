import asyncio
import random
import time
import secrets
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import aiosqlite
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ===== ЛОГИРОВАНИЕ =====
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ===== НОВЫЙ ТОКЕН =====
BOT_TOKEN = "8546786613:AAGpcqtJFrCi7zSw6jbC50sjudL0dxgYt_M"
ADMIN_ID = 8953762615
DB_PATH = "data.db"

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
        logger.info("База данных инициализирована")

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
    logger.info(f"Команда /start от {update.effective_user.id}")
    await update.message.reply_text(
        "👋 Привет! Напиши мне любое сообщение, и я пришлю тебе код для входа на сайт."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info(f"Получено сообщение от {user_id}: {update.message.text}")
    
    code = f"{random.randint(100000, 999999)}"
    pending_codes[code] = {
        "user_id": user_id,
        "timestamp": time.time()
    }
    await update.message.reply_text(
        f"🔑 Твой код для входа: `{code}`\n"
        "Введи его на сайте TgsMaster.\n"
        "Код действителен 5 минут.",
        parse_mode="Markdown"
    )
    logger.info(f"Код {code} отправлен пользователю {user_id}")

# ===== ГЛОБАЛЬНЫЙ ОБЪЕКТ БОТА =====
bot_app = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot_app
    logger.info("Запуск приложения...")
    
    bot_app = Application.builder().token(BOT_TOKEN).build()
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    await bot_app.initialize()
    logger.info("Бот инициализирован")
    
    # Получаем URL сервера из переменной окружения Railway
    webhook_url = f"https://{os.getenv('RAILWAY_STATIC_URL', 'localhost')}/webhook"
    # Или можно указать вручную:
    # webhook_url = "https://твой-домен.up.railway.app/webhook"
    
    # Устанавливаем вебхук (вместо поллинга)
    await bot_app.bot.set_webhook(url=webhook_url)
    logger.info(f"Вебхук установлен на {webhook_url}")
    
    await init_db()
    
    yield
    
    if bot_app:
        await bot_app.shutdown()
        logger.info("Бот остановлен")

# ===== FASTAPI =====
app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class VerifyRequest(BaseModel):
    code: str

class GrantRequest(BaseModel):
    user_id: int
    days: int

# ===== ВЕБХУК ДЛЯ TELEGRAM =====
@app.post("/webhook")
async def webhook(request: Request):
    """Принимает обновления от Telegram."""
    try:
        data = await request.json()
        update = Update.de_json(data, bot_app.bot)
        await bot_app.process_update(update)
        return {"ok": True}
    except Exception as e:
        logger.error(f"Ошибка в вебхуке: {e}")
        return {"ok": False}

@app.post("/verify-code")
async def verify_code(req: VerifyRequest):
    code = req.code.strip()
    logger.info(f"Попытка входа с кодом {code}")
    if code not in pending_codes:
        logger.warning(f"Код {code} не найден")
        raise HTTPException(400, "Неверный или истёкший код")
    
    entry = pending_codes[code]
    if time.time() - entry["timestamp"] > 300:
        del pending_codes[code]
        logger.warning(f"Код {code} истёк")
        raise HTTPException(400, "Код истёк. Запросите новый у бота")
    
    user_id = entry["user_id"]
    del pending_codes[code]
    logger.info(f"Код {code} подтверждён для пользователя {user_id}")
    
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
