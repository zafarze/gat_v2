# D:\New_GAT\core\templatetags\custom_filters.py

from django import template

register = template.Library()

@register.filter(name='get_item')
def get_item(dictionary, key):
    """
    Позволяет получать значение из словаря по ключу-переменной в шаблоне.
    Использование: {{ my_dictionary|get_item:my_variable }}
    """
    if isinstance(dictionary, dict):
        return dictionary.get(key)
    # Для списков, чтобы получать элемент по индексу
    try:
        return dictionary[key]
    except (TypeError, IndexError, KeyError):
        return None