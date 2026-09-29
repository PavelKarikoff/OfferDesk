{% extends "base_kp.html" %}

{#
  Канонический шаблон КП для домов из клееного бруса.
  Те же переменные работают для любой компании контура.

  Рендер PDF: templates/kp_timber_template.html + utils/timber_kp.py
  Заполненный пример: example_sirius.md
#}

{% block content %}

# КОММЕРЧЕСКОЕ ПРЕДЛОЖЕНИЕ
## Строительство дома из клееного бруса

**Кому:** {{ client_name }}
**Дата:** {{ current_date }}
**Объект:** {{ project_name or 'Индивидуальный жилой дом' }}
**Компания:** {{ company_legal }}


## 1. ВАРИАНТЫ КП

{% for v in variants %}
### Вариант {{ loop.index }} — {{ v.name }}{% if v.selected %} (детализация ниже){% endif %}
{% endfor %}

---

## 2. ДЕТАЛИЗАЦИЯ ПО ЭТАПАМ (на примере варианта «{{ selected_variant }}»)

{% for section in sections %}
### {{ section.title }}

| № | Наименование работ и материалов | Кол-во | Ед. изм. | Стоимость материала, ₽ | Стоимость работы, ₽ | Общая стоимость, ₽ |
|---|--------------------------------|--------|----------|------------------------|---------------------|---------------------|
{% for item in section.rows %}
| {{ loop.index }} | {{ item.name }} | {{ item.qty_fmt }} | {{ item.unit }} | {{ item.material_fmt }} | {{ item.work_fmt }} | {{ item.total_fmt }} |
{% endfor %}

**Итого по разделу «{{ section.short }}»:** {{ section.total_fmt }} ₽

---

{% endfor %}

## 3. СВОДНАЯ ТАБЛИЦА ПО РАЗДЕЛАМ

| Раздел | Стоимость, ₽ |
|--------|--------------|
{% for section in sections %}
| {{ section.short }} | {{ section.total_fmt }} |
{% endfor %}

**Итого по разделам:** {{ subtotal_fmt }} ₽

**Накладные расходы {{ overhead_pct }}%:** {{ overhead_fmt }} ₽

**ИТОГО с накладными:** {{ grand_total_fmt }} ₽

---

## 4. ЧТО ВХОДИТ / НЕ ВХОДИТ

### Входит:
{% for x in included %}
- {{ x }}
{% endfor %}

### Не входит:
{% for x in excluded %}
- {{ x }}
{% endfor %}

---

## 5. ГАРАНТИИ

{% for x in warranty %}
- {{ x }}
{% endfor %}

---

## 6. СЛЕДУЮЩИЙ ШАГ

{% for step in next_steps %}
{{ loop.index }}. {{ step }}
{% endfor %}

---

## 7. КОНТАКТЫ

**{{ company_legal }}**  
{% if address %}Адрес: {{ address }}  {% endif %}
Тел.: {{ phone }}{% if phone_free %} · {{ phone_free }}{% endif %}  
{% if email %}E-mail: {{ email }}  {% endif %}
Сайт: {{ website }}
{% if socials %}
{% for s in socials %}[{{ s.name }}]({{ s.url }}){% if not loop.last %} · {% endif %}{% endfor %}
{% endif %}

---

*КП подготовлено на основе протокола телефонного разговора № {{ protocol_number or 'XX/XX' }}. Цены ориентировочные для {{ region or 'Московской области' }}; итоговая смета уточняется после выбора проекта и выезда на участок.*

{% endblock %}
