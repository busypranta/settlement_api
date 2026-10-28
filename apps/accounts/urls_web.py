from django.contrib.auth import views as auth_views
from django.urls import path

from . import web_views

urlpatterns = [
    path('login/', auth_views.LoginView.as_view(
        template_name='portal/login.html',
        redirect_authenticated_user=True,
    ), name='portal-login'),
    path('logout/', auth_views.LogoutView.as_view(), name='portal-logout'),
    path('', web_views.dashboard, name='portal-dashboard'),
    path('logs/', web_views.api_logs, name='portal-api-logs-page'),
    path('playground/', web_views.api_playground, name='portal-api-playground'),
    path('merchants/', web_views.merchant_access, name='portal-merchant-access'),
    path('accounts/', web_views.managed_accounts, name='portal-managed-accounts'),
    path('accounts/create/', web_views.create_account, name='portal-create-account'),
    path('profile/', web_views.profile, name='portal-profile-page'),
]
