# D:\Project Archive\GAT\core\ai_service.py

import json
import logging
import re
import time
import requests
from django.conf import settings
from django.db import connection
from .views.permissions import get_accessible_schools

logger = logging.getLogger(__name__)

# ==========================================
# 1. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ (ТВОИ, ПОЛНЫЕ)
# ==========================================

def _extract_json(text):
    """
    Надежно вытаскивает JSON из ответа AI, даже если там есть лишний текст.
    """
    try:
        # Ищем контент между первыми { и последними }
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        return json.loads(text)
    except json.JSONDecodeError:
        # Если AI ответил просто текстом (без JSON), считаем это ответом чата
        return {"sql": None, "text_response": text, "is_sql_needed": False}

def _is_safe_sql(sql):
    """
    Блокирует опасные команды.
    """
    if not sql: return True
    forbidden = [
        'DROP', 'DELETE', 'UPDATE', 'INSERT', 'ALTER', 'TRUNCATE', 
        'GRANT', 'REVOKE', 'CREATE', 'REPLACE', 'EXECUTE', 'pg_sleep',
        'PG_SLEEP', 'WAF'
    ]
    normalized_sql = sql.upper()
    for word in forbidden:
        if re.search(r'\b' + word + r'\b', normalized_sql):
            logger.warning(f"SQL Injection blocked: {word} found in {sql}")
            return False
    return True

def _send_direct_request(model_name, prompt):
    """
    Отправляет запрос к Google API.
    """
    api_key = settings.GOOGLE_API_KEY
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    
    headers = {'Content-Type': 'application/json'}
    data = {"contents": [{"parts": [{"text": prompt}]}]}

    response = requests.post(url, headers=headers, json=data, timeout=45)
    
    if response.status_code == 200:
        result = response.json()
        try:
            return result['candidates'][0]['content']['parts'][0]['text']
        except (KeyError, IndexError):
            return ""
    elif response.status_code == 429:
        raise Exception("429_LIMIT")
    elif response.status_code == 404:
        raise Exception(f"404_NOT_FOUND (Model {model_name})")
    else:
        raise Exception(f"HTTP {response.status_code}: {response.text}")

def _get_ai_response(prompt):
    """
    Умный перебор моделей (Failover system).
    """
    models_to_try = [
        "gemini-2.0-flash-exp",          # Самая новая
        "gemini-1.5-flash",              # Стабильная
        "gemini-1.5-pro",                # Умная
        "gemini-pro"                     # Старая
    ]
    
    last_error = None
    
    for model in models_to_try:
        try:
            return _send_direct_request(model, prompt)
        except Exception as e:
            error_str = str(e)
            if "429_LIMIT" in error_str:
                time.sleep(1.5)
                continue
            elif "404_NOT_FOUND" in error_str:
                continue
            last_error = e
            continue
            
    logger.critical(f"All AI models failed. Last error: {last_error}")
    raise Exception("AI_SERVICE_UNAVAILABLE")

