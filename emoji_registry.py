"""
emoji_registry.py — реестр кастомных эмодзи для NicoPaz Rich Posts.

Как получить emoji-id для нового стикерпака:
  python get_emoji_ids.py ИМЯ_НАБОРА

Как добавить тему:
  1. Добавить ключевые слова в THEME_KEYWORDS
  2. Добавить emoji-id в THEME_EMOJI_MAP (только те эмодзи, для которых есть тематический вариант)
  3. Остальные эмодзи подтянутся из CUSTOM_EMOJI_MAP (общий пул)

Приоритет замены:
  тематический THEME_EMOJI_MAP[тема] > общий CUSTOM_EMOJI_MAP > оригинальный символ (без замены)
"""

import re

# ══════════════════════════════════════════════════════════════════════════════
# ОБЩИЙ ПУЛ — применяется всегда когда нет тематического варианта
# Заполнить emoji-id через get_emoji_ids.py после создания/выбора стикерпака.
# Оставьте значение "" если кастомного варианта нет — символ останется как есть.
# ══════════════════════════════════════════════════════════════════════════════

CUSTOM_EMOJI_MAP: dict[str, str] = {
    # ── Футбол / спорт ────────────────────────────────────────────────────────
    "⚽": "",   # мяч
    "🥅": "",   # ворота
    "🏆": "",   # трофей
    "🥇": "",   # золото
    "🥈": "",   # серебро
    "🎽": "",   # форма
    "👟": "",   # бутсы
    "🏟️": "",   # стадион
    "📊": "5231200819986047254",   # статистика
    "📋": "",   # тактика
    "🎯": "",   # точность
    "💪": "",   # сила
    "🦵": "",   # удар
    "🤝": "",   #握手 рукопожатие / трансфер
    "✍️": "",   # подпись контракта
    "📝": "",   # новость / репортаж
    "🔄": "",   # замена
    "🚑": "5328109524295372534",   # травма
    "🩺": "",   # медицина
    "❌": "",   # красная карточка / отмена
    "🟨": "",   # жёлтая карточка
    "🟥": "",   # красная карточка
    "⏱️": "",   # таймер / добавленное время
    "⏰": "5413704112220949842",   # финальный свисток
    "🚨": "5861924756042815123",   # алярм
    "🎙️": "5382013970905309819",   # финальный свисток
    "❗️": "5445331903496332384",   # финальный свисток
    "❓": "5443119411223342219",   # финальный свисток
    # ── Эмоции / реакции ──────────────────────────────────────────────────────
    "🔥": "5210956306952758910",   # горячая новость
    "💥": "",   # взрыв
    "⭐": "5438496463044752972",   # звезда
    "🌟": "5438496463044752972",   # суперзвезда
    "👑": "",   # чемпион
    "💎": "",   # класс
    "😮": "",   # удивление
    "😍": "5373141891321699086",   # восхищение
    "😤": "",   # разочарование
    "😡": "",   # злость / скандал
    "😂": "",   # юмор
    "🙏": "",   # благодарность
    "👏": "5471921242866981303",   # аплодисменты
    "🎉": "",   # праздник
    "💔": "",   # поражение / расставание
    "❤️": "",   # любовь / верность клубу
    "🤔": "",   # слухи / вопрос
    "👀": "5210956306952758910",   # следим / инсайд
    "🗣️": "5370765563226236970",   # интервью / цитата
    "📢": "",   # официальное заявление
    # ── Направления / навигация ───────────────────────────────────────────────
    "➡️": "",
    "⬅️": "",
    "⬆️": "",
    "⬇️": "",
    "🔝": "",
    # ── Деньги / трансферы ────────────────────────────────────────────────────
    "💰": "",   # деньги
    "💸": "",   # большая сумма
    "💵": "",   # долларов
    "💶": "",   # евро
    "🏦": "",   # банк / финансы
    # ── Время ─────────────────────────────────────────────────────────────────
    "🗓️": "",   # дата
    "📅": "",   # календарь
    "🕐": "",
}


# ══════════════════════════════════════════════════════════════════════════════
# ТЕМАТИЧЕСКИЕ НАБОРЫ — переопределяют общий пул для конкретной темы
# Добавляйте только эмодзи у которых есть тематический стикер (мяч в цветах клуба и т.д.)
# ══════════════════════════════════════════════════════════════════════════════

