# D:\New_GAT\core\utils.py

def calculate_grade_from_percentage(percentage):
    """
    Единая функция для конвертации процента в 10-балльную оценку.
    Используется по всему проекту.
    """
    if not isinstance(percentage, (int, float)):
        return 1 # Возвращаем минимальную оценку, если данные некорректны

    if percentage >= 91: return 10
    elif percentage >= 81: return 9
    elif percentage >= 71: return 8
    elif percentage >= 61: return 7
    elif percentage >= 51: return 6
    elif percentage >= 41: return 5
    elif percentage >= 31: return 4
    elif percentage >= 21: return 3
    elif percentage >= 11: return 2
    else: return 1