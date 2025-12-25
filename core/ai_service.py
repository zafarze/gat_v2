import json
import logging
import re
import time
import requests
from django.conf import settings
from django.db import connection
from .views.permissions import get_accessible_schools

logger = logging.getLogger(__name__)

def _extract_json(text):
    """
    Вырезает JSON-объект из текста ответа AI.
    Ищет контент между фигурными скобками { ... }.
    """
    try:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        return json.loads(text)
    except json.JSONDecodeError:
        return None

def _is_safe_sql(sql):
    """
    Блокирует опасные SQL-команды (защита от дурака).
    """
    forbidden_keywords = [
        'DROP', 'DELETE', 'UPDATE', 'INSERT', 'ALTER', 'TRUNCATE', 
        'GRANT', 'REVOKE', 'CREATE', 'REPLACE', 'EXECUTE'
    ]
    normalized_sql = sql.upper()
    for word in forbidden_keywords:
        if re.search(r'\b' + word + r'\b', normalized_sql):
            return False
    return True

def _send_direct_request(model_name, prompt):
    """
    Отправляет прямой HTTP запрос к API Google (v1beta).
    Минует библиотеку google-genai, чтобы избежать ошибок совместимости.
    """
    api_key = settings.GOOGLE_API_KEY
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    
    headers = {'Content-Type': 'application/json'}
    data = {"contents": [{"parts": [{"text": prompt}]}]}

    # Таймаут 25 секунд, чтобы успел подумать над сложным запросом
    response = requests.post(url, headers=headers, json=data, timeout=25)
    
    if response.status_code == 200:
        result = response.json()
        try:
            return result['candidates'][0]['content']['parts'][0]['text']
        except (KeyError, IndexError):
            return "Google вернул пустой ответ."
    elif response.status_code == 429:
        # Специальный код для лимитов
        raise Exception("429_LIMIT")
    elif response.status_code == 404:
        raise Exception(f"404_NOT_FOUND (Model {model_name})")
    else:
        raise Exception(f"HTTP {response.status_code}: {response.text}")

def _get_ai_response(prompt):
    """
    Пробует модели по очереди.
    Используем список моделей, который точно доступен твоему аккаунту.
    """
    models_to_try = [
        # 1. Новая Lite (быстрая, дешевая/бесплатная)
        "gemini-2.0-flash-lite-preview-02-05", 
        
        # 2. Стабильный алиас (обычно работает лучше всего)
        "gemini-flash-latest",
        
        # 3. Экспериментальная 2.0 (мощная)
        "gemini-2.0-flash-exp",
        
        # 4. Резерв
        "gemini-pro-latest"
    ]
    
    last_error = None
    
    for model in models_to_try:
        try:
            return _send_direct_request(model, prompt)
        except Exception as e:
            error_str = str(e)
            if "429_LIMIT" in error_str:
                # Если лимит, ждем 1 секунду и пробуем следующую
                time.sleep(1) 
            elif "404_NOT_FOUND" in error_str:
                logger.warning(f"Model {model} не найдена.")
            else:
                logger.warning(f"Model {model} ошибка: {e}")
            
            last_error = e
            continue
            
    if "429_LIMIT" in str(last_error):
        raise Exception("RATE_LIMIT_EXCEEDED")
        
    raise Exception(f"Все модели недоступны. Последняя ошибка: {last_error}")

