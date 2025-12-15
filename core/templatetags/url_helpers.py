# D:\New_GAT\core\templatetags\url_helpers.py
from django import template

register = template.Library()

@register.simple_tag(takes_context=True)
def query_transform(context, **kwargs):
    """
    Сохраняет текущие GET-параметры страницы и позволяет перезаписать или добавить новые.
    """
    query = context['request'].GET.copy()
    for k, v in kwargs.items():
        if v is not None:
            query[k] = v
        else:
            # Если значение None, удаляем параметр (для сброса фильтра)
            query.pop(k, None)
    return query.urlencode()