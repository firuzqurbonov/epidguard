# Source labels preserved; normalization only fixes old Tajik font codepoints.
# Aggregate lines are report groups, never selectable diagnoses.
GROUPS = [
 ('all', 'Все инфекционные заболевания', 'Ҳамагӣ бемориҳои сироятӣ', 'All infectious diseases'),
 ('intestinal', 'Всего острых кишечных инфекций', 'Ҳамагӣ БШРМ', 'All acute intestinal infections'),
 ('hepatitis', 'Вирусные гепатиты', 'Гепатитҳои вирусӣ', 'Viral hepatitis'),
 ('parasites', 'Паразитарные заболевания', 'Бемориҳои паразитарӣ', 'Parasitic diseases'),
 ('priority', 'Инфекции особого внимания (проект)', 'Сироятҳои ниёзманд ба таваҷҷуҳи махсус', 'Priority infections (draft)'),
]

# code, Russian, Tajik, English, additional group, review terminology
DISEASES = [
 ('intestinal-known', 'ОКИ установленной этиологии', 'БШРМ бо сабаби маълум', 'Intestinal infection, known etiology', 'intestinal', False),
 ('intestinal-unknown', 'ОКИ неустановленной этиологии', 'БШРМ бо сабаби номаълум', 'Intestinal infection, unknown etiology', 'intestinal', False),
 ('amoebic-dysentery', 'Амёбная дизентерия', 'Исҳоли хунини амёбавӣ', 'Amoebic dysentery', 'intestinal', False),
 ('hepatitis-a', 'Гепатит A', 'Геп. А', 'Hepatitis A', 'hepatitis', False),
 ('hepatitis-b', 'Гепатит B', 'Геп. В', 'Hepatitis B', 'hepatitis', False),
 ('hepatitis-c', 'Гепатит C', 'Геп. С', 'Hepatitis C', 'hepatitis', False),
 ('hepatitis-e', 'Гепатит E', 'ГЕП Е', 'Hepatitis E', 'hepatitis', False),
 ('chronic-hepatitis-b', 'Хронический гепатит B', 'Гепатити музмини В', 'Chronic hepatitis B', 'hepatitis', False),
 ('chronic-hepatitis-c', 'Хронический гепатит C', 'Гепатити музмини С', 'Chronic hepatitis C', 'hepatitis', False),
 ('carrier-b', 'Носительство возбудителя гепатита B', 'Ҳомили барангезандаи В', 'Hepatitis B carrier', 'hepatitis', True),
 ('carrier-c', 'Носительство возбудителя гепатита C', 'Ҳомили барангезандаи С', 'Hepatitis C carrier', 'hepatitis', True),
 ('afp', 'Острый вялый паралич (ОВП)', 'Фалаҷмонанд (ОВП)', 'Acute flaccid paralysis', '', True),
 ('pertussis', 'Коклюш', 'Кабудсулфа', 'Pertussis', '', False),
 ('scarlet-fever', 'Скарлатина', 'Махмалак', 'Scarlet fever', '', False),
 ('chickenpox', 'Ветряная оспа', 'Обакон', 'Chickenpox', '', False),
 ('measles', 'Корь', 'Сурхча (корь)', 'Measles', '', True),
 ('mumps', 'Эпидемический паротит', 'Баногӯшак', 'Mumps', '', False),
 ('encephalitis', 'Энцефалит', 'Илтиҳоби майнаи сар', 'Encephalitis', '', False),
 ('chronic-brucellosis', 'Хронический бруцеллёз', 'Буғумдард (бруцеллёз) музмин', 'Chronic brucellosis', '', False),
 ('anthrax', 'Сибирская язва', 'Сӯхтанӣ', 'Anthrax', 'priority', False),
 ('animal-bites', 'Пострадавшие от укусов / травм (уточнить)', 'Захмбардоштагон', 'Bite / injury surveillance (review)', '', True),
 ('tuberculosis', 'Туберкулёз: форма не уточнена', 'Сил', 'Tuberculosis, unspecified form', '', True),
 ('pulmonary-tb', 'Туберкулёз лёгких', 'Сили шуш', 'Pulmonary tuberculosis', '', False),
 ('tb-positive', 'Туберкулёз (БК+)', 'Сил (БК+)', 'Tuberculosis (BK+)', '', True),
 ('syphilis', 'Сифилис', 'Оташак', 'Syphilis', '', False),
 ('gonorrhea', 'Гонорея', 'Сӯзок', 'Gonorrhea', '', False),
 ('hiv', 'ВИЧ-инфекция', 'Бо ВНМО сироятёфта', 'HIV infection', '', False),
 ('aids', 'СПИД', 'БПНМ', 'AIDS', '', False),
 ('pediculosis', 'Педикулёз', 'Шабушкзанӣ (Педикулёз)', 'Pediculosis', '', True),
 ('ari', 'Острые респираторные инфекции', 'БШРН', 'Acute respiratory infections', '', True),
 ('pneumonia', 'Пневмония', 'Илтиҳоби шуш', 'Pneumonia', '', False),
 ('microsporia', 'Микроспория', 'Микроспория', 'Microsporia', '', False),
 ('scabies', 'Чесотка', 'Хоришак', 'Scabies', '', False),
 ('trichophytia', 'Трихофития', 'Трихофития', 'Trichophytia', '', False),
 ('giardiasis', 'Лямблиоз', 'Лямблиоз', 'Giardiasis', 'parasites', False),
 ('ascariasis', 'Аскаридоз', 'Аскаридоз', 'Ascariasis', 'parasites', False),
 ('trichuriasis', 'Трихоцефалёз', 'Трихоцефалез', 'Trichuriasis', 'parasites', False),
 ('enterobiasis', 'Энтеробиоз', 'Энтеробиоз', 'Enterobiasis', 'parasites', False),
 ('taeniasis', 'Тениаринхоз', 'Тениаринхоз', 'Beef tapeworm infection', 'parasites', False),
 ('hymenolepiasis', 'Гименолепидоз', 'Геминолепидоз', 'Hymenolepiasis', 'parasites', True),
 ('echinococcosis', 'Эхинококкоз', 'Эхинококкоз', 'Echinococcosis', 'parasites', False),
 ('malaria', 'Малярия', 'Вараҷа (малярия)', 'Malaria', 'priority', False),
 ('cholera', 'Холера', 'Вабо', 'Cholera', 'priority', False),
 ('plague', 'Чума', 'Тоун', 'Plague', 'priority', False),
 ('rabies', 'Бешенство', 'Ҳорӣ', 'Rabies', 'priority', False),
 ('cchf', 'Крымская геморрагическая лихорадка', 'Таби хунин (КҚГЛ)', 'Crimean-Congo hemorrhagic fever', 'priority', True),
 ('rubella', 'Краснуха', 'Сурхакон (ниёз ба санҷиши истилоҳ)', 'Rubella', '', True),
 ('acute-brucellosis', 'Острый бруцеллёз', 'Бруцеллёзи шадид', 'Acute brucellosis', '', False),
]


