# D:\Project Archive\GAT\core\ai_service.py

import json
import logging
import google.generativeai as genai
from django.conf import settings
from django.db import connection
from .views.permissions import get_accessible_schools

logger = logging.getLogger(__name__)

# Настройка API
try:
    genai.configure(api_key=settings.OPENAI_API_KEY)
except Exception as e:
    logger.error(f"Google AI Config Error: {e}")

def ask_database(user, user_question):
    """
    AI Andarz: Версия для gemini-flash-latest.
    """
    
    # 1. ПРОВЕРКА ПРАВ
    allowed_schools_qs = get_accessible_schools(user)
    if not allowed_schools_qs.exists():
        return "У вас пока нет доступа ни к одной школе."
        
    allowed_ids = list(allowed_schools_qs.values_list('id', flat=True))
    allowed_ids_str = ", ".join(map(str, allowed_ids))

    # 2. СХЕМА БД
    db_schema = """
    Схема PostgreSQL:
    1. core_school (id, name, city)
    2. core_schoolclass (id, name, school_id)
    3. core_student (id, first_name_ru, last_name_ru, school_class_id) 
       ! ВНИМАНИЕ: У студента НЕТ поля school_id. Связь: student -> school_class -> school.
    4. core_gattest (id, name, date)
    5. core_studentresult (id, total_score, student_id, gat_test_id)
    """

    # 3. ПРОМПТ
    system_prompt = f"""
    Ты — AI Andarz. Отвечай ТОЛЬКО в формате JSON.
    
    ПРАВИЛА БЕЗОПАСНОСТИ:
    1. Доступные школы ID: [{allowed_ids_str}].
    2. Если вопрос про школу не из списка -> JSON "ACCESS_DENIED".
    
    ПРАВИЛА SQL:
    1. Для фильтрации учеников используй JOIN:
       `JOIN core_schoolclass sc ON core_student.school_class_id = sc.id`
       `WHERE sc.school_id IN ({allowed_ids_str})`
    2. НЕ ПРИДУМЫВАЙ поле core_student.school_id.
    
    ФОРМАТ ОТВЕТА (JSON):
    {{
        "sql": "SELECT ...",
        "text_response": "Текст ответа",
        "is_sql_needed": true/false
    }}
    """

    full_prompt = f"{system_prompt}\n\nВОПРОС: \"{user_question}\""

    # 4. ЗАПРОС К AI
    try:
        # ИСПОЛЬЗУЕМ gemini-flash-latest - это имя было в твоем списке доступных моделей
        model = genai.GenerativeModel('gemini-flash-latest')
        
        response = model.generate_content(full_prompt)
        ai_content = response.text
        
    except Exception as e:
        error_msg = str(e)
        # Обработка перегрузки лимитов
        if "429" in error_msg:
            return "⏳ Лимит запросов Google исчерпан. Пожалуйста, подождите 30 секунд."
        if "404" in error_msg:
            return f"❌ Ошибка модели: Google не видит 'gemini-flash-latest'. Попробуйте обновить библиотеку."
        return f"Ошибка AI: {error_msg}"

    # 5. ОБРАБОТКА
    try:
        cleaned = ai_content.replace('```json', '').replace('```', '').strip()
        data = json.loads(cleaned)
        
        if data.get("text_response") == "ACCESS_DENIED":
            return "🔒 Нет доступа к этой школе."

        if not data.get("is_sql_needed"):
            return data.get("text_response")

        sql = data.get("sql")
        
        with connection.cursor() as cursor:
            cursor.execute(sql)
            results = cursor.fetchall()
            
        if not results:
            return "Данных не найдено."
            
        text = f"{data.get('text_response')}\n\n"
        for row in results[:15]:
            row_items = [str(item) if item is not None else "-" for item in row]
            text += " • " + " | ".join(row_items) + "\n"
            
        return text

    except Exception as e:
        logger.error(f"AI Logic Error: {e}")
        return "Не удалось обработать ответ AI. Попробуйте еще раз."