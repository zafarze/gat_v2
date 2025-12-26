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
# 1. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ (ПОЛНЫЕ ВЕРСИИ)
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
        logger.warning(f"JSON Parse Warning. Text received: {text}")
        # Если это просто текст, возвращаем структуру для чата
        return {"sql": None, "text_response": text, "is_sql_needed": False}

def _is_safe_sql(sql):
    """
    Блокирует опасные команды, которые могут удалить данные.
    """
    if not sql: return True
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
    (ТВОЯ ПОЛНАЯ ВЕРСИЯ С REGEX)
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
        clean_query = re.sub(r'\b(id|ид|код|#)?\s*[:\-]?\s*0*\d{4,}\b', '', clean_query)
    
    # Удаляем GAT из запроса
    if gat_test:
        clean_query = re.sub(r'gat[-\s]*\d+', '', clean_query)
        
    # Список стоп-слов (ТВОЙ ПОЛНЫЙ СПИСОК)
    stop_words = [
        'найди', 'мне', 'все', 'информации', 'ученик', 'ученика', 'студент',
        'школы', 'класса', 'класс', 'школа', 'и', 'для', 'по', 'из', 'в',
        'составь', 'список', 'покажи', 'выведи', 'топ', 'рейтинг', 'таблицу',
        'результат', 'балл', 'оценки', 'данные', 'id', 'ид', 'код', 'номер',
        'поиск', 'поиска', 'найти', 'найдите', 'запрос', 'запроса', 'карточка',
        'ассистент', 'ai', 'чат', 'диалог', 'режим', 'полный', 'экран',
        'как', 'почему', 'ты', 'посчитал', 'объясни'
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

    # 5. Ищем школу (ТВОЯ ПОЛНАЯ ЛОГИКА)
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
# 2. НОВЫЙ МОДУЛЬ: BEAUTIFIER (КРАСИВЫЙ HTML)
# ==========================================

def _format_value_smart(val, col_name):
    """
    Превращает данные (включая JSON) в красивый HTML.
    Добавлена для улучшения визуализации.
    """
    if val is None:
        return '<span class="text-gray-300">-</span>'

    col_lower = col_name.lower()

    # --- 1. ОБРАБОТКА JSON (Progress Bar) ---
    # Проверяем, похоже ли это на JSON результатов
    if isinstance(val, (dict, list)) or (isinstance(val, str) and val.strip().startswith('{')):
        try:
            # Если строка - парсим
            data = val if isinstance(val, (dict, list)) else json.loads(val)
            
            # Функция рекурсивного подсчета true/false
            def count_bools(obj):
                c, t = 0, 0
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        sc, st = count_bools(v)
                        c += sc; t += st
                elif isinstance(obj, list):
                    for v in obj:
                        sc, st = count_bools(v)
                        c += sc; t += st
                elif isinstance(obj, bool):
                    t = 1
                    if obj: c = 1
                return c, t

            correct_q, total_q = count_bools(data)

            if total_q > 0:
                percent = int((correct_q / total_q) * 100)
                # Цвет прогресс-бара
                color = "bg-emerald-500" if percent >= 80 else "bg-amber-400" if percent >= 50 else "bg-rose-500"
                text_color = "text-emerald-700" if percent >= 80 else "text-amber-700" if percent >= 50 else "text-rose-700"
                
                return f'''
                <div class="w-full min-w-[140px]">
                    <div class="flex justify-between items-end mb-1">
                        <span class="font-bold {text_color} text-xs">{percent}%</span>
                        <span class="text-[10px] text-gray-400 font-medium">{correct_q} из {total_q}</span>
                    </div>
                    <div class="w-full bg-gray-100 rounded-full h-1.5 overflow-hidden">
                        <div class="{color} h-1.5 rounded-full transition-all duration-500" style="width: {percent}%"></div>
                    </div>
                </div>
                '''
            else:
                # Если JSON пустой или структура другая
                return '<span class="text-[10px] text-gray-400 font-mono" title="No Data">Empty Data</span>'
                
        except Exception:
            # Если не смогли распарсить - возвращаем сокращенный текст
            return f'<span class="text-xs text-gray-400 font-mono truncate max-w-[150px] block">{str(val)}</span>'

    # --- 2. РЕЙТИНГИ (Медали) ---
    if 'rank' in col_lower or 'место' in col_lower or 'place' in col_lower:
        if val == 1: return f'<span class="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-bold bg-yellow-50 text-yellow-700 border border-yellow-100">🥇 1-е</span>'
        if val == 2: return f'<span class="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-bold bg-gray-50 text-gray-600 border border-gray-200">🥈 2-е</span>'
        if val == 3: return f'<span class="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-bold bg-orange-50 text-orange-700 border border-orange-100">🥉 3-е</span>'
        return f'<span class="font-bold text-gray-400 text-xs">#{val}</span>'

    # --- 3. БАЛЛЫ (Цветные числа) ---
    if isinstance(val, (int, float)) and ('score' in col_lower or 'балл' in col_lower or 'avg' in col_lower):
        formatted = round(val, 1)
        text_color = "text-emerald-600" if formatted >= 80 else "text-indigo-600" if formatted >= 50 else "text-rose-600"
        return f'<span class="font-extrabold {text_color} text-sm">{formatted}</span>'

    # --- 4. ИМЕНА И КЛАССЫ (Жирный шрифт) ---
    if isinstance(val, str) and ('name' in col_lower or 'имя' in col_lower or 'школа' in col_lower or 'класс' in col_lower):
         return f'<span class="font-semibold text-gray-800">{val}</span>'

    # Стандартный вывод
    return str(val)


# ==========================================
# 3. ОСНОВНАЯ ЛОГИКА (ASK DATABASE)
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

    logger.info(f"User question: {user_question}")
    
    student_info = _extract_student_info_from_query(user_question)
    is_search = _is_search_query(user_question)
    
    # --- ШАГ 2: Подготовка истории чата (для контекста "Как ты посчитал?") ---
    history_text = ""
    if chat_history:
        recent_history = chat_history[-4:] 
        for msg in recent_history:
            role = "User" if msg['role'] == 'user' else "AI"
            clean_text = re.sub('<[^<]+?>', '', str(msg['text']))[:300]
            history_text += f"{role}: {clean_text}\n"

    # --- ШАГ 3: ОПРЕДЕЛЕНИЕ СТРАТЕГИИ ---
    sql = None
    text_response = None
    search_type = None
    
    # Ключевые слова, требующие AI
    ai_keywords = ['топ', 'рейтинг', 'лучшие', 'худшие', 'средний', 'анализ', 'количество', 'список', 'почему', 'как', 'объясни']
    has_gat_request = student_info.get('gat_test') is not None
    force_ai = any(word in user_question.lower() for word in ai_keywords) or has_gat_request or not is_search
    
    # СТРАТЕГИЯ 1: Если найден ID и он цифровой — ищем строго по нему (БЫСТРЫЙ ПУТЬ)
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
    
    # СТРАТЕГИЯ 2: Ручной поиск по Имени (ЕСЛИ НЕТ СЛОЖНЫХ СЛОВ)
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
        
        if conditions: sql += " AND (" + " OR ".join(conditions) + ")"
        if student_info['class_name']: sql += f" AND sc.name ILIKE '%{student_info['class_name']}%'"
        if student_info['school_name']: sql += f" AND sch.name ILIKE '%{student_info['school_name']}%'"
        
        sql += " ORDER BY s.last_name_ru, s.first_name_ru LIMIT 50"
        text_response = f"🔍 Результаты поиска:"
        search_type = 'name'
    
    # --- ШАГ 4: AI СТРАТЕГИЯ (ЕСЛИ СЛОЖНЫЙ ВОПРОС ИЛИ ЧАТ) ---
    if not sql:
        system_prompt = f"""
Ты — "AI Andarz", дружелюбный аналитик GAT.

=== ЛИЧНОСТЬ ===
1. Будь вежливым, используй смайлики (😊, 📊, 🚀).
2. Если это просто общение ("Привет", "Как дела?") или вопрос "Как ты посчитал?" -> Отвечай текстом (is_sql_needed: false).
3. Если это запрос данных -> Генерируй SQL.

=== БД ===
1. core_school (id, name, district)
2. core_schoolclass (id, name, school_id)
3. core_student (id, first_name_ru, last_name_ru, school_class_id)
4. core_gattest (id, name, test_number)
5. core_studentresult (student_id, gat_test_id, total_score, scores_by_subject JSONB)

=== ИСТОРИЯ ЧАТА ===
{history_text}

=== ЗАДАНИЕ ===
Вопрос: "{user_question}"
- Ищи ТОЛЬКО в школах ({allowed_ids_str}).
- JSON поле scores_by_subject содержит ключи-ID. Используй jsonb_each_text.

=== ФОРМАТ ОТВЕТА (JSON) ===
{{
    "sql": "SELECT ... или null",
    "text_response": "Текст...",
    "is_sql_needed": true/false
}}
"""
        try:
            ai_content = _get_ai_response(system_prompt)
            data = _extract_json(ai_content)
            
            # Если AI решил просто поболтать
            if not data.get("is_sql_needed") or not data.get("sql"):
                return data.get("text_response", "Я здесь! 😊 Чем могу помочь с данными?")
            
            # Если AI дал SQL
            sql = data.get("sql", "").strip().replace(';', '')
            text_response = data.get("text_response", "Вот что я нашел 📊:")
            search_type = 'ai'
            
        except Exception as e:
            logger.error(f"AI Error: {e}")
            return "📡 Ошибка связи с AI."
    
    # --- ШАГ 5: ВЫПОЛНЕНИЕ SQL И РЕНДЕР ---
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
                # Если AI запрос упал, а это был простой поиск, можно попробовать фоллбек (опционально)
                return f"😓 Ошибка базы данных.<br><small class='text-red-500'>{e}</small>"

    # --- ШАГ 6: ГЕНЕРАЦИЯ КРАСИВОГО HTML (С ИСПОЛЬЗОВАНИЕМ НОВОЙ ФУНКЦИИ) ---
    if not results and not columns:
        return text_response

    if not results:
        return f"{text_response}<br><br><div class='p-4 bg-yellow-50 text-yellow-800 rounded-xl border border-yellow-200 flex items-center gap-3'><span>🔍</span> По вашему запросу ничего не найдено.</div>"

    table_id = f"ai-table-{int(time.time())}"
    
    output = f"<div class='mb-4 text-slate-700 leading-relaxed font-medium'>{text_response}</div>"
    
    output += f'<div class="overflow-hidden border border-gray-200 rounded-xl shadow-sm bg-white mt-2 ring-1 ring-black/5">'
    output += f'<div class="overflow-x-auto"><table id="{table_id}" class="min-w-full text-sm text-left">'
    
    # Шапка
    output += '<thead class="bg-gray-50/90 border-b border-gray-200 text-[11px] uppercase font-bold text-gray-500 tracking-wider"><tr>'
    for col in columns:
        col_name = str(col).replace('_', ' ').replace('ru', '').strip().title()
        if 'First Name' in col_name or 'Last Name' in col_name: col_name = 'Ученик'
        if 'Class Name' in col_name: col_name = 'Класс'
        if 'School Name' in col_name: col_name = 'Школа'
        if 'Total Score' in col_name or 'Avg Score' in col_name: col_name = 'Балл'
        
        output += f'<th class="px-6 py-4 whitespace-nowrap text-indigo-900/80">{col_name}</th>'
    output += '</tr></thead>'
    
    # Тело таблицы (С КРАСИВЫМ ФОРМАТИРОВАНИЕМ)
    output += '<tbody class="divide-y divide-gray-100 bg-white">'
    for i, row in enumerate(results):
        row_class = "bg-white hover:bg-indigo-50/40 transition-colors" if i % 2 == 0 else "bg-slate-50/50 hover:bg-indigo-50/40 transition-colors"
        output += f'<tr class="{row_class}">'
        for j, val in enumerate(row):
            # 🔥 ВОТ ГЛАВНОЕ ИЗМЕНЕНИЕ: ВЫЗОВ BEAUTIFIER 🔥
            formatted_html = _format_value_smart(val, columns[j])
            output += f'<td class="px-6 py-3 text-gray-700 align-middle">{formatted_html}</td>'
        output += '</tr>'
    output += '</tbody></table></div></div>'

    # Кнопка скачивания
    if results:
        output += f'''
        <div class="mt-3 flex justify-end">
            <button onclick="downloadCSV('{table_id}')" class="group flex items-center gap-2 px-3 py-1.5 bg-white text-emerald-600 border border-emerald-200 rounded-lg hover:bg-emerald-50 hover:border-emerald-300 transition-all text-xs font-bold shadow-sm">
                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"></path></svg>
                <span>Скачать CSV</span>
            </button>
        </div>
        '''

    return output