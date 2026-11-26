"""Patient-facing text in Armenian, Russian and English.

Everything a patient can receive over WhatsApp/Telegram, plus the library of
plain-language findings used when a plan is drafted without AI.
"""

from __future__ import annotations

from datetime import date

LANGUAGES = ("hy", "ru", "en")
DEFAULT_LANGUAGE = "hy"


def lang_of(value: str | None) -> str:
    return value if value in LANGUAGES else DEFAULT_LANGUAGE


# --------------------------------------------------------------------------
# Outbound messages. Placeholders: {name} {clinic} {dentist} {link}
# {valid_until} {option}
# --------------------------------------------------------------------------

MESSAGES: dict[str, dict[str, str]] = {
    "plan_ready": {
        "en": (
            "Hello {name}! This is {clinic}. Your personal orthodontic treatment plan is ready — "
            "with a 3D preview of the expected result and payment options.\n\n"
            "Open your plan: {link}\n\n"
            "You can ask the doctor a question or choose a time to start right on the page."
        ),
        "ru": (
            "Здравствуйте, {name}! Это {clinic}. Ваш персональный план ортодонтического лечения готов: "
            "3D-визуализация ожидаемого результата и варианты оплаты.\n\n"
            "Открыть план: {link}\n\n"
            "Прямо на странице можно задать вопрос врачу или выбрать удобное время для начала лечения."
        ),
        "hy": (
            "Բարև Ձեզ, {name}։ Գրում ենք {clinic}-ից։ Ձեր անհատական օրթոդոնտիկ բուժման պլանը պատրաստ է՝ "
            "սպասվող արդյունքի 3D ցուցադրությամբ և վճարման տարբերակներով։\n\n"
            "Բացել պլանը՝ {link}\n\n"
            "Էջում կարող եք հարց ուղղել բժշկին կամ ընտրել բուժումը սկսելու հարմար ժամանակ։"
        ),
    },
    "followup_1_unviewed": {
        "en": (
            "Hi {name}, {clinic} here. Your treatment plan is ready whenever you have a couple of minutes — "
            "it shows step by step how your smile is expected to change.\n\n{link}"
        ),
        "ru": (
            "{name}, добрый день! Это {clinic}. Ваш план лечения ждёт вас — в нём показано, как шаг за шагом "
            "изменится ваша улыбка. Просмотр займёт пару минут.\n\n{link}"
        ),
        "hy": (
            "Բարև Ձեզ, {name}։ Գրում ենք {clinic}-ից։ Ձեր բուժման պլանը պատրաստ է և սպասում է Ձեզ․ այնտեղ քայլ առ քայլ "
            "ցույց է տրված, թե ինչպես կփոխվի Ձեր ժպիտը։ Դիտումը կտևի մի քանի րոպե։\n\n{link}"
        ),
    },
    "followup_1_viewed": {
        "en": (
            "Hi {name}, thank you for looking through your treatment plan. If anything is unclear — duration, "
            "comfort, payment — just reply here and the doctor will answer.\n\n{link}"
        ),
        "ru": (
            "{name}, спасибо, что посмотрели план лечения. Если остались вопросы — о сроках, комфорте или оплате — "
            "просто ответьте на это сообщение, и врач вам ответит.\n\n{link}"
        ),
        "hy": (
            "{name}, շնորհակալություն, որ ծանոթացաք բուժման պլանին։ Եթե հարցեր ունեք՝ տևողության, հարմարավետության "
            "կամ վճարման վերաբերյալ, պարզապես պատասխանեք այս հաղորդագրությանը, և բժիշկը կպատասխանի Ձեզ։\n\n{link}"
        ),
    },
    "followup_2": {
        "en": (
            "{name}, a quick note from {clinic}: your plan includes several payment options, including paying in "
            "monthly installments. You can compare them here: {link}\n\n"
            "Prefer to talk it through by phone? Reply with a time that suits you."
        ),
        "ru": (
            "{name}, напоминаем: в вашем плане есть несколько вариантов оплаты, включая рассрочку по месяцам. "
            "Сравнить их можно здесь: {link}\n\n"
            "Если удобнее обсудить по телефону — напишите, когда вам позвонить."
        ),
        "hy": (
            "{name}, հիշեցնում ենք, որ Ձեր պլանում կան վճարման մի քանի տարբերակներ, այդ թվում՝ ամսական տարաժամկետ "
            "վճարում։ Դրանք կարող եք համեմատել այստեղ՝ {link}\n\n"
            "Եթե նախընտրում եք քննարկել հեռախոսով, գրեք՝ երբ զանգահարենք Ձեզ։"
        ),
    },
    "followup_3": {
        "en": (
            "Hi {name}, your treatment plan and the quoted prices are valid until {valid_until}. If you decide to "
            "start, you can pick a time here: {link}\n\n"
            "If now isn't the right moment, that's completely fine — just let us know."
        ),
        "ru": (
            "{name}, здравствуйте! Ваш план лечения и указанные в нём цены действуют до {valid_until}. Если решите "
            "начать — выбрать время можно здесь: {link}\n\n"
            "Если сейчас неподходящий момент — ничего страшного, просто дайте нам знать."
        ),
        "hy": (
            "Բարև Ձեզ, {name}։ Ձեր բուժման պլանը և նշված գները ուժի մեջ են մինչև {valid_until}։ Եթե որոշեք սկսել, "
            "ժամանակ կարող եք ընտրել այստեղ՝ {link}\n\n"
            "Եթե հիմա հարմար պահ չէ, ոչինչ․ պարզապես տեղեկացրեք մեզ։"
        ),
    },
    "followup_final": {
        "en": (
            "{name}, this is our last reminder about your treatment plan. It stays available at the link below, "
            "and you're welcome to write to us any time.\n\n{link}\n\nWishing you all the best — {clinic}"
        ),
        "ru": (
            "{name}, это наше последнее напоминание о плане лечения. Он останется доступен по ссылке ниже, "
            "и вы можете написать нам в любое время.\n\n{link}\n\nВсего доброго! {clinic}"
        ),
        "hy": (
            "{name}, սա մեր վերջին հիշեցումն է Ձեր բուժման պլանի մասին։ Այն հասանելի կմնա ստորև նշված հղումով, "
            "և Դուք ցանկացած ժամանակ կարող եք գրել մեզ։\n\n{link}\n\nԼավագույն մաղթանքներով՝ {clinic}"
        ),
    },
    "opt_out_hint": {
        "en": "Reply STOP to stop these reminders.",
        "ru": "Чтобы не получать напоминания, ответьте СТОП.",
        "hy": "Հիշեցումները դադարեցնելու համար պատասխանեք ՍՏՈՊ։",
    },
    "booked_confirmation": {
        "en": (
            "Thank you, {name}! We've received your request to start with “{option}”. Our coordinator will "
            "contact you to confirm the exact date and time. — {clinic}"
        ),
        "ru": (
            "Спасибо, {name}! Мы получили вашу заявку на начало лечения: «{option}». Администратор свяжется "
            "с вами, чтобы подтвердить дату и время. — {clinic}"
        ),
        "hy": (
            "Շնորհակալություն, {name}։ Ստացել ենք բուժումը սկսելու Ձեր հայտը՝ «{option}»։ Մեր ադմինիստրատորը "
            "կկապվի Ձեզ հետ՝ օրը և ժամը հաստատելու համար։ — {clinic}"
        ),
    },
    "declined_ack": {
        "en": (
            "Understood, {name} — we won't send further reminders. Your plan stays available if you change "
            "your mind. Take care! — {clinic}"
        ),
        "ru": (
            "Понятно, {name}. Больше не будем беспокоить напоминаниями. Если передумаете — план останется "
            "доступен по ссылке. Всего доброго! — {clinic}"
        ),
        "hy": (
            "Հասկանալի է, {name}։ Այլևս հիշեցումներ չենք ուղարկի։ Եթե մտափոխվեք, պլանը հասանելի կմնա հղումով։ "
            "Ամենայն բարիք։ — {clinic}"
        ),
    },
    "question_ack": {
        "en": "Thanks, {name} — your question has been passed to the doctor. We'll reply here shortly.",
        "ru": "Спасибо, {name}! Ваш вопрос передан врачу. Мы ответим вам здесь в ближайшее время.",
        "hy": "Շնորհակալություն, {name}։ Ձեր հարցը փոխանցվել է բժշկին։ Շուտով կպատասխանենք այստեղ։",
    },
    "tg_linked": {
        "en": "Hi {name}! You're connected to {clinic}. Your treatment plan will arrive in this chat.",
        "ru": "Здравствуйте, {name}! Вы подключены к {clinic}. План лечения придёт в этот чат.",
        "hy": "Բարև Ձեզ, {name}։ Դուք միացել եք {clinic}-ին։ Բուժման պլանը կուղարկվի այս չատ։",
    },
    "tg_unknown": {
        "en": "Please open the personal link you received from your clinic to connect.",
        "ru": "Чтобы подключиться, откройте персональную ссылку, которую вам дали в клинике.",
        "hy": "Միանալու համար բացեք կլինիկայից ստացած Ձեր անհատական հղումը։",
    },
    "button_open_plan": {
        "en": "Open my plan",
        "ru": "Открыть план",
        "hy": "Բացել պլանը",
    },
    "button_not_interested": {
        "en": "Not interested",
        "ru": "Не интересует",
        "hy": "Չի հետաքրքրում",
    },
}

