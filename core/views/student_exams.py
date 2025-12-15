# D:\New_GAT\core\views\student_exams.py (ПОЛНАЯ ИСПРАВЛЕННАЯ ВЕРСИЯ)

from collections import defaultdict # <--- Не забудьте добавить этот импорт!
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages

from ..models import StudentResult, Question, Subject

@login_required
def exam_list_view(request):
    """Отображает страницу со списком всех пройденных учеником тестов."""
    if not hasattr(request.user, 'profile') or request.user.profile.role != 'STUDENT':
        return redirect('core:dashboard') # Исправлен редирект на core:dashboard

    student = request.user.profile.student
    student_results = StudentResult.objects.filter(student=student).select_related('gat_test').order_by('-gat_test__test_date')
    
    context = {
        'title': 'Мои экзамены',
        'results': student_results,
    }
    return render(request, 'student_dashboard/exam_list.html', context)

@login_required
def exam_review_view(request, result_id):
    """
    Отображает детальный разбор одного выбранного теста.
    ОПТИМИЗИРОВАНО: Устранена проблема N+1 запросов.
    """
    result = get_object_or_404(StudentResult, id=result_id)
    
    # Проверка безопасности: ученик может видеть только свои результаты
    # Используем безопасное сравнение профиля
    if not hasattr(request.user, 'profile') or not request.user.profile.student == result.student:
        messages.error(request, "У вас нет доступа к этому результату.")
        return redirect('core:student_dashboard')

    # 1. Загружаем ВСЕ вопросы для теста ОДНИМ запросом
    # Используем select_related для связей ForeignKey (topic, subject)
    # Используем prefetch_related для связей ManyToMany/Reverse FK (options - варианты ответов)
    all_questions = Question.objects.filter(
        gat_test=result.gat_test
    ).select_related('topic__subject').prefetch_related('options').order_by('question_number')

    # 2. Группируем вопросы по предметам в Python (в памяти)
    # Это позволяет избежать обращения к БД внутри цикла
    questions_map = defaultdict(list)
    for q in all_questions:
        if q.topic and q.topic.subject:
             questions_map[q.topic.subject].append(q)

    # 3. Формируем структуру данных для шаблона
    questions_by_subject = {}
    
    # Получаем список предметов теста, отсортированный по имени
    test_subjects = result.gat_test.subjects.order_by('name')
    
    for subject in test_subjects:
        # Берем вопросы из заранее подготовленного словаря
        questions = questions_map.get(subject, [])
        
        # Если по предмету нет вопросов, пропускаем
        if not questions:
            continue

        # Получаем ответы студента из JSON (dict: {"1": True, "2": False})
        student_answers_dict = result.scores_by_subject.get(str(subject.id), {})
        
        review_data = []
        for question in questions:
            # Ищем ответ по номеру вопроса
            was_correct = student_answers_dict.get(str(question.question_number))
            
            review_data.append({
                'question': question,
                'student_was_correct': was_correct,
            })
        
        if review_data:
            questions_by_subject[subject.name] = review_data

    context = {
        'title': f'Разбор теста: {result.gat_test.name}',
        'result': result,
        'questions_by_subject': questions_by_subject,
    }
    return render(request, 'student_dashboard/exam_review.html', context)