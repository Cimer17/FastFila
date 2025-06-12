from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates

import sqlite3
import markdown as md
import openai
import os

# Путь к файлу базы данных SQLite (без URI-префикса)
DB_PATH = "sqlite:////datafila/questions.db"
# Путь к файлу со списком вопросов
QUESTIONS_FILE_PATH = "questions.txt"

# Инициализация клиента OpenAI
openai_client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

def get_db():
    """Возвращает соединение с SQLite и настраивает row_factory."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

@asynccontextmanager
async def lifespan(app: FastAPI):
    # При старте приложения — создаём таблицу, если она ещё не существует
    print("Запуск приложения...")
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title   TEXT NOT NULL UNIQUE,
            content TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()
    print(f"База данных '{DB_PATH}' успешно инициализирована.")
    yield
    # При завершении приложения
    print("Приложение завершает работу.")

# Единственный экземпляр FastAPI с lifespan-хэндлером
app = FastAPI(lifespan=lifespan)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
async def list_questions(request: Request, q: str = ""):
    conn = get_db()
    rows = conn.execute("SELECT * FROM questions ORDER BY id").fetchall()
    conn.close()

    if q:
        q_lower = q.lower()
        filtered = [r for r in rows if q_lower in r["title"].lower()]
    else:
        filtered = rows

    return templates.TemplateResponse(
        "index.html",
        {"request": request, "questions": filtered, "q": q}
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
async def seed_questions_from_file():
    # Проверяем файл с вопросами
    if not os.path.exists(QUESTIONS_FILE_PATH):
        raise HTTPException(
            status_code=404,
            detail=f"Файл с вопросами '{QUESTIONS_FILE_PATH}' не найден."
        )
    # Проверяем OPENAI_API_KEY
    if not os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(
            status_code=500,
            detail="OPENAI_API_KEY не установлен в переменных окружения."
        )

    processed_count = 0
    failed_questions = []

    with open(QUESTIONS_FILE_PATH, "r", encoding="utf-8") as f:
        questions_to_process = [line.strip() for line in f if line.strip()]

    conn = get_db()
    cursor = conn.cursor()

    for title in questions_to_process:
        try:
            # Избегаем дубликатов
            cursor.execute("SELECT id FROM questions WHERE title = ?", (title,))
            if cursor.fetchone():
                processed_count += 1
                continue

            prompt_messages = [
                {
                    "role": "system",
                    "content": (
                        "Ты - глубокомысленный философ, способный анализировать сложные идеи "
                        "и выражать их ясно и доступно в формате Markdown. Твой ответ должен "
                        "быть полным и содержательным, но не чрезмерно длинным, сосредоточься "
                        "на основных философских аспектах. Используй стандартный Markdown "
                        "для форматирования текста (заголовки, списки, курсив, жирный текст)."
                    )
                },
                {
                    "role": "user",
                    "content": f"Пожалуйста, дай философский ответ на следующий вопрос: {title}"
                }
            ]

            print(f"Запрашиваю ответ у OpenAI для: '{title}'...")
            response = await openai_client.chat.completions.create(
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
                    f"Процесс завершен: успешно {processed_count}, "
                    f"не удалось {len(failed_questions)}."
                ),
                "failed_questions": failed_questions
            }
        )

    return JSONResponse(
        status_code=200,
        content={"message": f"Все {processed_count} вопросов успешно обработаны."}
    )
