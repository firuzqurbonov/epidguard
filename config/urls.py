from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path
from surveillance import views
from surveillance.forms import SecureAuthenticationForm

urlpatterns = [
    path('admin/', admin.site.urls),
    path('login/', auth_views.LoginView.as_view(authentication_form=SecureAuthenticationForm, redirect_authenticated_user=True), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('', views.dashboard, name='dashboard'),
    path('cases/', views.case_list, name='cases'),
    path('cases/new/', views.case_create, name='case_create'),
    path('cases/<uuid:pk>/', views.case_detail, name='case_detail'),
    path('cases/<uuid:pk>/edit/', views.case_edit, name='case_edit'),
    path('cases/<uuid:pk>/action/', views.case_action, name='case_action'),
    path('cases/<uuid:pk>/contacts/add/', views.related_create, {'kind': 'contacts'}, name='contact_create'),
    path('cases/<uuid:pk>/measures/add/', views.related_create, {'kind': 'measures'}, name='measure_create'),
    path('cases/<uuid:pk>/laboratory/add/', views.related_create, {'kind': 'laboratory'}, name='lab_create'),
    path('cases/<uuid:pk>/documents/', views.upload_attachment, name='attachment_upload'),
    path('documents/<int:pk>/', views.download_attachment, name='attachment_download'),
    path('inbox/', views.inbox, name='inbox'),
    path('contacts/', views.related_list, {'kind': 'contacts'}, name='contacts'),
    path('contacts/<int:pk>/edit/', views.contact_edit, name='contact_edit'),
    path('measures/', views.related_list, {'kind': 'measures'}, name='measures'),
    path('measures/<int:pk>/complete/', views.measure_complete, name='measure_complete'),
    path('laboratory/', views.related_list, {'kind': 'laboratory'}, name='laboratory'),
    path('outbreaks/', views.outbreaks, name='outbreaks'),
    path('outbreaks/new/', views.outbreak_create, name='outbreak_create'),
    path('outbreaks/<int:pk>/', views.outbreak_detail, name='outbreak_detail'),
    path('signals/', views.signals, name='signals'),
    path('signals/new/', views.signal_edit, name='signal_create'),
    path('signals/<int:pk>/', views.signal_edit, name='signal_edit'),
    path('reports/', views.reports, name='reports'),
    path('map/', views.map_view, name='map'),
    path('diseases/', views.disease_catalog, name='diseases'),
    path('api/diseases/<int:pk>/questionnaire/', views.questionnaire_api, name='questionnaire_api'),
    path('audit/', views.audit_view, name='audit'),
    path('language/', views.set_language, name='language'),
    path('settings/', views.settings_view, name='settings'),
    path('security/', views.security, name='security'),
]