THEME_EMOJI_MAP: dict[str, dict[str, str]] = {
    "barcelona": {
        "⚽": "",   # мяч сине-красный
        "❤️": "",   # сердце гранатовое
        "🏆": "",   # трофей Барсы
        "⭐": "",   # звезда Барсы
        "🔵": "",   # синий — цвет клуба
        "🔴": "",   # красный — цвет клуба
    },
    "real_madrid": {
        "⚽": "",   # мяч белый
        "❤️": "",   # сердце белое
        "🏆": "",   # трофей ЛЧ
        "👑": "",   # корона Реала
        "⭐": "",   # звезда Реала
        "⚪": "",   # белый — цвет клуба
    },
    "manchester_city": {
        "⚽": "",   # мяч голубой
        "💙": "",   # голубое сердце
        "🏆": "",
        "🔵": "",   # голубой — цвет клуба
    },
    "manchester_united": {
        "⚽": "",
        "❤️": "",
        "😈": "",   # дьявол — символ МЮ
        "🔴": "",
    },
    "liverpool": {
        "⚽": "",
        "❤️": "",
        "🔴": "",
        "🦅": "",   # орёл Ливерпуля
    },
    "arsenal": {
        "⚽": "",
        "🔴": "",
        "💪": "",
        "🔫": "",   # пушка — символ Арсенала
    },
    "chelsea": {
        "⚽": "",
        "💙": "",
        "🦁": "",   # лев
        "🔵": "",
    },
    "bayern": {
        "⚽": "",
        "❤️": "",
        "🔴": "",
        "🦁": "",
    },
    "dortmund": {
        "⚽": "",
        "💛": "",   # жёлтое сердце
        "🖤": "",   # чёрное
        "🐝": "",   # пчела — символ
    },
    "juventus": {
        "⚽": "",
        "🖤": "",
        "⚪": "",
        "👑": "",
    },
    "psg": {
        "⚽": "",
        "🔵": "",
        "❤️": "",
        "🗼": "",   # Эйфелева башня
    },
    "atletico": {
        "⚽": "",
        "🔴": "",
        "⚪": "",
        "🦁": "",
    },
    "inter": {
        "⚽": "",
        "🖤": "",
        "💙": "",
    },
    "ac_milan": {
        "⚽": "",
        "❤️": "",
        "🖤": "",
        "🔴": "",
    },
    "champions_league": {
        "⚽": "",
        "🏆": "",   # кубок ЛЧ
        "⭐": "",
        "🌟": "",
    },
    "euro": {
        "⚽": "",
        "🏆": "",
        "🌍": "",
    },
    "world_cup": {
        "⚽": "",
        "🏆": "",
        "🌍": "",
        "🌎": "",
        "🌏": "",
    },
    "germany": {
        "⚽": "",
        "🦅": "",
        "🇩🇪": "",
    },
    "spain": {
        "⚽": "",
        "🇪🇸": "",
        "🐂": "",
    },
    "france": {
        "⚽": "",
        "🇫🇷": "",
        "🐓": "",
    },
    "england": {
        "⚽": "",
        "🇬🇧": "",
        "🦁": "",
    },
    "transfer": {
        "✍️": "",   # подпись контракта
        "💰": "",
        "🤝": "",
        "🔄": "",
    },
    "injury": {
        "🚑": "",
        "🩺": "",
        "💉": "",
        "🏥": "",
    },
}


# ══════════════════════════════════════════════════════════════════════════════
# КЛЮЧЕВЫЕ СЛОВА ДЛЯ ДЕТЕКЦИИ ТЕМЫ
# lowercase, plain text — проверяем против strip_tags(full_text).lower()
# ══════════════════════════════════════════════════════════════════════════════

