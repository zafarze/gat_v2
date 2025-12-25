# D:\Project Archive\GAT\list_models.py
import os
import django
from google import genai

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()
from django.conf import settings

print("📡 Запрашиваю список моделей (через google-genai)...")

try:
    client = genai.Client(api_key=settings.GOOGLE_API_KEY)
    models = client.models.list()
    
    print("\n✅ ДОСТУПНЫЕ МОДЕЛИ:")
    found = False
    for m in models:
        # Просто печатаем имя, это самое надежное
        print(f"👉 {m.name}")
        found = True
        
    if not found:
        print("⚠️ Список пуст. Проверь API Key.")

except Exception as e:
    print(f"\n❌ ОШИБКА: {e}")