def ask_database(user, user_question, chat_history=None):
    """
    Умный AI Andarz.
    """
    # 1. ПРОВЕРКА ПРАВ ДОСТУПА
    allowed_schools_qs = get_accessible_schools(user)
    if not allowed_schools_qs.exists():
        return "У вас пока нет доступа к данным школ."
        
    allowed_ids = list(allowed_schools_qs.values_list('id', flat=True))
    allowed_ids_str = ", ".join(map(str, allowed_ids))

    # 2. ФОРМИРОВАНИЕ ИСТОРИИ ЧАТА (КОНТЕКСТ)
    history_text = ""
    if chat_history:
        history_text = "ИСТОРИЯ ДИАЛОГА:\n"
        for msg in chat_history:
            role = "User" if msg['role'] == 'user' else "Assistant"
            # Чистим HTML теги из истории
            clean_text = re.sub('<[^<]+?>', '', msg['text'])
            history_text += f"{role}: {clean_text}\n"

    # 3. СИСТЕМНЫЙ ПРОМПТ (МОЗГ)
    system_prompt = f"""
    Ты — Андарз, дружелюбный ИИ-аналитик для школ Таджикистана.
    
    ТВОЯ ГЛАВНАЯ ЗАДАЧА — понять намерение пользователя:
    
    1. ЭТО ПРОСТО РАЗГОВОР? ("Привет", "Как дела?", "Спасибо", "Стоп", "Пока", "Кто ты?")
       -> Отвечай вежливо текстом.
       -> СТРОГО: "is_sql_needed": false
       -> "sql": "" (пусто)
    
    2. ЭТО ЗАПРОС ДАННЫХ? ("Сколько учеников?", "Оценки Халида", "Сравни школы")
       -> Генерируй SQL.
       -> "is_sql_needed": true
    
    СХЕМА БАЗЫ ДАННЫХ:
    core_school (id, name, city)
    core_schoolclass (id, name, school_id)
    core_student (id, first_name_ru, last_name_ru, school_class_id)
    core_gattest (id, name, test_date)
    core_studentresult (total_score, student_id, gat_test_id)
    
    ПРАВИЛА SQL:
    - Ищи данные ТОЛЬКО в школах с ID: ({allowed_ids_str}).
    - Если вопрос про ШКОЛЫ (количество учеников, средний балл) -> Группируй по `core_school.name`. НЕ выводи классы.
    - Если вопрос про КЛАССЫ -> Группируй по `core_schoolclass.name`.
    - Не показывай SQL код в text_response.
    
    ПРИМЕРЫ:
    
    User: "Привет, ты тут?"
    JSON: {{"sql": "", "text_response": "Здравствуйте! Я Андарз. Готов работать с данными!", "is_sql_needed": false}}

    User: "Сколько учеников в каждой школе?"
    SQL: SELECT sch.name as Школа, COUNT(s.id) as Учеников FROM core_student s JOIN core_schoolclass sc ON s.school_class_id = sc.id JOIN core_school sch ON sc.school_id = sch.id WHERE sch.id IN ({allowed_ids_str}) GROUP BY sch.name ORDER BY Учеников DESC;
    JSON: {{"sql": "...", "text_response": "Вот статистика по школам:", "is_sql_needed": true}}
    
    ТЕКУЩИЙ ЗАПРОС:
    {history_text}
    User: "{user_question}"

    ВЕРНИ ТОЛЬКО JSON:
    {{
        "sql": "SQL_QUERY",
        "text_response": "Текст ответа",
        "is_sql_needed": true/false
    }}
    """

    # 4. ЗАПРОС К API
    try:
        ai_content = _get_ai_response(system_prompt)
    except Exception as e:
        error_msg = str(e)
        if "RATE_LIMIT_EXCEEDED" in error_msg:
            return "⏳ Ох, я перегрелся! Слишком много вопросов подряд. Дайте мне минутку остыть."
        logger.error(f"AI Error: {e}")
        return "Что-то со связью с Google API. Попробуйте позже."

    # 5. ОБРАБОТКА ОТВЕТА
    data = _extract_json(ai_content)
    if not data: 
        return "Не смог разобрать ответ от сервера."
    
    # Если SQL не нужен (просто болтовня)
    if not data.get("is_sql_needed"):
        return data.get("text_response", "Слушаю вас!")

    sql = data.get("sql", "").strip().replace(';', '')
    
    # Проверка безопасности
    if not _is_safe_sql(sql): 
        return "Запрос отклонен системой безопасности."

    # 6. ВЫПОЛНЕНИЕ SQL
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET statement_timeout = 5000;") # Лимит 5 сек на выполнение
            cursor.execute(sql)
            
            if cursor.description:
                columns = [col[0] for col in cursor.description]
                results = cursor.fetchall()
            else:
                return data.get("text_response")

        if not results:
            return f"{data.get('text_response')}<br><br><i>(Данных не найдено)</i>"

        # 7. ГЕНЕРАЦИЯ HTML ТАБЛИЦЫ
        output = f"{data.get('text_response')}<br><br>"
        output += '<div class="overflow-x-auto border rounded-lg"><table class="min-w-full text-sm text-left text-gray-600">'
        
        # Заголовки
        output += '<thead class="bg-gray-50 text-xs uppercase font-semibold text-gray-700"><tr>'
        for col in columns:
             col_name = col.replace('_', ' ').title()
             output += f'<th class="px-4 py-3">{col_name}</th>'
        output += '</tr></thead>'
        
        # Тело таблицы
        output += '<tbody class="divide-y divide-gray-200">'
        for row in results:
            output += '<tr class="hover:bg-gray-50">'
            for val in row:
                output += f'<td class="px-4 py-3">{val if val is not None else "-"}</td>'
            output += '</tr>'
        output += '</tbody></table></div>'

        return output

    except Exception as e:
        logger.error(f"SQL Error: {e}")
        return "Не удалось получить данные из базы (ошибка SQL)."