# Words a patient can send to stop all automated messages.
STOP_WORDS = {
    "stop", "unsubscribe", "стоп", "отписаться", "отписка", "ստոպ", "դադարեցնել",
}


def render(key: str, lang: str, **values: str) -> str:
    template = MESSAGES[key][lang_of(lang)]
    return template.format(**values)


def format_date(value: str | date | None, lang: str) -> str:
    """Dates are written as DD.MM.YYYY in all three languages for this market."""
    if not value:
        return ""
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value[:10])
        except ValueError:
            return value
    return value.strftime("%d.%m.%Y")


def first_name(full_name: str) -> str:
    parts = full_name.strip().split()
    return parts[0] if parts else full_name.strip()


# --------------------------------------------------------------------------
# Findings library: plain-language explanation for each condition.
# --------------------------------------------------------------------------

FINDINGS: dict[str, dict[str, tuple[str, str]]] = {
    "crowding": {
        "en": (
            "Crowded teeth",
            "There isn't quite enough room for all the teeth, so some of them overlap or are rotated. "
            "After alignment the teeth are easier to clean and the smile line becomes even.",
        ),
        "ru": (
            "Скученность зубов",
            "Зубам не хватает места в зубном ряду, поэтому некоторые из них повёрнуты или заходят друг на друга. "
            "После выравнивания зубы проще чистить, а линия улыбки становится ровной.",
        ),
        "hy": (
            "Ատամների խիտ դասավորություն",
            "Ատամնաշարում տեղը բավարար չէ, այդ պատճառով որոշ ատամներ թեքված են կամ մասամբ ծածկում են իրար։ "
            "Հարթեցումից հետո ատամներն ավելի հեշտ է մաքրել, իսկ ժպիտի գիծը դառնում է համաչափ։",
        ),
    },
    "spacing": {
        "en": (
            "Gaps between teeth",
            "There are spaces between some of the teeth. Closing them gives a more even smile and keeps food "
            "from getting trapped against the gums.",
        ),
        "ru": (
            "Промежутки между зубами",
            "Между некоторыми зубами есть промежутки. Их закрытие делает улыбку ровнее и защищает дёсны "
            "от застревания пищи.",
        ),
        "hy": (
            "Ատամների միջև բացվածքներ",
            "Որոշ ատամների միջև կան բացվածքներ։ Դրանց փակումը ժպիտը դարձնում է ավելի համաչափ և պաշտպանում է "
            "լնդերը սննդի մնացորդների կուտակումից։",
        ),
    },
    "diastema": {
        "en": (
            "Gap between the front teeth",
            "There is a space between the two upper front teeth. It can be closed gently as part of the alignment.",
        ),
        "ru": (
            "Щель между передними зубами (диастема)",
            "Между двумя верхними передними зубами есть промежуток. Его можно аккуратно закрыть в ходе выравнивания.",
        ),
        "hy": (
            "Բացվածք առջևի ատամների միջև (դիաստեմա)",
            "Վերին երկու կենտրոնական ատամների միջև կա բացվածք։ Այն կարելի է նրբորեն փակել հարթեցման ընթացքում։",
        ),
    },
    "overjet": {
        "en": (
            "Upper front teeth sit forward",
            "The upper front teeth are positioned noticeably further forward than the lower ones. Bringing them "
            "back improves the bite and lowers the risk of chipping a tooth.",
        ),
        "ru": (
            "Верхние передние зубы выступают вперёд",
            "Верхние передние зубы расположены заметно впереди нижних. Их перемещение назад улучшает прикус "
            "и снижает риск травмы зубов.",
        ),
        "hy": (
            "Վերին առջևի ատամները առաջ են ցցված",
            "Վերին առջևի ատամները տեղակայված են ստորիններից զգալիորեն առաջ։ Դրանց հետ տեղաշարժը բարելավում է "
            "կծվածքը և նվազեցնում ատամների վնասվելու ռիսկը։",
        ),
    },
    "underbite": {
        "en": (
            "Lower teeth in front of the upper teeth",
            "The lower front teeth close in front of, or edge to edge with, the upper ones. Correcting this "
            "spreads chewing forces more evenly.",
        ),
        "ru": (
            "Нижние зубы перекрывают верхние",
            "Нижние передние зубы смыкаются впереди верхних или встык с ними. Исправление позволяет равномерно "
            "распределить жевательную нагрузку.",
        ),
        "hy": (
            "Ստորին ատամները ծածկում են վերիններին",
            "Ստորին առջևի ատամները փակվում են վերինների առջևում կամ դրանց եզր առ եզր։ Շտկումը թույլ է տալիս "
            "հավասարաչափ բաշխել ծամողական ծանրաբեռնվածությունը։",
        ),
    },
    "deepbite": {
        "en": (
            "Deep bite",
            "The upper front teeth cover too much of the lower ones when you bite. Levelling the bite protects "
            "the lower teeth and gums from wear.",
        ),
        "ru": (
            "Глубокий прикус",
            "При смыкании верхние передние зубы слишком сильно перекрывают нижние. Нормализация прикуса защищает "
            "нижние зубы и дёсны от стирания и травмы.",
        ),
        "hy": (
            "Խորը կծվածք",
            "Ատամները փակելիս վերին առջևի ատամները չափից շատ են ծածկում ստորիններին։ Կծվածքի կարգավորումը "
            "պաշտպանում է ստորին ատամներն ու լնդերը մաշվելուց։",
        ),
    },
    "openbite": {
        "en": (
            "Open bite",
            "The front teeth don't meet when the back teeth are closed. Closing the bite makes it easier to bite "
            "into food and can improve some speech sounds.",
        ),
        "ru": (
            "Открытый прикус",
            "При сомкнутых боковых зубах передние зубы не соприкасаются. Закрытие прикуса облегчает откусывание "
            "пищи и улучшает дикцию.",
        ),
        "hy": (
            "Բաց կծվածք",
            "Կողմնային ատամները փակելիս առջևի ատամները չեն հպվում իրար։ Կծվածքի փակումը հեշտացնում է սնունդ կծելը "
            "և բարելավում արտասանությունը։",
        ),
    },
    "crossbite": {
        "en": (
            "Narrow upper arch (crossbite)",
            "The upper arch is narrower than the lower one, so some back teeth bite on the wrong side of each "
            "other. Widening the upper arch balances the bite.",
        ),
        "ru": (
            "Сужение верхнего зубного ряда (перекрёстный прикус)",
            "Верхний зубной ряд уже нижнего, поэтому часть боковых зубов смыкается неправильно. Расширение "
            "верхнего ряда выравнивает прикус.",
        ),
        "hy": (
            "Վերին ատամնաշարի նեղացում (խաչաձև կծվածք)",
            "Վերին ատամնաշարն ավելի նեղ է, քան ստորինը, ուստի կողմնային ատամների մի մասը սխալ է փակվում։ "
            "Վերին շարքի լայնացումը հավասարակշռում է կծվածքը։",
        ),
    },
    "midline": {
        "en": (
            "Midline shift",
            "The centre line of the upper teeth doesn't line up with the centre of the face. Treatment brings it "
            "back to the middle for a symmetrical smile.",
        ),
        "ru": (
            "Смещение средней линии",
            "Центральная линия верхних зубов не совпадает с серединой лица. В ходе лечения она возвращается "
            "к центру, и улыбка становится симметричной.",
        ),
        "hy": (
            "Միջին գծի շեղում",
            "Վերին ատամների կենտրոնական գիծը չի համընկնում դեմքի միջին գծի հետ։ Բուժման ընթացքում այն վերադառնում է "
            "կենտրոն, և ժպիտը դառնում է համաչափ։",
        ),
    },
}

