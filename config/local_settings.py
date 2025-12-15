# /home/andarzedu/gat/config/local_settings.py

SECRET_KEY='z-9)^tgm0n-58@k2j01_w3h3j56&wqeeu*gc0*$2d!46-9@iq1'
DEBUG = False
ALLOWED_HOSTS = ['andarzedu.pythonanywhere.com']

# Данные для вашей MySQL базы данных на PythonAnywhere
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': 'andarzedu$default', # <--- ИСПРАВЛЕНО
        'USER': 'andarzedu',
        'PASSWORD': 'Tojikiston010203/',
        'HOST': 'andarzedu.mysql.pythonanywhere-services.com',
        'PORT': '3306',
    }
}