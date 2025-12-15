# D:\New_GAT\config\urls.py

from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    # --- Django Admin ---
    path('admin/', admin.site.urls),

    # --- Приложение accounts (для входа/выхода/регистрации/профиля) ---
    # Мы используем встроенные URL Django для аутентификации,
    # но можно добавить свои, если нужно (например, для регистрации)
    # path('accounts/', include('django.contrib.auth.urls')), # Стандартные URL Django Auth
    # path('accounts/', include('accounts.urls')), # Если у вас есть свое приложение accounts с доп. URL

    # --- Приложение core (основное приложение) ---
    # ✨✨✨ ИСПРАВЛЕНИЕ ЗДЕСЬ ✨✨✨
    # Убираем префикс 'dashboard/', чтобы URL начинались с корня сайта
    path('', include('core.urls')),
    # ✨✨✨ КОНЕЦ ИСПРАВЛЕНИЯ ✨✨✨
]

# --- Настройки для раздачи медиафайлов в режиме разработки (DEBUG=True) ---
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    # Можно добавить staticfiles_urlpatterns, если Whitenoise не используется
    # from django.contrib.staticfiles.urls import staticfiles_urlpatterns
    # urlpatterns += staticfiles_urlpatterns()