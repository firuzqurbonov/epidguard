from django import template
from copy import deepcopy
from surveillance.i18n import tr

register = template.Library()


@register.simple_tag(takes_context=True)
def t(context, value):
    return tr(value, context.get('lang', 'ru'))


@register.simple_tag(takes_context=True)
def disease_name(context, disease):
    return getattr(disease, 'name_' + context.get('lang', 'ru'), disease.name_ru)


@register.filter
def get_item(value, key):
    return value.get(key, '') if isinstance(value, dict) else ''


@register.simple_tag(takes_context=True)
def query_update(context, **kwargs):
    query = context['request'].GET.copy()
    for key, value in kwargs.items():
        query[key] = value
    return query.urlencode()


@register.simple_tag(takes_context=True)
def localized_widget(context, field):
    language = context.get('lang', 'ru')
    widget = deepcopy(field.field.widget)
    if hasattr(widget, 'choices'):
        choices = []
        for value, label in widget.choices:
            obj = getattr(value, 'instance', None)
            if obj is not None and hasattr(obj, 'name_' + language):
                label = getattr(obj, 'name_' + language)
            elif isinstance(label, (list, tuple)):
                label = [(v, tr(l, language)) for v, l in label]
            else:
                label = tr(label, language)
            choices.append((value, label))
        widget.choices = choices
    return field.as_widget(widget=widget)
