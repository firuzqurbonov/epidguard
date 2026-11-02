from django.contrib import admin
from .models import Territory, Service, Route, Profile, DiseaseGroup, Disease, Questionnaire, Audit
from .services import audit

admin.site.site_header = 'EpiGuard · Администрирование'
admin.site.site_title = 'EpiGuard'


class RestrictedAdmin(admin.ModelAdmin):
    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        before = {}
        if change:
            old = type(obj).objects.get(pk=obj.pk)
            before = {field.attname: str(getattr(old, field.attname)) for field in old._meta.fields if field.name not in ['totp_secret', 'totp_last_step']}
            for field in old._meta.many_to_many:
                before[field.name] = list(getattr(old, field.name).values_list('pk', flat=True))
        super().save_model(request, obj, form, change)
        after = {field.attname: str(getattr(obj, field.attname)) for field in obj._meta.fields if field.name not in ['totp_secret', 'totp_last_step']}
        for field in obj._meta.many_to_many:
            after[field.name] = [item.pk for item in form.cleaned_data.get(field.name, [])]
        audit(request.user, 'Изменение настройки', detail=f'{obj._meta.label} #{obj.pk}', before=before, after=after)


@admin.register(Disease)
class DiseaseAdmin(RestrictedAdmin):
    list_display = ['code', 'name_ru', 'name_tg', 'high_consequence', 'terminology_review', 'active']
    search_fields = ['code', 'name_ru', 'name_tg']
    filter_horizontal = ['groups']


@admin.register(Profile)
class ProfileAdmin(RestrictedAdmin):
    list_display = ['user', 'role', 'approved', 'institution', 'totp_enabled']
    list_filter = ['role', 'approved']
    filter_horizontal = ['services']
    # TOTP reset is a separate management command; secrets never appear in admin.
    exclude = ['totp_secret', 'totp_last_step']
    readonly_fields = ['totp_enabled']


@admin.register(Questionnaire)
class QuestionnaireAdmin(RestrictedAdmin):
    list_display = ['disease', 'version', 'approved', 'effective_from']


for model in [Territory, Service, Route, DiseaseGroup]:
    admin.site.register(model, RestrictedAdmin)