def question(key, ru, tg, en, type='text', **kwargs):
    return {'key': key, 'label_ru': ru, 'label_tg': tg, 'label_en': en, 'type': type, **kwargs}


COMMON_QUESTIONS = [
    question('exposure', 'Известные контакты и возможные обстоятельства заражения', 'Тамосҳо ва шароити эҳтимолии сироят', 'Known contacts and possible exposures'),
    question('travel', 'Поездки в период до заболевания', 'Сафарҳо пеш аз беморӣ', 'Travel before illness'),
    question('source', 'Источник сведений эпиданамнеза', 'Манбаи маълумоти эпидемиологӣ', 'Source of epidemiological information', 'select', options=['Документ', 'Со слов пациента', 'Со слов родственника', 'Неизвестно']),
]
# These are draft data-collection prompts, not clinical criteria or approved protocols.
SPECIFIC_QUESTIONS = {
 'malaria': [question('travel_country', 'Страна / район пребывания и даты', 'Кишвар / ноҳияи будубош ва санаҳо', 'Country / district visited and dates'), question('species', 'Вид плазмодия по лабораторному документу', 'Намуди плазмодий аз рӯи ҳуҷҷати озмоишгоҳ', 'Plasmodium species per laboratory document')],
 'measles': [question('rash_date', 'Дата появления сыпи (если известна)', 'Санаи пайдоиши доначаҳо', 'Rash onset date, if known', 'date'), question('vaccine_record', 'Реквизиты документа о прививках', 'Маълумоти ҳуҷҷати эмгузаронӣ', 'Vaccination record reference')],
 'anthrax': [question('animal_contact', 'Контакт с животными или продуктами животного происхождения', 'Тамос бо ҳайвонот ё маҳсулоти ҳайвонот', 'Animal or animal-product exposure', 'select', options=['Да', 'Нет', 'Неизвестно']), question('animal_details', 'Хозяйство, организация и источник подтверждения', 'Хоҷагӣ, ташкилот ва манбаи тасдиқ', 'Farm, organization and evidence source', show_if={'key': 'animal_contact', 'value': 'Да'})],
 'acute-brucellosis': [question('livestock', 'Контакт с животными / молочными продуктами, источник сведений', 'Тамос бо ҳайвонот / маҳсулоти ширӣ ва манбаи маълумот', 'Livestock / dairy exposure and evidence source')],
 'chronic-brucellosis': [question('livestock', 'Контакт с животными / молочными продуктами, источник сведений', 'Тамос бо ҳайвонот / маҳсулоти ширӣ ва манбаи маълумот', 'Livestock / dairy exposure and evidence source')],
}