def _extract_student_info_from_query(query):
    """
    Полная версия твоего парсера (с регулярками).
    """
    query_lower = query.lower()
    
    # 1. Ищем ID
    id_match = re.search(r'\b(id|ид|код|#)?\s*[:\-]?\s*0*(\d{4,})\b', query_lower)
    student_id = id_match.group(2) if id_match else None
    
    # 2. Ищем GAT тест
    gat_match = re.search(r'gat[-\s]*(\d+)', query_lower)
    gat_test = gat_match.group(1) if gat_match else None
    
    # 3. Чистим запрос
    clean_query = query_lower
    if id_match:
        clean_query = re.sub(r'\b(id|ид|код|#)?\s*[:\-]?\s*0*\d{4,}\b', '', clean_query)
    if gat_test:
        clean_query = re.sub(r'gat[-\s]*\d+', '', clean_query)
        
    stop_words = [
        'найди', 'мне', 'все', 'информации', 'ученик', 'ученика', 'студент',
        'школы', 'класса', 'класс', 'школа', 'и', 'для', 'по', 'из', 'в',
        'составь', 'список', 'покажи', 'выведи', 'топ', 'рейтинг', 'таблицу',
        'результат', 'балл', 'оценки', 'данные', 'id', 'ид', 'код', 'номер',
        'поиск', 'поиска', 'найти', 'найдите', 'запрос', 'запроса', 'карточка',
        'ассистент', 'ai', 'чат', 'диалог', 'режим', 'полный', 'экран',
        'как', 'ты', 'посчитал', 'почему', 'объясни', 'привет'
    ]
    
    words = re.findall(r'\b[а-яёa-z]{2,}\b', clean_query)
    potential_names = []
    for w in words:
        if w not in stop_words:
            # Исключаем цифры и короткие слова
            if not re.match(r'^\d+[а-яa-z]?$', w) and w not in ['мактаби', 'лицей', 'гимназия']:
                potential_names.append(w.capitalize())
    
    first_name = potential_names[0] if len(potential_names) >= 1 else None
    last_name = potential_names[1] if len(potential_names) >= 2 else None

    # 4. Ищем класс
    class_match = re.search(r'\b([1-9]|10|11)[\s\-]*([А-ЯA-Zа-яa-z]?)\b', query, re.IGNORECASE)
    class_name = None
    if class_match:
        class_digit = class_match.group(1)
        class_letter = class_match.group(2).upper() if class_match.group(2) else ''
        class_name = f"{class_digit}{class_letter}"
    else:
        class_digit_match = re.search(r'\b([1-9]|10|11)\s+класс', query_lower)
        if class_digit_match:
            class_name = class_digit_match.group(1)

    # 5. Ищем школу
    school_name = None
    school_keywords = ['мактаби', 'лицей', 'гимназия', 'школа', 'школе', 'муассисаи']
    for keyword in school_keywords:
        if keyword in query_lower:
            pattern = rf'{keyword}[-\s]+([А-Яа-яЁёA-Za-z\s]+?)(?=\s|$)'
            match = re.search(pattern, query_lower)
            if match:
                school_part = match.group(1).strip()
                start = query.lower().find(keyword + ' ' + school_part)
                if start != -1:
                    end = start + len(keyword + ' ' + school_part)
                    school_name = query[start + len(keyword) + 1:end].strip()
                    break
    
    if not school_name:
        known_schools = ['адолат', 'абдураҳмони', 'ҷомӣ', 'ҳоризон', 'ҳамадонӣ', 'камоли', 'хуҷандӣ']
        for school in known_schools:
            if school in query_lower:
                start = query_lower.find(school)
                end_match = re.search(rf'{school}[^\s]*', query_lower[start:])
                if end_match:
                    end = start + len(end_match.group())
                    school_name = query[start:end].capitalize()
                    break

    return {
        'id': student_id,
        'first_name': first_name,
        'last_name': last_name,
        'class_name': class_name,
        'school_name': school_name,
        'gat_test': gat_test
    }

def _is_search_query(query):
    """
    Определяет, является ли запрос поисковым или это просто болтовня.
    """
    query_lower = query.lower().strip()
    
    # Явные слова для поиска
    search_keywords = [
        'найди', 'ищи', 'поиск', 'ученик', 'студент', 'ученика', 
        'школа', 'класс', 'gat', 'гат', 'результат', 'балл', 
        'оценка', 'имя', 'фамилия', 'id', 'айди',
        'топ', 'рейтинг', 'лучшие', 'список', 'отчет', 'статистика',
        'покажи', 'составь', 'выведи', 'какие'
    ]
    
    # Если есть ключевые слова или цифры (ID) - это поиск
    if any(keyword in query_lower for keyword in search_keywords):
        return True
    if re.search(r'\d{4,}', query_lower):
        return True
    if re.search(r'\b[А-ЯЁ][а-яё]+\b', query): # Русские имена с большой буквы
        return True
        
    return False


# ==========================================
# 2. МОЗГ АНДАРЗ (ОСНОВНАЯ ЛОГИКА)
# ==========================================

