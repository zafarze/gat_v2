# D:\Project Archive\GAT\core\views\ai_chat.py

from django.shortcuts import render
from django.http import HttpResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.template.loader import render_to_string # Важно!
from core.ai_service import ask_database

@login_required
def ai_chat_page(request):
    """Обычная страница чата."""
    return render(request, 'ai_chat.html')

@login_required
@require_POST
def ai_ask_api(request):
    """
    API чата (HTMX).
    Возвращает HTML: Сначала сообщение юзера, потом ответ ИИ.
    """
    user_question = request.POST.get('question', '').strip()
    
    if not user_question:
        return HttpResponse("")

    # 1. Получаем историю
    chat_history = request.session.get('ai_chat_history', [])

    # 2. Спрашиваем ИИ
    try:
        ai_response_html = ask_database(request.user, user_question, chat_history)
    except Exception as e:
        ai_response_html = f"Простите, ошибка системы: {str(e)}"

    # 3. Обновляем историю
    chat_history.append({'role': 'user', 'text': user_question})
    chat_history.append({'role': 'ai', 'text': ai_response_html})
    
    # Храним последние 20 сообщений
    request.session['ai_chat_history'] = chat_history[-20:]
    request.session.modified = True

    # 4. Рендерим ОТВЕТ (Юзер + ИИ)
    
    # А) Сообщение Юзера (HTML)
    user_html = f"""
    <div class="flex justify-end animate-fade-in-up mb-4">
        <div class="bg-indigo-600 text-white p-3.5 rounded-2xl rounded-tr-none text-sm shadow-md shadow-indigo-500/20 max-w-[85%] leading-relaxed font-medium">
            {user_question}
        </div>
    </div>
    """

    # Б) Сообщение ИИ (HTML)
    ai_html = f"""
    <div class="flex gap-3 animate-fade-in-up mb-4">
        <div class="w-8 h-8 rounded-full bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center shadow-sm shrink-0 overflow-hidden p-1">
             <img src="https://cdn-icons-png.flaticon.com/512/4712/4712027.png" alt="AI" class="w-full h-full object-cover">
        </div>
        <div class="bg-white p-3.5 rounded-2xl rounded-tl-none text-slate-700 text-sm shadow-sm border border-slate-100 leading-relaxed max-w-[95%] overflow-hidden prose prose-sm prose-indigo">
            {ai_response_html}
        </div>
    </div>
    """
    
    # Возвращаем оба куска сразу!
    return HttpResponse(user_html + ai_html)