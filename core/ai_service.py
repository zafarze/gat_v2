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
# 1. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
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
        logger.error(f"JSON Parse Error. Text received: {text}")
        return None

def _is_safe_sql(sql):
    """
    Блокирует опасные команды, которые могут удалить данные.
    """
    forbidden = [
        'DROP', 'DELETE', 'UPDATE', 'INSERT', 'ALTER', 'TRUNCATE', 
        'GRANT', 'REVOKE', 'CREATE', 'REPLACE', 'EXECUTE', 'pg_sleep',
        'PG_SLEEP', 'WAF'
    ]
    normalized_sql = sql.upper()
    for word in forbidden:
        # Ищем слово как отдельную команду (с границами слов)
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

    response = requests.post(url, headers=headers, json=data, timeout=45) # Таймаут 45 сек
    
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
    Если одна модель занята или не отвечает, пробуем следующую.
    """
    # Список моделей от самой быстрой/умной к старым
    models_to_try = [
        "gemini-2.0-flash-exp",          # Самая новая и быстрая
        "gemini-1.5-flash",              # Стабильная быстрая
        "gemini-1.5-pro",                # Умная, но медленнее
        "gemini-pro"                     # Старая надежная
    ]
    
    last_error = None
    
    for model in models_to_try:
        try:
            return _send_direct_request(model, prompt)
        except Exception as e:
            error_str = str(e)
            if "429_LIMIT" in error_str:
                time.sleep(1.5) # Пауза перед следующей моделью
                continue
            elif "404_NOT_FOUND" in error_str:
                continue
            
            last_error = e
            continue
            
    # Если все модели упали
    logger.critical(f"All AI models failed. Last error: {last_error}")
    raise Exception("AI_SERVICE_UNAVAILABLE")

def _extract_student_info_from_query(query):
    """
    Извлекает информацию об ученике (Имя, ID, Класс, Школа).
    """
    query_lower = query.lower()
    
    # 1. Ищем явный ID (например: "id 1001", "010001", "код 777")
    id_match = re.search(r'\b(id|ид|код|#)?\s*[:\-]?\s*0*(\d{4,})\b', query_lower)
    student_id = None
    if id_match:
        student_id = id_match.group(2)  # Берем цифры без ведущих нулей
    
    # 2. Ищем GAT тест
    gat_match = re.search(r'gat[-\s]*(\d+)', query_lower)
    gat_test = gat_match.group(1) if gat_match else None
    
    # 3. Чистим запрос для извлечения имен
    clean_query = query_lower
    
    # Удаляем ID из запроса если нашли
    if id_match:
        # Удаляем всю конструкцию с ID
        clean_query = re.sub(r'\b(id|ид|код|#)?\s*[:\-]?\s*0*\d{4,}\b', '', clean_query)
    
    # Удаляем GAT из запроса
    if gat_test:
        clean_query = re.sub(r'gat[-\s]*\d+', '', clean_query)
        
    # Список стоп-слов (расширенный)
    stop_words = [
        'найди', 'мне', 'все', 'информации', 'ученик', 'ученика', 'студент',
        'школы', 'класса', 'класс', 'школа', 'и', 'для', 'по', 'из', 'в',
        'составь', 'список', 'покажи', 'выведи', 'топ', 'рейтинг', 'таблицу',
        'результат', 'балл', 'оценки', 'данные', 'id', 'ид', 'код', 'номер',
        'поиск', 'поиска', 'найти', 'найдите', 'запрос', 'запроса', 'карточка',
        'ассистент', 'ai', 'чат', 'диалог', 'режим', 'полный', 'экран'
    ]
    
    # Разбиваем на слова
    words = re.findall(r'\b[а-яёa-z]{2,}\b', clean_query)
    
    # Фильтруем слова (берем только те, что НЕ в стоп-листе)
    potential_names = []
    for w in words:
        if w not in stop_words:
            # Проверяем, что это не название школы или класса
            if not re.match(r'^\d+[а-яa-z]?$', w) and w not in ['мактаби', 'лицей', 'гимназия']:
                potential_names.append(w.capitalize())
    
    first_name = None
    last_name = None
    
    if len(potential_names) >= 2:
        # Берем первые два слова как имя и фамилию
        first_name = potential_names[0]
        last_name = potential_names[1]
    elif len(potential_names) == 1:
        # Если одно слово, считаем его именем
        first_name = potential_names[0]

    # 4. Ищем класс (цифра + буква, например 4Г, 10А)
    class_match = re.search(r'\b([1-9]|10|11)[\s\-]*([А-ЯA-Zа-яa-z]?)\b', query, re.IGNORECASE)
    class_name = None
    if class_match:
        class_digit = class_match.group(1)
        class_letter = class_match.group(2).upper() if class_match.group(2) else ''
        class_name = f"{class_digit}{class_letter}"
    else:
        # Или просто цифра класса, если сказано "10 класс"
        class_digit_match = re.search(r'\b([1-9]|10|11)\s+класс', query_lower)
        if class_digit_match:
            class_name = class_digit_match.group(1)

    # 5. Ищем школу (более гибкий поиск)
    school_name = None
    school_keywords = ['мактаби', 'лицей', 'гимназия', 'школа', 'школе', 'муассисаи']
    
    # Ищем название школы после ключевых слов
    for keyword in school_keywords:
        if keyword in query_lower:
            # Ищем слово/слова после ключевого слова
            pattern = rf'{keyword}[-\s]+([А-Яа-яЁёA-Za-z\s]+?)(?=\s|$)'
            match = re.search(pattern, query_lower)
            if match:
                school_part = match.group(1).strip()
                # Берем из оригинального запроса с сохранением регистра
                start = query.lower().find(keyword + ' ' + school_part)
                if start != -1:
                    end = start + len(keyword + ' ' + school_part)
                    school_name = query[start + len(keyword) + 1:end].strip()
                    break
    
    # Если не нашли через ключевые слова, ищем известные названия школ
    if not school_name:
        known_schools = ['адолат', 'абдураҳмони', 'ҷомӣ', 'ҳоризон', 'ҳамадонӣ', 'камоли', 'хуҷандӣ']
        for school in known_schools:
            if school in query_lower:
                # Находим начало и конец названия в оригинальном запросе
                start = query_lower.find(school)
                # Ищем конец слова (до пробела или конца строки)
                end_match = re.search(rf'{school}[^\s]*', query_lower[start:])
                if end_match:
                    end = start + len(end_match.group())
                    school_name = query[start:end].capitalize()
                    break

    return {
        'id': student_id,          # ID без ведущих нулей
        'first_name': first_name,
        'last_name': last_name,
        'class_name': class_name,
        'school_name': school_name,
        'gat_test': gat_test
    }

def _is_search_query(query):
    """
    Определяет, является ли запрос поисковым.
    """
    query_lower = query.lower().strip()
    
    # Приветствия и общие вопросы
    greetings = ['привет', 'здравствуй', 'здравствуйте', 'добрый день', 'доброе утро', 'добрый вечер']
    if any(query_lower.startswith(g) for g in greetings):
        return False
    
    # Очень короткие запросы
    if len(query_lower.split()) <= 2 and len(query_lower) < 10:
        general_questions = ['как дела', 'кто ты', 'что ты', 'помощь', 'помоги']
        if any(q in query_lower for q in general_questions):
            return False
    
    # Проверяем наличие ключевых слов для поиска
    search_keywords = [
        'найди', 'ищи', 'поиск', 'ученик', 'студент', 'ученика', 
        'школа', 'класс', 'gat', 'гат', 'результат', 'балл', 
        'оценка', 'имя', 'фамилия', 'id', 'айди',
        'топ', 'рейтинг', 'лучшие', 'список', 'отчет', 'статистика',
        'покажи', 'составь', 'выведи', 'какие'
    ]
    
    if any(keyword in query_lower for keyword in search_keywords):
        return True
    
    # Проверяем наличие цифр (возможно ID)
    if re.search(r'\d{4,}', query_lower):
        return True
    
    # Проверяем наличие русских имен (слов с заглавной буквы)
    if re.search(r'\b[А-ЯЁ][а-яё]+\b', query):
        return True
    
    return False


# ==========================================
# 2. МОЗГ АНДАРЗ (ОСНОВНАЯ ЛОГИКА)
# ==========================================

def ask_database(user, user_question, chat_history=None):
    """
    Генерирует SQL запрос, выполняет его и возвращает красивый HTML-ответ.
    """
    
    # --- ШАГ 1: Проверка доступа ---
    allowed_schools_qs = get_accessible_schools(user)
    if not allowed_schools_qs.exists():
        return "😔 У вас пока нет доступа к данным школ. Обратитесь к администратору."
        
    allowed_ids = list(allowed_schools_qs.values_list('id', flat=True))
    allowed_ids_str = ", ".join(map(str, allowed_ids))

    # --- ШАГ 2: Логирование ---
    logger.info(f"User question: {user_question}")
    logger.info(f"Allowed school IDs: {allowed_ids_str}")
    
    student_info = _extract_student_info_from_query(user_question)
    is_search = _is_search_query(user_question)
    
    # Если это не поисковый запрос (приветствие и т.д.)
    if not is_search:
        return "Привет! 👋 Я AI Andarz, ваш аналитик данных GAT. Спросите меня об учениках, школах или результатах тестов!"
    
    # --- ШАГ 3: Подготовка истории ---
    history_text = ""
    if chat_history:
        recent_history = chat_history[-6:] 
        for msg in recent_history:
            role = "User" if msg['role'] == 'user' else "AI"
            clean_text = re.sub('<[^<]+?>', '', str(msg['text']))
            history_text += f"{role}: {clean_text}\n"

    # --- ШАГ 4: ОПРЕДЕЛЕНИЕ СТРАТЕГИИ (Ручной SQL или AI) ---
    sql = None
    text_response = None
    search_type = None
    
    # Ключевые слова, которые требуют работы ИИ (аналитика, списки, топы)
    ai_keywords = [
        'топ', 'рейтинг', 'лучшие', 'худшие', 'средний', 'балл', 
        'статистика', 'количество', 'список', 'отчет', 'анализ',
        'максимальный', 'минимальный', 'общий', 'итог', 'результаты'
    ]
    
    force_ai = any(word in user_question.lower() for word in ai_keywords)
    
    # СТРАТЕГИЯ 1: Если найден ID и он цифровой — ищем строго по нему
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
        text_response = f"🔍 Карточка ученика ID {student_info['id']}:"
        search_type = 'id'
        logger.info(f"Searching by ID: {student_info['id']}")
    
    # СТРАТЕГИЯ 2: Ручной поиск по Имени (ТОЛЬКО если нет сложных слов "Топ", "Список" и т.д.)
    elif not force_ai and (student_info.get('first_name') or student_info.get('last_name')):
        sql = f"""
        SELECT 
            s.id, s.first_name_ru, s.last_name_ru,
            sc.name as class_name, sch.name as school_name
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
        
        if conditions:
            sql += " AND (" + " OR ".join(conditions) + ")"
        
        if student_info['class_name']:
            sql += f" AND sc.name ILIKE '%{student_info['class_name']}%'"
        if student_info['school_name']:
            sql += f" AND sch.name ILIKE '%{student_info['school_name']}%'"
        
        sql += " ORDER BY s.last_name_ru, s.first_name_ru LIMIT 50"
        
        text_response = f"🔍 Поиск: "
        if student_info['first_name']: 
            text_response += f"{student_info['first_name']} "
        if student_info['last_name']: 
            text_response += f"{student_info['last_name']} "
        
        if student_info['class_name']:
            text_response += f", класс {student_info['class_name']}"
        if student_info['school_name']:
            text_response += f", школа {student_info['school_name']}"
            
        search_type = 'name'
        logger.info(f"Manually generated Name SQL: {sql}")
    
    # --- ШАГ 5: Если ручной SQL не сработал (или нужен AI для аналитики) ---
    if not sql:
        system_prompt = f"""
Ты — "AI Andarz", аналитик GAT.

=== СТРУКТУРА БАЗЫ ДАННЫХ ===
1. core_school (id, name, district) - школы
2. core_schoolclass (id, name, school_id) - классы
3. core_student (id, first_name_ru, last_name_ru, school_class_id) - ученики
4. core_studentresult (student_id, total_score) - РЕЗУЛЬТАТЫ GAT (баллы).

=== ВАЖНО ===
1. Если запрос содержит ID ученика (например: "010001"), ищи строго по ID.
2. Для средних баллов ВСЕГДА используй ROUND(AVG(sr.total_score), 1).
3. Для "список класса" выводи: id, first_name_ru, last_name_ru, class_name, school_name.
4. Ищи ТОЛЬКО в школах с ID IN ({allowed_ids_str}).
5. Лимит вывода: 50 строк.

=== ВОПРОС ===
"{user_question}"

=== ЗАДАНИЕ ===
1. Сгенерируй ТОЧНЫЙ SQL запрос для PostgreSQL.
2. Напиши краткий ответ на русском/таджикском.

=== ФОРМАТ ОТВЕТА (JSON) ===
{{
    "sql": "SELECT ...",
    "text_response": "Текст ответа",
    "is_sql_needed": true
}}
"""
        
        try:
            ai_content = _get_ai_response(system_prompt)
            data = _extract_json(ai_content)
            
            if data and data.get("is_sql_needed") and data.get("sql"):
                sql = data.get("sql", "").strip().replace(';', '')
                text_response = data.get("text_response", "Результаты анализа:")
                search_type = 'ai'
            else:
                return data.get("text_response") if data else "🤖 Не удалось понять запрос."
                
        except Exception as e:
            logger.error(f"AI Error: {e}")
            return "📡 Ошибка связи с AI."
    
    # --- ШАГ 6: Выполнение SQL ---
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
            error_message = str(e)
            logger.warning(f"SQL Fail (Try {attempt+1}): {e}")
            
            # Если ошибка в синтаксисе SQL, пробуем упростить запрос
            if attempt == max_retries - 1:
                # Последняя попытка: используем простой запрос
                if student_info.get('first_name') or student_info.get('last_name'):
                    simple_sql = f"""
                    SELECT s.id, s.first_name_ru, s.last_name_ru, 
                           sc.name as class_name, sch.name as school_name
                    FROM core_student s
                    JOIN core_schoolclass sc ON s.school_class_id = sc.id
                    JOIN core_school sch ON sc.school_id = sch.id
                    WHERE sch.id IN ({allowed_ids_str})
                    """
                    
                    if student_info.get('first_name'):
                        simple_sql += f" AND (s.first_name_ru ILIKE '%{student_info['first_name']}%' OR s.last_name_ru ILIKE '%{student_info['first_name']}%')"
                    
                    simple_sql += " LIMIT 20"
                    
                    try:
                        with connection.cursor() as cursor2:
                            cursor2.execute(simple_sql)
                            if cursor2.description:
                                columns = [col[0] for col in cursor2.description]
                                results = cursor2.fetchall()
                            sql = simple_sql
                            break
                    except Exception as e2:
                        return f"😓 Не удалось найти данные.<br><small class='text-red-500'>Ошибка: {e2}</small>"
                else:
                    return f"😓 Ошибка базы данных.<br><small class='text-red-500'>{e}</small>"

    # --- ШАГ 7: Вывод HTML ---
    if not results:
        # Специальное сообщение для поиска по ID, если нет результатов
        if search_type == 'id':
            return f"""
            <div class='p-4 bg-yellow-50 text-yellow-800 rounded-xl border border-yellow-200'>
                <div class='flex items-center gap-3 mb-2'>
                    <span class='text-xl'>🔍</span>
                    <div>
                        <strong>Ученик с ID {student_info['id']} не найден</strong>
                        <p class='text-sm mt-1'>Проверьте правильность ID или попробуйте поиск по имени и фамилии.</p>
                    </div>
                </div>
                <div class='text-sm mt-3'>
                    <strong>Как найти ученика:</strong>
                    <ul class='list-disc pl-5 mt-1'>
                        <li>По ID: <code>ID 010001</code></li>
                        <li>По имени: <code>Амина</code></li>
                        <li>По фамилии: <code>Муродова</code></li>
                        <li>По классу: <code>4Г класс</code></li>
                        <li>По школе: <code>ученики школы Адолат</code></li>
                    </ul>
                </div>
            </div>
            """
        elif columns:
            # Запрос выполнился, но результатов нет (кроме поиска по ID)
            return f"{text_response}<br><br><div class='p-4 bg-yellow-50 text-yellow-800 rounded-xl border border-yellow-200 flex items-center gap-3'><span>🔍</span> По вашему запросу ничего не найдено.</div>"
        else:
            # Нет результатов и нет columns (например, AI вернул только текст)
            return text_response
    elif not results and not columns:
        return text_response

    table_id = f"ai-table-{int(time.time())}"
    
    output = f"<div class='mb-3 font-medium text-slate-700'>{text_response}</div>"
    
    if results:
        output += f'<div class="text-sm text-slate-500 mb-2">Найдено записей: <span class="font-bold">{len(results)}</span></div>'
    
    output += f'<div class="overflow-hidden border border-gray-200 rounded-xl shadow-sm bg-white mt-4 ring-1 ring-black/5">'
    output += f'<div class="overflow-x-auto"><table id="{table_id}" class="min-w-full text-sm text-left">'
    
    output += '<thead class="bg-gray-50/80 border-b border-gray-100 text-xs uppercase font-bold text-gray-500 tracking-wider"><tr>'
    for col in columns:
        col_name = str(col)
        # Русские названия колонок
        if col_name == 'id': col_name = 'ID'
        elif col_name == 'first_name_ru': col_name = 'Имя'
        elif col_name == 'last_name_ru': col_name = 'Фамилия'
        elif col_name == 'class_name': col_name = 'Класс'
        elif col_name == 'school_name': col_name = 'Школа'
        elif 'first_name' in col_name.lower(): col_name = 'Имя'
        elif 'last_name' in col_name.lower(): col_name = 'Фамилия'
        elif 'class' in col_name.lower(): col_name = 'Класс'
        elif 'school' in col_name.lower(): col_name = 'Школа'
        elif 'avg_score' in col_name.lower(): col_name = 'Средний балл'
        elif 'average_score' in col_name.lower(): col_name = 'Средний балл'
        elif 'total_score' in col_name.lower(): col_name = 'Балл'
        else:
            col_name = col_name.replace('_', ' ').replace('ru', '').strip().title()
        
        output += f'<th class="px-6 py-4 whitespace-nowrap text-indigo-900/80">{col_name}</th>'
    output += '</tr></thead>'
    
    output += '<tbody class="divide-y divide-gray-100 bg-white">'
    for i, row in enumerate(results):
        row_class = "bg-white hover:bg-indigo-50/60 transition-colors" if i % 2 == 0 else "bg-gray-50/50 hover:bg-indigo-50/60 transition-colors"
        output += f'<tr class="{row_class}">'
        for j, val in enumerate(row):
            display_val = val
            if val is None: 
                display_val = '-'
            elif isinstance(val, float): 
                display_val = round(val, 1)
            elif isinstance(val, int):
                # ID оставляем как есть
                display_val = str(val)
            
            output += f'<td class="px-6 py-4 font-medium text-gray-700">{display_val}</td>'
        output += '</tr>'
    output += '</tbody></table></div></div>'

    # Кнопка скачивания (только если есть результаты)
    if results:
        output += f'''
        <div class="mt-4 flex justify-end">
            <button onclick="downloadCSV('{table_id}')" class="group flex items-center gap-2 px-4 py-2 bg-white text-green-600 border border-green-200 rounded-xl hover:bg-green-50 hover:border-green-300 transition-all text-xs font-bold shadow-sm">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"></path></svg>
                <span>Скачать Excel (CSV)</span>
            </button>
        </div>
        
        <div class="mt-2 text-xs text-gray-500">
            <strong>Совет:</strong> Для поиска конкретного ученика попробуйте запросы:
            <ul class="list-disc pl-5 mt-1">
                <li>"Амина" (поиск по имени)</li>
                <li>"Муродова" (поиск по фамилии)</li>
                <li>"ID 010001" (поиск по ID)</li>
                <li>"4Г" (поиск по классу)</li>
            </ul>
        </div>
        '''

    return output