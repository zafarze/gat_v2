# D:\New_GAT\core\context_processors.py (ПОЛНАЯ ИСПРАВЛЕННАЯ ВЕРСИЯ)

from .models import AcademicYear
# 1. Импортируем кеш Django
from django.core.cache import cache

def archive_years_processor(request):
    """
    Этот процессор добавляет список всех учебных лет и ID выбранного года
    в контекст каждого шаблона.
    (Версия с кешированием)
    """
    selected_year_id = request.GET.get('year')
    
    # 2. Определяем уникальный ключ для нашего кеша
    CACHE_KEY = 'all_archive_years'
    
    # 3. Пытаемся получить данные ИЗ КЕША
    all_archive_years = cache.get(CACHE_KEY)
    
    # 4. Если в кеше ничего нет (all_archive_years is None)...
    if all_archive_years is None:
        # ...тогда делаем запрос к БД (как и раньше)
        all_archive_years = AcademicYear.objects.all().order_by('-name')
        
        # 5. ...и СОХРАНЯЕМ результат в кеш на 1 день (86400 секунд)
        # В следующий раз код возьмет данные из кеша и пропустит этот блок
        cache.set(CACHE_KEY, all_archive_years, 86400) 

    # --- Логика получения selected_year остается без изменений ---
    selected_year = None
    if selected_year_id:
        try:
            selected_year = AcademicYear.objects.get(pk=selected_year_id)
        except AcademicYear.DoesNotExist:
            pass

    return {
        # 6. Передаем в контекст список (из кеша или из БД)
        'all_archive_years': all_archive_years,
        'selected_archive_year': selected_year
    }