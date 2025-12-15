# D:\New_GAT\config\urls.py (ПОЛНАЯ ВЕРСИЯ)

from django.contrib import admin
from django.urls import path, include # <<<--- Убедись, что 'include' импортирован
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    # Маршрут для админки Django
    path('admin/', admin.site.urls),

    # Включаем ВСЕ остальные URL-шаблоны из приложения 'core'
    # Это значит, что URL для 'accounts' (login, profile и т.д.)
    # должны быть определены или включены ВНУТРИ 'core.urls'
    path('', include('core.urls')), # <<<--- Обрати внимание на пустой префикс ''

    # ВАЖНО: Маршруты для 'accounts' НЕ включены здесь напрямую.
    # Если ты хочешь использовать 'accounts:profile', тебе нужно
    # включить их здесь с пространством имен, например:
    # path('accounts/', include('accounts.urls', namespace='accounts')),
    # Но тогда URL будет /accounts/profile/, а не /dashboard/profile/
]

# Этот блок добавляет маршруты для обслуживания медиа-файлов (загруженных пользователями)
# ТОЛЬКО в режиме разработки (DEBUG=True).
# В продакшене веб-сервер (Nginx/Apache) должен быть настроен для их раздачи.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

    # Маршруты для статических файлов (CSS, JS) обычно НЕ нужны здесь при DEBUG=True,
    # так как Django обрабатывает их сам, если 'django.contrib.staticfiles'
    # включено в INSTALLED_APPS. Whitenoise используется для продакшена.
    # urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT) # Эту строку можно удалить, если она есть