# D:\New_GAT\core\backends.py (ПОЛНАЯ ИСПРАВЛЕННАЯ ВЕРСИЯ)

from django.contrib.auth.backends import BaseBackend
from django.contrib.auth import get_user_model
from django.db.models import Q

class EmailOrUsernameBackend(BaseBackend):
    """
    Кастомный бэкенд аутентификации.
    Позволяет пользователям входить, используя их email ИЛИ логин.
    """
    def authenticate(self, request, username=None, password=None, **kwargs):
        UserModel = get_user_model()
        
        # --- ✨ ИСПРАВЛЕНИЕ ЗДЕСЬ ✨ ---
        # Вместо .get(), который ожидает только один результат,
        # мы используем .filter(), который может вернуть несколько.
        users = UserModel.objects.filter(Q(username__iexact=username) | Q(email__iexact=username))

        # 1. Если найден ровно один пользователь...
        if users.count() == 1:
            user = users.first()
            # ...проверяем его пароль.
            if user.check_password(password):
                return user
        
        # 2. Если найдено 0 или больше 1 пользователя, мы не можем надежно
        #    определить, кто пытается войти. Возвращаем None, и форма
        #    выдаст стандартную ошибку "Неверные данные".
        return None
        # --- КОНЕЦ ИСПРАВЛЕНИЯ ---

    def get_user(self, user_id):
        UserModel = get_user_model()
        try:
            return UserModel.objects.get(pk=user_id)
        except UserModel.DoesNotExist:
            return None