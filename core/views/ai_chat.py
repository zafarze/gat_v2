# D:\Project Archive\GAT\core\views\ai_chat.py

from django.shortcuts import render
from django.http import HttpResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.template.loader import render_to_string
from core.ai_service import ask_database

@login_required
def ai_chat_page(request):
    """Обычная страница чата."""
    # Очищаем историю при входе на страницу (опционально, можно убрать)
    # request.session['ai_chat_history'] = [] 
    return render(request, 'ai_chat.html')

@login_required
@require_POST
def ai_ask_api(request):
    """
    API чата.
    """
    user_question = request.POST.get('question', '').strip()
    
    if not user_question:
        return HttpResponse("")

    # 1. Получаем историю из сессии (чтобы ИИ помнил контекст)
    chat_history = request.session.get('ai_chat_history', [])

    # 2. Спрашиваем ИИ (передаем историю!)
    try:
        # Передаем chat_history в функцию
        ai_response_text = ask_database(request.user, user_question, chat_history)
    except Exception as e:
        ai_response_text = f"Простите, я немного устал (ошибка системы: {str(e)})."

    # 3. Обновляем историю
    # Добавляем вопрос пользователя
    chat_history.append({'role': 'user', 'text': user_question})
    # Добавляем ответ ИИ (убираем лишние символы форматирования, если есть)
    clean_response = ai_response_text.replace('\n', '<br>') 
    chat_history.append({'role': 'ai', 'text': clean_response})
    
    # Храним последние 10 сообщений (чтобы не перегружать память ИИ)
    request.session['ai_chat_history'] = chat_history[-10:]
    request.session.modified = True

    # 4. Рендерим ответ (HTML для HTMX)
    html_response = render_to_string('partials/chat/_message_user.html', {'message': user_question})
    
    ai_html = f"""
    <div class="flex items-start gap-2.5">
        <div class="w-8 h-8 rounded-full bg-indigo-100 flex items-center justify-center text-lg border border-indigo-200 flex-shrink-0">🤖</div>
        <div class="bg-white p-3 rounded-xl shadow-sm border border-gray-100 text-gray-700 text-sm leading-relaxed max-w-[85%]">
            {ai_response_text}
        </div>
    </div>
    """
    
    return HttpResponse(html_response + ai_html)