def ask_database(user, user_question, chat_history=None):
    """
    Генерирует SQL запрос или текстовый ответ.
    """
    
    # --- ШАГ 1: Проверка доступа ---
    allowed_schools_qs = get_accessible_schools(user)
    if not allowed_schools_qs.exists():
        return "😔 У вас пока нет доступа к данным школ. Обратитесь к администратору."
        
    allowed_ids = list(allowed_schools_qs.values_list('id', flat=True))
    allowed_ids_str = ", ".join(map(str, allowed_ids))

    logger.info(f"User question: {user_question}")
    
    student_info = _extract_student_info_from_query(user_question)
    
    # --- ШАГ 2: Подготовка истории (для контекста ИИ) ---
    history_text = ""
    if chat_history:
        recent_history = chat_history[-4:]  # Помним последние 4 сообщения
        for msg in recent_history:
            role = "User" if msg['role'] == 'user' else "AI"
            clean_text = re.sub('<[^<]+?>', '', str(msg['text']))[:300] # Обрезаем длинные ответы
            history_text += f"{role}: {clean_text}\n"

    # --- ШАГ 3: ОПРЕДЕЛЕНИЕ СТРАТЕГИИ ---
    
    sql = None
    text_response = None
    search_type = None
    
    # СТРАТЕГИЯ 1: Если найден ID и он цифровой — ищем строго по нему (Твой надежный код)
    if student_info.get('id') and student_info['id'].isdigit():
        sql = f"""
        SELECT 
            s.id, s.first_name_ru, s.last_name_ru,
            sc.name as class_name, sch.name as school_name,
            COALESCE(ROUND(AVG(sr.total_score), 1), 0) as avg_score
        FROM core_student s
        JOIN core_schoolclass sc ON s.school_class_id = sc.id
        JOIN core_school sch ON sc.school_id = sch.id
        LEFT JOIN core_studentresult sr ON s.id = sr.student_id
        WHERE s.id = {int(student_info['id'])} AND sch.id IN ({allowed_ids_str})
        GROUP BY s.id, s.first_name_ru, s.last_name_ru, sc.name, sch.name
        LIMIT 1
        """
        text_response = f"👤 Карточка ученика ID {student_info['id']}:"
        search_type = 'id'
    
    # СТРАТЕГИЯ 2: Ручной поиск по Имени (если это похоже на простой поиск)
    # Используем твой код, но добавляем проверку, не задает ли юзер сложный вопрос
    elif _is_search_query(user_question) and (student_info.get('first_name') or student_info.get('last_name')):
        # Если юзер просит "топ" или "анализ", пропускаем этот блок и идем к AI
        ai_keywords = ['топ', 'рейтинг', 'лучшие', 'худшие', 'средний', 'анализ', 'как', 'почему']
        if not any(k in user_question.lower() for k in ai_keywords):
            sql = f"""
            SELECT s.id, s.first_name_ru, s.last_name_ru, sc.name as class_name, sch.name as school_name
            FROM core_student s
            JOIN core_schoolclass sc ON s.school_class_id = sc.id
            JOIN core_school sch ON sc.school_id = sch.id
            WHERE sch.id IN ({allowed_ids_str})
            """
            conditions = []
            if student_info['first_name']:
                conditions.append(f"(s.first_name_ru ILIKE '%{student_info['first_name']}%' OR s.last_name_ru ILIKE '%{student_info['first_name']}%')")
            if student_info['last_name']:
                conditions.append(f"(s.first_name_ru ILIKE '%{student_info['last_name']}%' OR s.last_name_ru ILIKE '%{student_info['last_name']}%')")
            
            if conditions: sql += " AND (" + " OR ".join(conditions) + ")"
            if student_info['class_name']: sql += f" AND sc.name ILIKE '%{student_info['class_name']}%'"
            if student_info['school_name']: sql += f" AND sch.name ILIKE '%{student_info['school_name']}%'"
            
            sql += " ORDER BY s.last_name_ru, s.first_name_ru LIMIT 50"
            text_response = f"🔍 Результаты поиска:"
            search_type = 'name'
    
    # --- ШАГ 4: AI ЗАПРОС (Если ручной поиск не сработал или это чат) ---
    if not sql:
        system_prompt = f"""
Ты — "AI Andarz", дружелюбный аналитик GAT.

=== ТВОЯ ЛИЧНОСТЬ ===
1. Будь вежливым, используй смайлики (😊, 📊, 👋).
2. Если это просто приветствие или болтовня ("Как дела?", "Кто ты?") -> Отвечай текстом, SQL не нужен.
3. Если пользователь спрашивает ОБЪЯСНЕНИЕ ("Как ты посчитал?", "Почему так?") -> Используй ИСТОРИЮ ЧАТА ниже, объясни логику текстом.
4. Если это ЗАПРОС ДАННЫХ ("Топ школ", "Оценки Амины") -> Генерируй SQL.

=== СТРУКТУРА БАЗЫ ===
1. core_school (id, name, district)
2. core_schoolclass (id, name, school_id)
3. core_student (id, first_name_ru, last_name_ru, school_class_id)
4. core_gattest (id, name, test_number)
5. core_studentresult (student_id, gat_test_id, total_score, scores_by_subject JSONB)

=== ИСТОРИЯ ЧАТА ===
{history_text}

=== УСЛОВИЯ ===
- Ищи ТОЛЬКО в школах ID IN ({allowed_ids_str}).
- Для рейтингов используй RANK() или ORDER BY total_score DESC.

=== ВОПРОС ПОЛЬЗОВАТЕЛЯ ===
"{user_question}"

=== ФОРМАТ ОТВЕТА (JSON) ===
{{
    "sql": "SELECT ... или null",
    "text_response": "Текст твоего ответа (дружелюбный)",
    "is_sql_needed": true/false
}}
"""
        try:
            ai_content = _get_ai_response(system_prompt)
            data = _extract_json(ai_content)
            
            # Если AI вернул ответ
            if data:
                # Если AI решил, что SQL не нужен (просто чат)
                if not data.get("is_sql_needed") or not data.get("sql"):
                    return data.get("text_response", "Я здесь! 😊 Чем могу помочь с данными?")
                
                # Если AI сгенерировал SQL
                sql = data.get("sql", "").strip().replace(';', '')
                text_response = data.get("text_response", "Вот что я нашел 📊:")
                search_type = 'ai'
            else:
                return "🤖 Не удалось понять запрос. Попробуйте переформулировать."
                
        except Exception as e:
            logger.error(f"AI Error: {e}")
            return "📡 Проблемы со связью с моим мозгом 🤯. Попробуйте позже."
    
    # --- ШАГ 5: Выполнение SQL (Твой оригинальный код с ретраями) ---
    logger.info(f"Executing SQL: {sql}")
    max_retries = 2
    columns = []
    results = []
    
    for attempt in range(max_retries):
        if not _is_safe_sql(sql):
            return "🚫 Запрос отклонен системой безопасности."

        try:
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = 8000;") 
                cursor.execute(sql)
                if cursor.description:
                    columns = [col[0] for col in cursor.description]
                    results = cursor.fetchall()
                break 
        except Exception as e:
            logger.warning(f"SQL Fail (Try {attempt+1}): {e}")
            if attempt == max_retries - 1:
                return f"😓 Ошибка базы данных.<br><small class='text-red-500'>{e}</small>"

    # --- ШАГ 6: Генерация HTML (Твой код таблиц + Кнопка скачивания) ---
    if not results and not columns:
        return text_response # Просто текст (если SQL был, но пустой или AI передумал)

    if not results:
         # Специальный блок "Ничего не найдено"
        return f"{text_response}<br><br><div class='p-4 bg-yellow-50 text-yellow-800 rounded-xl border border-yellow-200 flex items-center gap-3'><span>🔍</span> По вашему запросу ничего не найдено.</div>"

    table_id = f"ai-table-{int(time.time())}"
    
    output = f"<div class='mb-3 font-medium text-slate-700'>{text_response}</div>"
    output += f'<div class="text-sm text-slate-500 mb-2">Найдено записей: <span class="font-bold">{len(results)}</span></div>'
    output += f'<div class="overflow-hidden border border-gray-200 rounded-xl shadow-sm bg-white mt-4 ring-1 ring-black/5">'
    output += f'<div class="overflow-x-auto"><table id="{table_id}" class="min-w-full text-sm text-left">'
    
    # Заголовки
    output += '<thead class="bg-gray-50/80 border-b border-gray-100 text-xs uppercase font-bold text-gray-500 tracking-wider"><tr>'
    for col in columns:
        col_name = str(col).replace('_', ' ').replace('ru', '').strip().title()
        if 'Total' in col_name or 'Score' in col_name: col_name = 'Балл 📊'
        output += f'<th class="px-6 py-4 whitespace-nowrap text-indigo-900/80">{col_name}</th>'
    output += '</tr></thead>'
    
    # Строки
    output += '<tbody class="divide-y divide-gray-100 bg-white">'
    for i, row in enumerate(results):
        row_class = "bg-white hover:bg-indigo-50/60 transition-colors" if i % 2 == 0 else "bg-gray-50/50 hover:bg-indigo-50/60 transition-colors"
        output += f'<tr class="{row_class}">'
        for val in row:
            display_val = val if val is not None else '-'
            if isinstance(val, float): display_val = round(val, 1)
            output += f'<td class="px-6 py-4 font-medium text-gray-700">{display_val}</td>'
        output += '</tr>'
    output += '</tbody></table></div></div>'

    # Кнопка скачивания
    if results:
        output += f'''
        <div class="mt-4 flex justify-end">
            <button onclick="downloadCSV('{table_id}')" class="group flex items-center gap-2 px-4 py-2 bg-white text-green-600 border border-green-200 rounded-xl hover:bg-green-50 hover:border-green-300 transition-all text-xs font-bold shadow-sm">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"></path></svg>
                <span>Скачать CSV</span>
            </button>
        </div>
        '''

    return output