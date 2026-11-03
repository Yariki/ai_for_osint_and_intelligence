# Журнал AI-OSINT: Richard Branson

Додаток до [основного досьє](Richard_Branson_AI_OSINT_Dossier.md). Не входить до його п’яти сторінок. Дата інформаційного зрізу: **03.11.2026**. Усі наведені нижче джерела перевірялися в межах цього сеансу; «індекс» означає текст, повернений пошуковим інструментом, без успішного прямого відкриття сторінки.

## Бриф і середовище

Суб’єкта та початкову Wikipedia-сторінку надав користувач. Мета — навчальне досьє про публічну діяльність, а не перевірка приватного життя чи встановлення поточного місцеперебування.

Локальний огляд: робоча папка містила лише порожній `README.md`; прикладного стеку, тестів і окремого шаблону досьє не було. Використано структуру з повідомлення користувача. Код продукту та README не змінювалися.

Інструменти: `web.run` (пошук, відкриття, пошук у тексті, переходи), конектор Google Drive (два пошуки), Python 3 зі стандартною бібліотекою (парсинг публічних даних TED, арифметика, перевірка документів), локальні файлові інструменти. Команда `python` відсутня; використано доступний `python3`.

Для внутрішніх джерел застосовано [навичку google-drive](/home/yariki/.codex/plugins/cache/openai-curated-remote/google-drive/0.1.22/skills/google-drive/SKILL.md) з локального каталогу навичок. Google Drive: `Branson` → 0 результатів; `Бренсон` → 0 результатів. Знайдених внутрішніх документів немає; приватні матеріали в зовнішні пошукові запити не передавалися.

## Пошукові напрями

Це перелік фактично використаних репрезентативних запитів, не повний машинний журнал усіх викликів:

- `Richard Branson official biography founder Virgin LinkedIn Instagram`
- `Richard Branson interview transcript leadership risk Virgin`
- `site.instagram.com/richardbranson/ "2026"`
- `site.linkedin.com/posts/rbranson "2026"`
- `site.youtube.com/watch "Richard Branson" "TED"`
- `Richard Branson`, фільтр домену `t.me`
- `Richard Branson July 18 1950 biography Britannica`
- `site.stowe.co.uk "Richard Branson" "Old Stoic"`
- `Richard Branson Virgin Atlantic 1984 history official`
- `Richard Branson 2021 July 11 Unity 22 86 km Reuters Virgin Galactic`
- `Richard Branson British Virgin Islands lives 2026`
- `Richard Branson Epstein 2026 Virgin statement Reuters February`
- `site.reuters.com "Virgin Orbit" "May 23, 2023"`
- `site.virgin.com/about-virgin/timeline "1973"`

Wikipedia була стартовим індексом. Пошук Britannica не дав придатного прямого читання — джерело не використано як підтвердження. Perplexity/Brave/Exa/Tavily/Jina не викликалися: окремих інструментів цих провайдерів у сеансі немає. Пакетний `web.run` замінив їх функціонально, але не забезпечує незалежності різних пошукових систем.

## Вибірка LinkedIn: десять одиниць

Відбір: дев’ять видимих текстів стрічки профілю та одна повністю відкрита стаття. Це зручна доступна вибірка. Аналізовано лише текст, атрибутований акаунту Бренсона; коментарі читачів, автоматичні рекомендації та повні тексти репостованих сторонніх матеріалів виключено. Кодування тем і тону — інтерпретація дослідника, середня впевненість. Факт публікації — B; правдивість зовнішніх подій, згаданих у ній, цим кодуванням не встановлюється.