THEME_KEYWORDS: dict[str, list[str]] = {
    "barcelona": [
        # клуб
        "барселона", "барсе", "барсы", "барсу", "барселоны",
        "barcelona", "barca", "barça", "fcb", "blaugrana",
        # тренер
        "флик", "flik", "hansi flick",
        # игроки
        "педри", "pedri",
        "ямаль", "yamal", "lamine yamal",
        "левандовски", "lewandowski",
        "де йонг", "de jong",
        "рафинья", "raphinha",
        "гави", "gavi",
        "куртуа",  # нет, это Реал — не добавлять
        "феррана торреса", "ferran torres",
        "кундэ", "kounde",
        "иньиго мартинес", "inigo martinez",
        "гундоган", "gundogan",
        "тер штеген", "ter stegen",
        "флик", "flick",
    ],
    "real_madrid": [
        # клуб
        "реал мадрид", "реала", "реале", "реалу",
        "real madrid", "los blancos", "merengues", "hala madrid",
        # тренер
        "анчелотти", "ancelotti",
        # игроки
        "мбаппе", "mbappe", "mbappé",
        "винисиус", "vinicius", "vini jr",
        "беллингем", "bellingham",
        "чуамени", "tchouameni",
        "карвахаль", "carvajal",
        "алаба", "alaba",
        "модрич", "modric",
        "кроос", "kroos",
        "камавинга", "camavinga",
        "родриго", "rodrygo",
        "куртуа", "courtois",
        "лунин", "lunin",
        "брахим диас", "brahim",
        "гюлер", "guler",
    ],
    "manchester_city": [
        "манчестер сити", "ман сити", "manchester city", "man city", "cityzens",
        "гвардиола", "guardiola", "pep",
        "холанн", "холанд", "haaland",
        "де брёйне", "de bruyne",
        "форден", "foden",
        "бернарду силва", "bernardo silva",
        "акэ", "ake",
        "родри", "rodri",
        "грилиш", "grealish",
        "алварес", "alvarez",
        "нунес", "nunes",
        "дока", "doku",
    ],
    "manchester_united": [
        "манчестер юнайтед", "ман юнайтед", "man utd", "manchester united",
        "red devils", "red devil", "олд траффорд", "old trafford",
        "аморим", "amorim",
        "фернандеш", "fernandes", "bruno fernandes",
        "хёйлунд", "hojlund",
        "мейну", "mainoo",
        "далот", "dalot",
        "де лигт", "de ligt",
        "линделёф", "lindelof",
    ],
    "liverpool": [
        "ливерпуль", "liverpool", "the reds", "анфилд", "anfield", "kop",
        "слот", "slot",
        "салах", "salah",
        "нунес", "nunez", "darwin nunez",
        "жота", "jota",
        "алиссон", "alisson",
        "ван дейк", "van dijk",
        "трент", "trent", "arnold",
        "гакпо", "gakpo",
        "маак", "mac allister",
        "собослаи", "szoboszlai",
        "куде", "koudé",
    ],
    "arsenal": [
        "арсенал", "arsenal", "the gunners", "gunners", "emirates",
        "артета", "arteta",
        "сака", "saka",
        "оде", "odegaard",
        "марти.нелли", "martinelli",
        "хавертц", "havertz",
        "жезус", "jesus",
        "уайт", "white",
        "салиба", "saliba",
        "зинченко", "zinchenko",
        "рамсдейл", "ramsdale",
        "рая", "raya",
        "трояну", "trossard",
    ],
    "chelsea": [
        "челси", "chelsea", "the blues", "стэмфорд бридж", "stamford bridge",
        "марсека", "maresca",
        "палмер", "palmer",
        "джексон", "jackson",
        "густо", "gusto",
        "колвилл", "colwill",
        "фофана", "fofana",
        "энцо фернандес", "enzo fernandez",
        "кавашвили", "kavashvili",
        "сантос", "santos",
        "нкунку", "nkunku",
    ],
    "bayern": [
        "бавария", "байерн", "bayern", "fc bayern", "мюнхен", "munich",
        "алиансе", "allianz arena",
        "компани", "kompany",
        "нойер", "neuer",
        "мане", "mane",
        "мусиала", "musiala",
        "тель", "tel",
        "гнабри", "gnabry",
        "де лигт", "de ligt",
        "упамекано", "upamecano",
        "горецка", "goretzka",
        "кимних", "kimmich",
        "мюллер", "muller", "müller",
    ],
    "dortmund": [
        "боруссия дортмунд", "дортмунд", "bvb", "borussia dortmund",
        "зигнал идуна", "signal iduna",
        "сауль", "saul",
        "санчо", "sancho",
        "брандт", "brandt",
        "адейеми", "adeyemi",
        "гросскройц", "grosskreutz",
        "мален", "malen",
        "хуммельс", "hummels",
        "шлоттербек", "schlotterbeck",
        "нико шульц", "schulz",
    ],
    "juventus": [
        "ювентус", "юве", "juventus", "juve", "bianconeri",
        "туринцы",
        "тьяго мотта", "thiago motta",
        "дьяло", "diallo",
        "влахович", "vlahovic",
        "локателли", "locatelli",
        "федерико кьеза", "chiesa",
        "камбиасо", "cambiaso",
        "бремер", "bremer",
        "данило", "danilo",
        "костич", "kostic",
    ],
    "psg": [
        "пари сен-жермен", "псж", "psg", "paris saint-germain", "paris sg",
        "парижане",
        "луис энрике", "luis enrique",
        "донарумма", "donnarumma",
        "мендес", "mendes",
        "марки"
        "носа", "nosa",
        "оасман демеле", "dembele",
        "фавр", "favre",
        "колу",
        "сиф", "sif",
        "захария", "zakaria",
        "хуан барриос", "barrios",
    ],
    "atletico": [
        "атлетико", "atletico", "atl madrid", "атл. мадрид",
        "симеоне", "simeone", "el cholo",
        "облак", "oblak",
        "мората", "morata",
        "феликс", "felix", "joao felix",
        "гризманн", "griezmann",
        "молина", "molina",
        "хитрен", "witsel",
        "корреа", "correa",
        "риккерт",
    ],
    "inter": [
        "интер", "интер милан", "inter", "inter milan", "nerazzurri",
        "индзаги", "inzaghi",
        "мартинес", "martinez", "lautaro",
        "тюрам", "thuram",
        "димарко", "dimarco",
        "бастони", "bastoni",
        "кальхановлу", "calhanoglu",
        "баррелла", "barella",
        "зеленски",
        "соммер", "sommer",
    ],
    "ac_milan": [
        "милан", "ac milan", "milan", "rossoneri", "сан-сиро",
        "фонсека", "fonseca",
        "леан", "leao",
        "жиру", "giroud",
        "мозе", "theo hernandez",
        "беннасер", "bennacer",
        "томори", "tomori",
        "майк майньян", "maignan",
        "монс",
    ],
    "champions_league": [
        "лига чемпионов", "лч", "уефа", "champions league", "ucl",
        "cl final", "финал лч", "финал лиги чемпионов",
        "четвертьфинал лч", "полуфинал лч",
        "групповой этап лч",
    ],
    "euro": [
        "евро", "euro", "чемпионат европы", "чемпионате европы",
        "euro 2024", "euro 2028",
        "отбор на евро", "квалификация евро",
    ],
    "world_cup": [
        "чм", "чемпионат мира", "world cup", "fifa world cup",
        "мундиаль",
        "world cup 2026", "чм 2026",
    ],
    "germany": [
        "германия", "немцы", "germany", "die mannschaft", "dfb",
        "нагельсманн", "nagelsmann",
        "мюллер", "müller", "muller",
        "кимних", "kimmich",
        "мусиала", "musiala",
        "рюдигер", "rudiger",
        "хаверц", "havertz",
        "шлоттербек", "schlotterbeck",
        "нойер", "neuer",
        "гнабри", "gnabry",
        "вирц", "wirtz",
    ],
    "spain": [
        "испания", "сборная испании", "spain", "la roja", "la furia roja",
        "луис де ла фуэнте", "de la fuente",
        "мораты", "morata",
        "ямаль", "yamal",
        "педри", "pedri",
        "гави", "gavi",
        "карвахаль", "carvajal",
        "родри", "rodri",
        "фабиан руис", "fabian ruiz",
        "симон", "simon",
    ],
    "france": [
        "франция", "сборная франции", "france", "les bleus",
        "дешам", "deschamps",
        "мбаппе", "mbappe",
        "гризманн", "griezmann",
        "тюрам", "thuram",
        "жиру", "giroud",
        "камавинга", "camavinga",
        "конкамбр", "konate",
        "салиба", "saliba",
        "рабьо", "rabiot",
    ],
    "england": [
        "англия", "сборная англии", "england", "three lions",
        "саутгейт", "southgate",
        "белл", "bellingham",
        "сака", "saka",
        "фоден", "foden",
        "харри кейн", "harry kane",
        "трент", "trent",
        "трипьер", "trippier",
        "гордон", "gordon",
        "рашфорд", "rashford",
    ],
    "transfer": [
        "трансфер", "transfer", "переход",
        "подписал", "подписала", "подписание",
        "контракт", "contract",
        "куплен", "продан", "куплен за", "продан за",
        "официально", "official", "announced",
        "deal done", "done deal",
        "согласован", "согласовано",
        "аренда", "loan", "in loan",
        "свободный агент", "free agent",
        "отступные", "release clause",
        "buyout clause",
        "fabrizio romano",  # часто = трансферная новость
        "sky sports transfer",
    ],
    "injury": [
        "травма", "травмирован", "травмировался",
        "injury", "injured", "out",
        "операция", "operation", "surgery",
        "реабилитация", "rehabilitation",
        "лазарет", "lazaret",
        "вне игры", "пропустит",
        "вернётся через", "вернется через",
        "возвращение после травмы",
        "разрыв", "растяжение", "перелом",
    ],
}


