import anthropic
from dotenv import load_dotenv

load_dotenv()

EXTRACT_PROMPT = "Ти — аналітик OSINT."\
"Витягни з тексту нижче всі сутності." \
"PERSON, ORG, LOCATION, EMAIL, PHONE, URL, DATE."\
"Формат: JSON-масив. Для кожної сутності:" \
"{'type': '...', 'value': '...', 'context': '...', 'confidence': '0.0–1.0'}" \
"Правило: НЕ вигадуй сутності яких немає в тексті." \
"[джерела в тегах <source>]" \
\
"Текст: <text>"

VERIFY_PROMPT = "Ось список сутностей: <text>"\
"Для кожної сутності визнач:"\
"1. Чи підтверджується вона у вихідному тексті?"\
"2. Оціни достовірність: 1-10 (де 9-10 = підтверджено, 5-6 = слабке, 3-4 = умовивід)"\
"   9-10 = підтверджено кількома незалежними джерелами"\
"   7-8  = одне надійне джерело"\
"   5-6  = одне слабке або непряме джерело"\
"   3-4  = лише умовивід без джерела"\
"3. Вкажи джерела для кожної сутності [джерела в тегах <source>]" \
"Формат: таблиця Markdown."

SYNTH_PROMPT = "На основі верифікованих даних нижче:\n"\
"<text>\n"\
"1. Напиши короткий аналітичний профіль об'єкта (5–8 речень)"\
"2. Перерахуй що залишилось невстановленим (gaps)"\
"3. Для кожного gap — де і як шукати далі"\
"Уникай тверджень з оцінкою впевненості нижче 7." \
"[джерела в тегах <source>]"

MODEL="claude-sonnet-4-5-20250929"

SYSTEM = open("system.txt", encoding="utf-8").read()
FACTS = open("facts.md", encoding="utf-8").read()

client = anthropic.Anthropic()  # ключ — зі змінної середовища ANTHROPIC_API_KEY

def ask(prompt: str) -> str:
    msg = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text

def as_source(sid: str, kind: str, date: str, text: str) -> str:
    # Розмітка, не захист: модель бачить, де закінчуються інструкції.
    text = text.replace("</source>", "")
    return f'<source id="{sid}" type="{kind}" date="{date}">\n{text}\n</source>'


def checkpoint(name: str, result: str) -> str:
    print(f"\n=== {name} ===\n{result}")
    if "⚠️" in result:
        print("!!! Модель повідомила про інструкцію в даних — перевірте.")
    input("Перевірте результат. Enter — далі, Ctrl+C — зупинити: ")
    return result

sources = "\n".join([
    as_source("А", "загальна інформація", "2023-07-31", open("source1.txt", encoding="utf-8").read()),
    as_source("Б", "загальна інформація", "2025-04-10", open("source2.txt", encoding="utf-8").read()),
    as_source("В", "загальна інформація", "2026-06-25", open("source3.txt", encoding="utf-8").read()),
    as_source("Г", "загальна інформація", "2026-05-13", open("source4.txt", encoding="utf-8").read()),
])

entities = checkpoint("Витяг", ask(EXTRACT_PROMPT.replace("<text>", FACTS) + sources))
verified = checkpoint("Звірка", ask(VERIFY_PROMPT.replace("<text>", entities) + sources))
grades = input("Оцінки джерел (напр. А=B, Б=B, В=B, Г=C): ")
merges = input("Які назви вважати одним актором: ")
report = checkpoint("Синтез", ask(SYNTH_PROMPT.replace("<text>", verified) + sources + grades + merges))

