# D:\Project Archive\GAT\debug_ai.py
import os
import django
from google import genai

# 1. Настраиваем Django, чтобы достать настройки
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.conf import settings

print("-" * 30)
print("🔍 ДИАГНОСТИКА AI SERIVCE")
print("-" * 30)

# 2. Проверяем ключ
api_key = settings.GOOGLE_API_KEY
if not api_key:
    print("❌ ОШИБКА: GOOGLE_API_KEY не найден в settings.py!")
    print("Проверь файл .env и переменные окружения.")
    exit()
else:
    masked_key = api_key[:5] + "..." + api_key[-4:]
    print(f"✅ API Key найден: {masked_key}")

# 3. Пробуем подключиться (тест SDK)
print("\n📡 Попытка подключения к Google (Gemini 1.5 Flash)...")

try:
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model="gemini-1.5-flash",
        contents="Привет! Если ты работаешь, скажи 'GAT System Online'."
    )
    print("✅ УСПЕХ! Ответ от AI:")
    print(f"👉 {response.text}")

except Exception as e:
    print("\n❌ ОШИБКА ПОДКЛЮЧЕНИЯ:")
    print(e)
    
    print("\n🔄 Пробуем Fallback (Gemini Pro)...")
    try:
        response = client.models.generate_content(
            model="gemini-1.5-pro",
            contents="Test."
        )
        print("✅ УСПЕХ (через Pro модель)!")
    except Exception as e2:
        print("❌ Fallback тоже не сработал.")
        print(e2)

print("-" * 30)