# ══════════════════════════════════════════════════════════════════════════════
# ДЕТЕКТОР ТЕМЫ + EMOJI ENHANCER
# ══════════════════════════════════════════════════════════════════════════════

_STRIP_TAGS_RE = re.compile(r"<[^>]+>")
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F9FF"
    "\U00002600-\U000027BF"
    "\U0001FA00-\U0001FA9F"
    "\uFE0F"
    "]",
    flags=re.UNICODE,
)
_TOKEN_RE = re.compile(r"(<[^>]+>|[^<]+)")


def detect_theme(html_text: str) -> str | None:
    """
    Возвращает название темы с максимальным числом совпадений ключевых слов.
    При ничьей или отсутствии совпадений — None (используется только CUSTOM_EMOJI_MAP).
    При мультитеме (два клуба с одинаковым счётом) — None (нейтральный пост).
    """
    plain = _STRIP_TAGS_RE.sub(" ", html_text).lower()

    scores: dict[str, int] = {}
    for theme, keywords in THEME_KEYWORDS.items():
        count = sum(1 for kw in keywords if kw in plain)
        if count:
            scores[theme] = count

    if not scores:
        return None

    # Сортируем по убыванию
    ranked = sorted(scores.items(), key=lambda x: -x[1])

    # Если топ-2 набрали одинаково — мультитема, нейтральный режим
    if len(ranked) >= 2 and ranked[0][1] == ranked[1][1]:
        # Исключение: если обе темы — это не клубы (например injury + transfer) — берём первую
        club_themes = {
            "barcelona", "real_madrid", "manchester_city", "manchester_united",
            "liverpool", "arsenal", "chelsea", "bayern", "dortmund",
            "juventus", "psg", "atletico", "inter", "ac_milan",
        }
        if ranked[0][0] in club_themes and ranked[1][0] in club_themes:
            return None  # матч между двумя клубами — нейтральный

    return ranked[0][0]


