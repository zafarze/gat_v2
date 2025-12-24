from django.shortcuts import render

def check_ai_chat(request):
    """Страница для проверки AI чата"""
    return render(request, 'test_chat.html')

def debug_base(request):
    """Страница с упрощенным base.html"""
    return render(request, 'base_simple.html')