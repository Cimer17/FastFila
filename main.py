import os
import asyncio
import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates

import markdown as md
import openai

# Путь к файлу базы данных SQLite
DB_PATH = "datafila/questions.db"
# Путь к файлу со списком вопросов
QUESTIONS_FILE_PATH = "questions.txt"

# Инициализация клиента OpenAI
openai_client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

def get_db():
    """Гарантируем, что папка для БД есть, и возвращаем соединение."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

@asynccontextmanager
async def lifespan(app: FastAPI):
    # При старте — создаём таблицу, если её ещё нет
    print("Запуск приложения...")
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS questions (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            title   TEXT NOT NULL UNIQUE,
            content TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()
    print(f"БД '{DB_PATH}' инициализирована.")
    yield
    # При завершении
    print("Приложение завершает работу.")

# Основной FastAPI-инстанс
app = FastAPI(lifespan=lifespan)

# CORS-настройки
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Шаблоны Jinja2
templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
async def list_questions(request: Request, q: str = ""):
    conn = get_db()
    rows = conn.execute("SELECT * FROM questions ORDER BY id").fetchall()
    conn.close()

    if q:
        q_lower = q.lower()
        rows = [r for r in rows if q_lower in r["title"].lower()]

    return templates.TemplateResponse(
        "index.html",
        {"request": request, "questions": rows, "q": q}
    )


@app.get("/questions/{question_id}", response_class=HTMLResponse)
async def question_detail(request: Request, question_id: int):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM questions WHERE id = ?",
        (question_id,)
    ).fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Вопрос не найден")

    html_content = md.markdown(row["content"])
    return templates.TemplateResponse(
        "detail.html",
        {"request": request, "title": row["title"], "content": html_content}
    )


@app.post("/seed_questions")
async def seed_questions_from_file(request: Request):
    # Проверяем наличие файла с вопросами
    if not os.path.exists(QUESTIONS_FILE_PATH):
        raise HTTPException(
            status_code=404,
            detail=f"Файл '{QUESTIONS_FILE_PATH}' не найден."
        )
    # Проверяем API-ключ
    if not os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(
            status_code=500,
            detail="OPENAI_API_KEY не установлен."
        )

    processed_count = 0
    failed_questions = []

    with open(QUESTIONS_FILE_PATH, "r", encoding="utf-8") as f:
        questions_to_process = [line.strip() for line in f if line.strip()]

    conn = get_db()
    cursor = conn.cursor()

    for title in questions_to_process:
        # Прерываем, если клиент закрыл соединение
        if await request.is_disconnected():
            conn.close()
            return JSONResponse(
                status_code=499,
                content={
                    "message": (
                        f"Клиент отменил операцию после обработки {processed_count} вопросов."
                    )
                }
            )
        try:
            # Пропускаем дубликаты
            cursor.execute("SELECT id FROM questions WHERE title = ?", (title,))
            if cursor.fetchone():
                processed_count += 1
                continue

            prompt_messages = [
                {
                    "role": "system",
                    "content": (
                        "Ты — глубокомысленный философ, способный анализировать сложные идеи "
                        "и выражать их ясно и доступно в формате Markdown. Твой ответ должен "
                        "быть полным и содержательным, но не чрезмерно длинным. Используй "
                        "заголовки, списки, курсив и жирный текст."
                    )
                },
                {
                    "role": "user",
                    "content": f"Пожалуйста, дай философский ответ на вопрос: {title}"
                }
            ]

            print(f"Запрашиваю ответ у OpenAI для: '{title}'...")
            response = await asyncio.to_thread(
                openai_client.chat.completions.create,
                model="gpt-4o",
                messages=prompt_messages,
                max_tokens=1000,
                temperature=0.7
            )
            answer = response.choices[0].message.content

            cursor.execute(
                "INSERT INTO questions (title, content) VALUES (?, ?)",
                (title, answer)
            )
            conn.commit()
            processed_count += 1

        except sqlite3.IntegrityError:
            failed_questions.append(title)
        except Exception as e:
            print(f"Ошибка при обработке '{title}': {e}")
            failed_questions.append(title)

    conn.close()

    if failed_questions:
        return JSONResponse(
            status_code=207,
            content={
                "message": (
                    f"Процесс завершен: успешно {processed_count}, не удалось {len(failed_questions)}."
                ),
                "failed_questions": failed_questions
            }
        )

    return JSONResponse(
        status_code=200,
        content={"message": f"Все {processed_count} вопросов успешно добавлены."}
    )