def _replace_emoji_in_text_nodes(html: str, replacer) -> str:
    """
    Применяет replacer() только к текстовым нодам HTML.
    Не трогает теги, атрибуты src=, href=, уже вставленные <tg-emoji>.
    """
    parts = []
    inside_tg_emoji = False
    for m in _TOKEN_RE.finditer(html):
        token = m.group(0)
        if token.startswith("<"):
            tl = token.lower()
            if tl.startswith("<tg-emoji"):
                inside_tg_emoji = True
            elif tl.startswith("</tg-emoji"):
                inside_tg_emoji = False
            parts.append(token)
        else:
            parts.append(token if inside_tg_emoji else replacer(token))
    return "".join(parts)


def enhance_rich_emoji(rich_html: str) -> str:
    """
    Заменяет обычные эмодзи на кастомные <tg-emoji> теги в Rich HTML.

    Тема определяется автоматически через detect_theme().
    Если Cloudinary не нашёл тему или нет кастомного ID — символ остаётся как есть.
    Функция безопасна: любое исключение ловится снаружи (в send_rich_post).
    """
    theme = detect_theme(rich_html)
    theme_map = THEME_EMOJI_MAP.get(theme, {}) if theme else {}

    if theme:
        print(f"  Emoji Enhancer: тема «{theme}»")
    else:
        print("  Emoji Enhancer: нейтральный пост — общий пул")

    def replacer(text: str) -> str:
        def sub(m: re.Match) -> str:
            char = m.group(0)
            emoji_id = theme_map.get(char) or CUSTOM_EMOJI_MAP.get(char)
            if not emoji_id:
                return char
            return f'<tg-emoji emoji-id="{emoji_id}">{char}</tg-emoji>'
        return _EMOJI_RE.sub(sub, text)

    return _replace_emoji_in_text_nodes(rich_html, replacer)
