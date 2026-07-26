from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import time
import secrets
from bot import bot, codes  # импортируем из bot.py

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

class CodeRequest(BaseModel):
    user_id: int

class VerifyRequest(BaseModel):
    user_id: int
    code: str

# Храним активные сессии (токены)
sessions = {}  # token -> user_id, expires

@app.post("/send-code")
async def send_code(req: CodeRequest):
    user_id = req.user_id
    # Проверяем, не запрашивал ли он код недавно
    if user_id in codes and time.time() - codes[user_id][1] < 30:
        raise HTTPException(400, "Подождите 30 секунд")
    
    # Генерируем код
    code = f"{random.randint(100000, 999999)}"
    codes[user_id] = (code, time.time())
    
    # Отправляем через бота
    try:
        await bot.send_message(user_id, f"🔑 Твой код для входа: `{code}`\nДействителен 5 минут.", parse_mode="Markdown")
        return {"ok": True}
    except Exception as e:
        raise HTTPException(400, f"Не удалось отправить код: {e}")

@app.post("/verify-code")
async def verify_code(req: VerifyRequest):
    user_id = req.user_id
    code = req.code.strip()
    
    if user_id not in codes:
        raise HTTPException(400, "Сначала запроси код")
    
    stored_code, timestamp = codes[user_id]
    if time.time() - timestamp > 300:  # 5 минут
        del codes[user_id]
        raise HTTPException(400, "Код истёк. Запросите новый")
    
    if stored_code != code:
        raise HTTPException(400, "Неверный код")
    
    # Код верный — создаём сессию
    token = secrets.token_urlsafe(32)
    sessions[token] = {"user_id": user_id, "expires": time.time() + 86400}  # 24 часа
    del codes[user_id]  # удаляем код, он больше не нужен
    
    # Возвращаем токен и данные пользователя
    return {
        "token": token,
        "user": {
            "id": user_id,
            "name": "User"
        }
    }

# Проверка токена (для защиты страницы)
@app.get("/check-session")
async def check_session(token: str):
    if token not in sessions:
        raise HTTPException(401, "Недействительная сессия")
    sess = sessions[token]
    if time.time() > sess["expires"]:
        del sessions[token]
        raise HTTPException(401, "Сессия истекла")
    return {"user_id": sess["user_id"]}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)