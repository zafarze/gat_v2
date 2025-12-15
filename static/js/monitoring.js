// --- Логика переключения языков ---
const translations = {
    ru: {
        monitoringTitle: 'Мониторинг',
        monitoringSubtitle: 'Используйте фильтры для поиска и анализа данных',
        labelAcademicYear: 'Учебный год',
        labelQuarter: 'Четверть',
        labelSchools: 'Школы',
        labelClasses: 'Классы',
        labelSubjects: 'Предметы',
        labelTests: 'Тесты',
        btnReset: 'Сбросить',
        btnApply: 'Применить',
        studentsFoundText: 'Найдено студентов',
        btnPrint: 'Печать',
        btnExcel: 'Excel',
        btnPdf: 'PDF',
        thNumber: '№',
        thStudentName: 'ФИО Студента',
        thClass: 'Класс',
        thTest: 'Тест',
        thTotalScore: 'Общий балл'
    },
    tj: {
        monitoringTitle: 'Мониторинг',
        monitoringSubtitle: 'Филтрҳоро барои ҷустуҷӯ ва таҳлили маълумот истифода баред',
        labelAcademicYear: 'Соли таҳсил',
        labelQuarter: 'Чоряк',
        labelSchools: 'Мактабҳо',
        labelClasses: 'Синфҳо',
        labelSubjects: 'Фанҳо',
        labelTests: 'Санҷишҳо',
        btnReset: 'Тоза кардан',
        btnApply: 'Татбиқ кардан',
        studentsFoundText: 'Шумораи донишҷӯён',
        btnPrint: 'Чоп',
        btnExcel: 'Excel',
        btnPdf: 'PDF',
        thNumber: '№',
        thStudentName: 'Ному насаб',
        thClass: 'Синф',
        thTest: 'Санҷиш',
        thTotalScore: 'Холи умумӣ'
    },
    en: {
        monitoringTitle: 'Monitoring',
        monitoringSubtitle: 'Use filters to search and analyze data',
        labelAcademicYear: 'Academic Year',
        labelQuarter: 'Quarter',
        labelSchools: 'Schools',
        labelClasses: 'Classes',
        labelSubjects: 'Subjects',
        labelTests: 'Tests',
        btnReset: 'Reset',
        btnApply: 'Apply',
        studentsFoundText: 'Students found',
        btnPrint: 'Print',
        btnExcel: 'Excel',
        btnPdf: 'PDF',
        thNumber: '#',
        thStudentName: 'Student Name',
        thClass: 'Class',
        thTest: 'Test',
        thTotalScore: 'Total Score'
    }
};

function changeLanguage(lang) {
    const langData = translations[lang];
    if (!langData) return;

    // Функция для безопасного обновления текста
    const updateText = (id, text) => {
        const el = document.getElementById(id);
        if (el) el.innerText = text;
    };

    // Обновляем текст
    updateText('monitoring-title', langData.monitoringTitle);
    updateText('monitoring-subtitle', langData.monitoringSubtitle);
    updateText('label-academic-year', langData.labelAcademicYear);
    updateText('label-quarter', langData.labelQuarter);
    updateText('label-schools', langData.labelSchools);
    updateText('label-classes', langData.labelClasses);
    updateText('label-subjects', langData.labelSubjects);
    updateText('label-tests', langData.labelTests);
    updateText('btn-reset', langData.btnReset);
    updateText('btn-apply', langData.btnApply);
    updateText('students-found-text', langData.studentsFoundText);
    updateText('btn-print', langData.btnPrint);
    updateText('btn-excel', langData.btnExcel);
    updateText('btn-pdf', langData.btnPdf);
    updateText('th-number', langData.thNumber);
    updateText('th-student-name', langData.thStudentName);
    updateText('th-class', langData.thClass);
    updateText('th-test', langData.thTest);
    updateText('th-total-score', langData.thTotalScore);

    // Переключаем видимость ФИО студентов
    document.querySelectorAll('.student-name').forEach(span => {
        span.classList.add('hidden');
        if (span.classList.contains('lang-' + lang)) {
            span.classList.remove('hidden');
        }
    });

    // Обновляем активную кнопку
    document.querySelectorAll('#lang-switcher button').forEach(button => {
        button.classList.remove('bg-gray-200', 'font-bold');
        if (button.getAttribute('onclick') === `changeLanguage('${lang}')`) {
            button.classList.add('bg-gray-200', 'font-bold');
        }
    });

    // Сохраняем выбранный язык
    localStorage.setItem('selectedLanguage', lang);
}