| ID | Джерело | Час у видимій стрічці / дата | Тема | Тон | Emoji у власному тексті |
|---|---|---|---|---|---|
| LI01 | [Heathrow](https://www.linkedin.com/posts/rbranson_heathrow-says-it-wants-to-bring-airline-partners-activity-7522699177915580416-qkXW) | `1d` | Конкуренція | Критичний | 1 графема |
| LI02 | [Everything is fine](https://www.linkedin.com/posts/rbranson_everything-is-fine-but-wont-be-quitting-activity-7522424851400273920-fjz-) | `2d` | Самопрезентація | Жартівливий | 0 |
| LI03 | [Great leader](https://www.linkedin.com/posts/rbranson_the-way-to-become-a-great-leader-is-to-look-activity-7521965641415712769-M5dh) | `3d` | Лідерство | Афористичний | 0 |
| LI04 | [La Guaira](https://www.linkedin.com/posts/rbranson_nothing-could-prepare-me-for-my-visit-to-activity-7519055343134408704-JF7x) | `1w` | Гуманітарна допомога | Співчутливий | 0 |
| LI05 | [I thought…](https://www.linkedin.com/posts/rbranson_i-thought-id-never-have-to-say-this-in-my-activity-7516554243710058497-4M5v) | `2w` | Міжнародна політика | Емоційно-критичний | 0 |
| LI06 | [Science](https://www.linkedin.com/posts/rbranson_science-is-our-compass-for-navigating-the-activity-7516146175855558656-XLNV) | `2w` | Наука / довкілля | Закличний | 0 |
| LI07 | [Head of Yes](https://www.linkedin.com/posts/rbranson_head-of-yes-information-activity-7515813482517323776-hHeV) | `2w` | Брендова кампанія | Промоційний | 0 |
| LI08 | [Yesterday…](https://www.linkedin.com/posts/rbranson_yesterday-i-thought-that-nobody-could-trump-activity-7515451258703372288-VD5L) | `3w` | Міжнародна політика | Полемічний | 0 |
| LI09 | [Mentorship](https://www.linkedin.com/posts/rbranson_my-number-one-piece-of-advice-for-anyone-activity-7515394941322457088-CgDj) | `3w` | Наставництво | Пояснювальний | 0 |
| LI10 | [Стаття про рекламний трюк](https://www.linkedin.com/pulse/time-i-ripped-my-trousers-jumping-off-tallest-building-branson-pwz1c) | 16.06.2026 | Ризик / навчання | Самокритичний | 0 |

У прямих сторінках LI01–LI02 були інші відносні позначки (`16h`, `1d`), ніж у профілі. Це ознака різних знімків, тому точних дат із цих позначок не виведено. Лічильники реакцій теж не об’єднувалися між знімками. Emoji-метрика: 1 / 10 × 100 = 10% текстів; графему з модифікатором кольору шкіри раховано як один emoji. Це ручне кодування малого корпусу.

## Відеотранскрипт

Відео: [TED на YouTube](https://www.youtube.com/watch?v=DudfBIxw6do). Джерело транскрипту: [офіційна сторінка TED](https://www.ted.com/talks/richard_branson_life_at_30_000_feet/transcript).

Звичайне відкриття TED показало оболонку без тексту. Публічний HTML успішно отримано через `urllib.request`; JSON у `script#__NEXT_DATA__` містить `transcriptData.translation.paragraphs[].cues[]`. Підрахунок слів: сума `len(cue['text'].split())` для всіх сегментів. Це 4 978 токенів, розділених пробілами, включно з позначками мовців і ремарками, а не лінгвістично нормалізоване число слів Бренсона. Час у `cue.time` подано в мілісекундах.

Повну транскрипцію прочитано для контексту; в досьє наведено лише стислий переказ і часові мітки. Повний чужий текст не зберігався в результатах роботи. Репліки ведучого не вважалися висловлюваннями суб’єкта. Прогнози з давнього інтерв’ю не перенесено в сучасні факти.

Додатково перевірено фрагменти [Diane Rehm, 23.09.2014](https://dianerehm.org/shows/2014-09-23/richard-branson-virgin-way/). Мітки `11:…` — час ефіру в опублікованому транскрипті, не хвилини від початку відео. Це радіоінтерв’ю; повний перегляд чи прослуховування медіа не заявляється.

## Instagram: правила використання CSV

[CSV](Instagram_Engagement_Sample.csv) містить лише один відкритий знімок [Pikory](https://pikory.com/u/richardbranson/215712317), без змішування з пошуковим знімком. Числові пари трактуються як likes/comments за представленням агрегатора; нативної перевірки немає. Для суми в ER порядок двох чисел не впливає на результат. Немає постійних URL оригінальних постів і підтвердження часу збору агрегатором. Усі рядки — **C**. Формула відтворює вибірку, не оцінює всю аудиторію.

## Проблеми доступу та виключені висновки

- `www.virgin.com`: прямі запити повертали 403; кілька копій `com.prod.loyalty.virgin.com` — 502. Використано доступний індексований текст із чітким зазначенням цього способу доступу.
- Instagram: пряме відкриття профілю завершилося помилкою. Не стверджується, що перевірено актуальну верифікацію, усі пости чи геотеги.
- Особистий YouTube `@richardbranson`: обмеження частоти; атрибуцію цього каналу не завершено. Для аналізу використано канал TED.
- X `richardbranson`: 403. Історичний вбудований допис не є поточною стрічкою X.
- Telegram: результати містили сторонні згадки; авторський канал не атрибутовано. Висновок «каналу не існує» не робився.
- AP: прямі сторінки не відкрилися; використано пошукові витяги. Reuters на Yahoo: прямий запит повернув 429; зміст доступний у пошуковому індексі.
- Великий PDF звіту Virgin Galactic у SEC перевищив ліміт завантаження вебінструмента; до доказової матриці його не включено. Старий URL пресрелізу Galactic повернув 404; не зараховано як прочитаний доказ.
- Поточні статки, актуальний юридичний контроль над усіма компаніями, непідтверджені зв’язки та діагнози не встановлювалися.

## Незалежність і перевірка оцінок

CNN/KESQ — один редакційний ланцюг; Reuters/Investing.com та Reuters/Yahoo — одне агентство, хоча матеріали різні. Virgin, Virgin Unite і корпоративні соцмережі утворюють пов’язані джерела. Публікація звіту компанії в SEC не робить SEC незалежним автором її тверджень. У F12 незалежний контроль дає матеріал Reuters.

F01–F12: A = F01, F02, F04, F06, F07, F08, F11, F12; B = F03, F09, F10; C = F05. Показник B+ описує лише цю матрицю. Раннє датування, статистика агрегатора, репутаційні сигнали та психологічні гіпотези не включені до цього знаменника.

## Реєстр джерел основного досьє

Реєстр нижче формується з усіх посилань основного документа. Для кожного зазначається фактичний спосіб доступу. Дев’ять постів і одна стаття LinkedIn додатково перелічені вище; повтори не створюють нових незалежних підтверджень.

**29 URL у головному досьє** (включно з ідентифікаторами й навігацією; не всі є прочитаними доказовими сторінками).

| ID | URL | Доступ / походження |
|---|---|---|
| S01 | [wiki](https://en.wikipedia.org/wiki/Richard_Branson) | Відкрито; навігаційне джерело |
| S02 | [cnn](https://kesq.com/news/national-world/cnn-world/2026/06/29/richard-branson-fast-facts-6/) | Відкрито; CNN через KESQ, 29.06.2026 |
| S03 | [bio](https://www.biography.com/business-leaders/richard-branson) | Відкрито; оновлено 02.11.2021 |
| S04 | [virgin](https://com.prod.loyalty.virgin.com/branson-family/richard-branson) | Індексована копія Virgin; прямі запити 403/502 |
| S05 | [forbes](https://www.forbes.com/profile/richard-branson/) | Відкрито з перенаправленням на tollbit.forbes.com; біографічний блок 10.03.2026 |
| S06 | [atlantic](https://www.virginatlantic.com/corporate/our-story) | Відкрито; корпоративна історія |
| S07 | [linkedin](https://www.linkedin.com/in/rbranson) | Відкрито; гостьовий профіль |
| S08 | [newsletter](https://com.prod.loyalty.virgin.com/branson-family/richard-branson-blog/questions-and-answers) | Індекс Virgin; 30.05.2021 |
| S09 | [instagram](https://www.instagram.com/richardbranson/) | Помилка прямого доступу; ідентифікатор зі вбудованих постів |
| S10 | [orbitblog](https://com.prod.loyalty.virgin.com/branson-family/richard-branson-blog/virgin-orbit-flies-into-orbit) | Індекс Virgin; 18.01.2021 |
| S11 | [youtube](https://www.youtube.com/watch?v=DudfBIxw6do) | Пошукові метадані; пряме відкриття дало оболонку |
| S12 | [ted](https://www.ted.com/talks/richard_branson_life_at_30_000_feet/transcript) | Відкрито; повний транскрипт з JSON публічного HTML |
| S13 | [stowe](https://www.stowe.co.uk/getmedia/c06cc64b-b287-444f-a11c-d5554eb6c2eb/Old-Stoic-Society-Accounts-2017-FINAL) | Відкрито PDF; звіт за 2017 рік |
| S14 | [timeline](https://com.prod.loyalty.virgin.com/about-virgin/timeline) | Індекс Virgin; історична хронологія |
| S15 | [unite](https://unite.virgin.com/about-us/index.html?region=ph) | Індекс; офіційна сторінка організації |
| S16 | [bteam](https://www.bteam.org/who-we-are/our-history) | Відкрито; історія організації |
| S17 | [bteamvirgin](https://unite.virgin.com/our-work/the-b-team/index.html?region=us) | Індекс; пов’язане з The B Team джерело |
| S18 | [space](https://www.investing.com/news/world-news/billionaire-branson-soars-to-space-aboard-virgin-galactic-flight-2554769) | Відкрито; Reuters через Investing.com, 11–12.07.2021 |
| S19 | [orbitsec](https://www.sec.gov/Archives/edgar/data/1843388/000162828023020105/vorb-20230523.htm) | Відкрито; первинний Form 8-K за травень 2023 |
| S20 | [orbitreuters](https://finance.yahoo.com/news/1-virgin-orbit-sell-manufacturing-183601788.html) | Індекс Reuters/Yahoo, 23.05.2023; пряме читання 429 |
| S21 | [elders](https://unite.virgin.com/our-work/the-elders/index.html?region=us) | Індекс; історія ініціативи від Virgin Unite |
| S22 | [mentor](https://www.linkedin.com/posts/rbranson_my-number-one-piece-of-advice-for-anyone-activity-7515394941322457088-CgDj) | Відкрито повний текст; авторський акаунт |
| S23 | [joan](https://apnews.com/article/9faac6930d67293967b46d1027b91c96) | Індекс AP, 25–26.11.2025; пряме читання недоступне |
| S24 | [epsteinap](https://apnews.com/article/682447e50bf9a3643a36c9b54ccdfa22) | Індекс AP, 31.01–01.02.2026; пряме читання недоступне |
| S25 | [epsteinatlantic](https://www.theatlantic.com/ideas/2026/04/epstein-files-contacts-degrees/686265/) | Пошуковий витяг; квітень 2026, оригінали листів не перевірено |
| S26 | [stunt](https://www.linkedin.com/pulse/time-i-ripped-my-trousers-jumping-off-tallest-building-branson-pwz1c) | Відкрито повний текст; 16.06.2026 |
| S27 | [pikory](https://pikory.com/u/richardbranson/215712317) | Відкрито; стороння неперевірена статистика |
| S28 | [rehm](https://dianerehm.org/shows/2014-09-23/richard-branson-virgin-way/) | Відкрито; фрагменти транскрипту радіоефіру 23.09.2014 |
| S29 | [hyperloop](https://com.prod.loyalty.virgin.com/branson-family/richard-branson-blog/virgin-hyperloop-makes-transportation-history) | Індекс Virgin; стаття 09.11.2020, вбудований допис 12.11.2020 |

## Час, витрати та фінальна перевірка

Перша контрольна мітка дослідження: **2026-11-03 21:51:18 UTC**. Це мітка після початкових пошуків, а не точний початок білінгу. Контрольну мітку завершення й результати перевірки наведено нижче.

Платних придбань і оформлення підписок не було. Білінг моделі, пошуку та конекторів недоступний; грошову суму не оцінювали. Програмні тести продукту непридатні: змінено лише дослідницькі документи та таблицю даних.

Контрольна мітка перевірки: **2026-11-03 22:03:22 UTC**, тобто **04.11.2026 00:03:22 Europe/Kyiv**. Між контрольною міткою після початкових пошуків і цією перевіркою — 12 хвилин 4 секунди; це не білінговий вимір. Інформаційний зріз залишено на 03.11.2026, оформлення завершено наступної календарної доби за київським часом.

Виконано:

- Перевірено всі 29 визначень посилань у досьє: немає невизначених або невикористаних reference ID. Це перевірка цілісності Markdown, не обіцянка доступності заблокованих вебсторінок.
- Перевірено існування всіх локальних посилань між документами та на використану навичку.
- Повторно обчислено CSV: 12 записів, сума 102 511, ER 0,1712227063%, округлено 0,1712%.
- Зіставлено 12 рядків фактів і лічильники A/B/C; перевірено 91,7% та суму компонентів Depth Score 7,0.
- Виконано тимчасове HTML/PDF-відтворення й перевірку через `pdfinfo` та `pdftotext`: **5 сторінок A4**, кожен із п’яти основних розділів починається на своїй сторінці; близько **2127 слів видимого тексту**. Візуально переглянуто щільні сторінки 2 і 5.
- Параметри контрольного друку: A4, поля 14 мм (нижнє 13 мм), Arial 10,5 pt, інтерліньяж 1,25; таблиці 9,4 pt, інтерліньяж 1,18, внутрішні поля 3 pt. Інший Markdown-рендерер може змінити пагінацію. Тимчасові PDF/HTML/зображення збережено поза робочою папкою, до результатів дослідження їх не додано.
- Перевірено відсутність внутрішніх ідентифікаторів вебінструмента, незаповнених маркерів і повних чужих транскрипцій у кінцевих документах.

Створено три файли: досьє Markdown, цей журнал Markdown і CSV умовного engagement-розрахунку. README залишено без змін. Коміти, публікації та надсилання третім особам не виконувалися.
