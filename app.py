from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import sqlite3
import markdown as md
import openai
import os

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Монтируем директорию 'static' для статических файлов (CSS, JS)
app.mount("/static", StaticFiles(directory="static"), name="static")
# Настраиваем Jinja2 шаблоны, указывая директорию 'templates'
templates = Jinja2Templates(directory="templates")

# Путь к файлу базы данных SQLite
DB_PATH = "sqlite:////datafila/questions.db" 
# Путь к файлу, содержащему список вопросов (по одному вопросу на строку)
QUESTIONS_FILE_PATH = "questions.txt"


openai_client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

def get_db():
    """Функция для получения соединения с базой данных SQLite."""
    conn = sqlite3.connect(DB_PATH)
    # Устанавливаем row_factory, чтобы получать строки как объекты, похожие на словари
    conn.row_factory = sqlite3.Row
    return conn

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Обработчик событий жизненного цикла приложения FastAPI.
    Выполняется при запуске и завершении работы приложения.
    """
    # Логика запуска приложения
    print("Запуск приложения...")
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL UNIQUE, # Добавлено UNIQUE, чтобы избежать дубликатов по заголовку
            content TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()
    print(f"База данных '{DB_PATH}' успешно инициализирована.")
    
    # Yield control to the application, allowing it to serve requests
    yield
    
    # Логика завершения работы приложения
    print("Приложение завершает работу.")
    # Здесь можно добавить логику закрытия ресурсов, если они были открыты глобально
    # Например, закрытие пулов соединений с базой данных

# Инициализация FastAPI приложения с обработчиком жизненного цикла
app = FastAPI(lifespan=lifespan)

# Добавляем middleware для Cross-Origin Resource Sharing (CORS)
# Это позволяет запросам с разных доменов получать доступ к вашему API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Разрешает запросы с любых доменов. Для продакшена лучше указать конкретные домены.
    allow_credentials=True, # Разрешает использование куки (cookies) и заголовков авторизации (Authorization headers)
    allow_methods=["*"],  # Разрешает все HTTP методы (GET, POST, PUT, DELETE и т.д.)
    allow_headers=["*"],  # Разрешает все заголовки HTTP запросов
)

# Монтируем директорию 'static' для статических файлов (CSS, JS)
app.mount("/static", StaticFiles(directory="static"), name="static")
# Настраиваем Jinja2 шаблоны, указывая директорию 'templates'
templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
async def list_questions(request: Request, q: str = ""):
    """
    Отображает список вопросов на главной странице.
    Позволяет фильтровать вопросы по заголовку.
    """
    conn = get_db()
    # Получаем все вопросы из БД, отсортированные по ID
    rows = conn.execute("SELECT * FROM questions ORDER BY id").fetchall()
    conn.close()

    # Фильтрация вопросов на стороне Python для корректной работы с Unicode
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
    """
    Отображает детали конкретного вопроса по его ID.
    Контент вопроса, хранящийся в Markdown, конвертируется в HTML.
    """
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM questions WHERE id = ?",
        (question_id,)
    ).fetchone()
    conn.close()

    if not row:
        # Возвращаем 404, если вопрос не найден
        raise HTTPException(status_code=404, detail="Вопрос не найден")
    
    # Конвертируем Markdown контент в HTML
    html_content = md.markdown(row["content"])
    
    return templates.TemplateResponse(
        "detail.html",
        {"request": request, "title": row["title"], "content": html_content}
    )

@app.post("/seed_questions")
async def seed_questions_from_file():
    """
    Эндпоинт для запуска процесса заполнения базы данных вопросами из файла
    и получения ответов на них через OpenAI API.
    """
    # Проверка наличия файла с вопросами
    if not os.path.exists(QUESTIONS_FILE_PATH):
        raise HTTPException(status_code=404, detail=f"Файл с вопросами '{QUESTIONS_FILE_PATH}' не найден.")

    # Проверка, установлен ли API ключ OpenAI
    if not os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY не установлен в переменных окружения. Пожалуйста, установите его перед запуском.")

    processed_count = 0
    failed_questions = []

    try:
        # Читаем вопросы из файла
        with open(QUESTIONS_FILE_PATH, 'r', encoding='utf-8') as f:
            # Очищаем строки от лишних пробелов и пустых строк
            questions_to_process = [line.strip() for line in f if line.strip()]

        conn = get_db()
        cursor = conn.cursor()

        for question_title in questions_to_process:
            try:
                # Проверяем, существует ли вопрос уже в базе данных, чтобы избежать дубликатов
                cursor.execute("SELECT id FROM questions WHERE title = ?", (question_title,))
                if cursor.fetchone():
                    print(f"Вопрос '{question_title}' уже существует в базе данных. Пропускаем.")
                    processed_count += 1
                    continue

                # Формируем промт для OpenAI
                prompt_messages = [
                    {"role": "system", "content": "Ты - глубокомысленный философ, способный анализировать сложные идеи и выражать их ясно и доступно в формате Markdown. Твой ответ должен быть полным и содержательным, но не чрезмерно длинным, сосредоточься на основных философских аспектах. Используй стандартный Markdown для форматирования текста (заголовки, списки, курсив, жирный текст)."},
                    {"role": "user", "content": f"Пожалуйста, дай философский ответ на следующий вопрос: {question_title}"}
                ]

                print(f"Запрашиваю ответ у OpenAI для вопроса: '{question_title}'...")
                # Выполняем асинхронный вызов к OpenAI API
                response = await openai_client.chat.completions.create(
                    model="gpt-4o", # Рекомендуемая модель для философских вопросов
                    messages=prompt_messages,
                    max_tokens=1000, # Максимальное количество токенов в ответе. Настройте по необходимости.
                    temperature=0.7 # Баланс между креативностью и когерентностью.
                )

                philosophical_answer = response.choices[0].message.content

                # Сохраняем вопрос и полученный ответ в базу данных
                cursor.execute(
                    "INSERT INTO questions (title, content) VALUES (?, ?)",
                    (question_title, philosophical_answer)
                )
                conn.commit() # Коммитим каждую запись, чтобы сохранить прогресс
                processed_count += 1
                print(f"Вопрос '{question_title}' успешно добавлен с ответом.")

            except sqlite3.IntegrityError as e:
                # Обработка случая, если UNIQUE constraint нарушен (одинаковый заголовок)
                print(f"Ошибка базы данных (дубликат заголовка) для вопроса '{question_title}': {e}")
                failed_questions.append(question_title)
            except openai.APIError as e:
                # Обработка ошибок, специфичных для OpenAI API
                print(f"Ошибка OpenAI API для вопроса '{question_title}': {e}")
                failed_questions.append(question_title)
            except Exception as e:
                # Обработка любых других непредвиденных ошибок
                print(f"Непредвиденная ошибка для вопроса '{question_title}': {e}")
                failed_questions.append(question_title)
    finally:
        # Закрываем соединение с БД в любом случае
        if conn:
            conn.close()

    # Возвращаем результат операции
    if failed_questions:
        return JSONResponse(
            status_code=207, # 207 Multi-Status, если есть частично успешные и неудачные
            content={
                "message": f"Процесс заполнения завершен. Успешно обработано: {processed_count}. Не удалось обработать: {len(failed_questions)} вопросов. Проверьте консоль для деталей.",
                "failed_questions": failed_questions
            }
        )
    else:
        return JSONResponse(
            status_code=200, # 200 OK, если все успешно
            content={"message": f"Все {processed_count} вопросов успешно обработаны и добавлены в базу данных."}
        )
