# D:\Project Archive\GAT\core\views\ai_chat.py

from django.shortcuts import render
from django.http import HttpResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.template.loader import render_to_string
from core.ai_service import ask_database

@login_required
def ai_chat_page(request):
    """Обычная страница чата (если заходят по прямой ссылке)."""
    return render(request, 'ai_chat.html')

@login_required
@require_POST
def ai_ask_api(request):
    """
    API чата. Сохраняет историю в сессию пользователя.
    """
    user_question = request.POST.get('question', '').strip()
    
    if not user_question:
        return HttpResponse("")

    # 1. Получаем ответ от AI
    try:
        ai_response_text = ask_database(request.user, user_question)
    except Exception as e:
        ai_response_text = f"Ошибка: {str(e)}"

    # 2. СОХРАНЯЕМ ИСТОРИЮ В СЕССИЮ
    # Получаем текущую историю или создаем новую
    chat_history = request.session.get('ai_chat_history', [])
    
    # Добавляем сообщение пользователя
    chat_history.append({'role': 'user', 'text': user_question})
    # Добавляем ответ AI
    chat_history.append({'role': 'ai', 'text': ai_response_text})
    
    # Храним только последние 20 сообщений, чтобы не раздувать сессию
    request.session['ai_chat_history'] = chat_history[-20:]
    request.session.modified = True

    # 3. Рендерим ТОЛЬКО новые сообщения для HTMX
    # Мы возвращаем HTML фрагмент, который HTMX добавит в конец чата
    
    # Сначала блок пользователя
    html_response = render_to_string('partials/chat/_message_user.html', {'message': user_question})
    
    # Затем блок AI (используем тот же шаблон, что и в истории, или создаем простой div)
    # Для простоты сформируем HTML ответа AI прямо здесь или через шаблон
    ai_html = f"""
    <div class="flex items-start gap-2.5">
        <div class="w-8 h-8 rounded-full bg-indigo-100 flex items-center justify-center text-lg border border-indigo-200 flex-shrink-0">🤖</div>
        <div class="bg-white p-3 rounded-xl shadow-sm border border-gray-200 text-sm text-gray-800 max-w-[85%]">
            <span class="font-bold text-indigo-600 block mb-1">GAT AI</span>
            <div class="whitespace-pre-wrap">{ai_response_text}</div>
        </div>
    </div>
    """
    
    return HttpResponse(html_response + ai_html)