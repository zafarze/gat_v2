# D:\New_GAT\core\templatetags\custom_filters.py

from django import template

register = template.Library()

@register.filter(name='get_item')
def get_item(dictionary, key):
    # СНАЧАЛА ПРОВЕРЯЕМ, ЯВЛЯЕТСЯ ЛИ 'dictionary' СЛОВАРЕМ.
    # Если это None или что-то другое, ошибка не возникнет.
    if isinstance(dictionary, dict):
        return dictionary.get(key)

    # Эта часть добавлена для безопасной работы со списками (на будущее)
    if isinstance(dictionary, (list, tuple)):
        try:
            key = int(key)
            if 0 <= key < len(dictionary):
                return dictionary[key]
        except (ValueError, TypeError):
            pass # Если ключ не число, просто ничего не делаем

    # Если 'dictionary' не является словарём или списком, 
    # или ключ не найден, безопасно возвращаем None.
    return None