SUMMARY_TEMPLATE = {
    "en": (
        "{name}, based on your examination we prepared a personal plan to align your teeth and balance your bite. "
        "Below you can see what we found, a 3D preview of the expected result, and the treatment options with prices."
    ),
    "ru": (
        "{name}, по результатам осмотра мы подготовили персональный план выравнивания зубов и нормализации прикуса. "
        "Ниже — что мы обнаружили, 3D-визуализация ожидаемого результата и варианты лечения с ценами."
    ),
    "hy": (
        "{name}, զննման արդյունքների հիման վրա պատրաստել ենք ատամների հարթեցման և կծվածքի կարգավորման անհատական պլան։ "
        "Ստորև կգտնեք հայտնաբերված խնդիրները, սպասվող արդյունքի 3D ցուցադրությունը և բուժման տարբերակները՝ գներով։"
    ),
}

# --------------------------------------------------------------------------
# Default treatment catalog text (prices live in planning.DEFAULT_SETTINGS).
# --------------------------------------------------------------------------

CATALOG_TEXT = {
    "metal": {
        "name": {"en": "Metal braces", "ru": "Металлические брекеты", "hy": "Մետաղական բրեկետներ"},
        "tagline": {
            "en": "The most affordable and reliable option. Small metal brackets, visible on the teeth.",
            "ru": "Самый доступный и надёжный вариант. Небольшие металлические брекеты, заметны на зубах.",
            "hy": "Ամենամատչելի և հուսալի տարբերակը։ Փոքր մետաղական բրեկետներ, որոնք տեսանելի են ատամների վրա։",
        },
    },
    "ceramic": {
        "name": {"en": "Ceramic braces", "ru": "Керамические брекеты", "hy": "Կերամիկական բրեկետներ"},
        "tagline": {
            "en": "Tooth-coloured brackets that are much less noticeable.",
            "ru": "Брекеты в цвет зубов — гораздо менее заметны.",
            "hy": "Ատամի գույնի բրեկետներ, որոնք շատ ավելի քիչ են նկատելի։",
        },
    },
    "aligners": {
        "name": {"en": "Clear aligners", "ru": "Прозрачные элайнеры", "hy": "Թափանցիկ էլայներներ"},
        "tagline": {
            "en": "Removable, nearly invisible trays. Take them out to eat and brush.",
            "ru": "Съёмные, почти незаметные каппы. Снимаются на время еды и чистки зубов.",
            "hy": "Շարժական, գրեթե անտեսանելի կապաներ։ Հանվում են ուտելիս և ատամները մաքրելիս։",
        },
    },
}

DEFAULT_INCLUDES = {
    "en": ["All adjustment visits", "Retainers after treatment", "12 months of follow-up checks"],
    "ru": ["Все плановые визиты и активации", "Ретейнеры после лечения", "Наблюдение в течение 12 месяцев"],
    "hy": ["Բոլոր պլանային այցերը", "Ռետեյներներ բուժումից հետո", "12 ամիս հսկողություն"],
}