// --- Основная логика после загрузки страницы ---
document.addEventListener('DOMContentLoaded', function () {
    
    // --- СКРИПТ ДЛЯ КЛИКАБЕЛЬНЫХ СТРОК ---
    const tableBody = document.getElementById('monitoring-table-body');
    if (tableBody) {
        tableBody.addEventListener('click', function(event) {
            const row = event.target.closest('tr[data-href]');
            if (row) {
                window.location.href = row.dataset.href;
            }
        });
    }
    
    // --- СКРИПТ ДЛЯ ДИНАМИЧЕСКИХ ФИЛЬТРОВ ---
    const filterForm = document.querySelector('form[method="get"]');
    if (!filterForm) return;

    // Получаем URL'ы из data-атрибутов формы
    const quartersUrl = filterForm.dataset.quartersUrl;
    const classesUrl = filterForm.dataset.classesUrl;
    const subjectsUrl = filterForm.dataset.subjectsUrl;

    const yearSelect = document.querySelector('#id_academic_year');
    const quarterSelect = document.querySelector('#id_quarter');
    const schoolsSelect = document.querySelector('#id_schools');
    const classesSelect = document.querySelector('#id_school_classes');
    const subjectsSelect = document.querySelector('#id_subjects');

    async function loadOptions(url, selectElement, placeholder) {
        selectElement.disabled = true;
        selectElement.innerHTML = `<option value="">${placeholder}</option>`;
        try {
            const response = await fetch(url);
            if (!response.ok) throw new Error(`HTTP error! Status: ${response.status}`);
            const html = await response.text();
            selectElement.innerHTML = html;
            selectElement.disabled = false;
        } catch (error) {
            console.error('Fetch error:', error);
            selectElement.innerHTML = `<option value="">Ошибка загрузки</option>`;
        }
    }

    if (yearSelect && quartersUrl) {
        yearSelect.addEventListener('change', function () {
            const url = `${quartersUrl}?year_id=${this.value}`;
            if (this.value) {
                loadOptions(url, quarterSelect, 'Загрузка...');
            } else {
                quarterSelect.innerHTML = '<option value="">Сначала выберите год</option>';
                quarterSelect.disabled = true;
            }
        });
    }

    if (schoolsSelect && classesUrl && subjectsUrl) {
        schoolsSelect.addEventListener('change', function () {
            const selectedSchoolIds = Array.from(this.selectedOptions).map(option => option.value);
            if (selectedSchoolIds.length > 0) {
                const params = new URLSearchParams();
                selectedSchoolIds.forEach(id => params.append('school_ids[]', id));
                
                loadOptions(`${classesUrl}?${params.toString()}`, classesSelect, 'Загрузка классов...');
                loadOptions(`${subjectsUrl}?${params.toString()}`, subjectsSelect, 'Загрузка предметов...');
            } else {
                classesSelect.innerHTML = '<option value="">Сначала выберите школу</option>';
                classesSelect.disabled = true;
                subjectsSelect.innerHTML = '<option value="">Сначала выберите школу</option>';
                subjectsSelect.disabled = true;
            }
        });
    }

    // --- Применение сохраненного языка при загрузке ---
    const savedLang = localStorage.getItem('selectedLanguage');
    if (savedLang) {
        changeLanguage(savedLang);
    }
});