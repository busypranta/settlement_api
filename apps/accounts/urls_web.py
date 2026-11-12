from functools import wraps
from django.contrib.auth import views as auth_views
from django.http import HttpResponse
from django.urls import path

from . import web_views

def noindex(view_func):
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        response = view_func(request, *args, **kwargs)
        response["X-Robots-Tag"] = "noindex, nofollow, noarchive, nosnippet, noimageindex"
        return response
    return _wrapped_view

def robots_txt(request):
    lines = [
        "User-agent: *",
        "Disallow: /admin/",
        "Disallow: /portal/",
        "Disallow: /api/",
        # Keep /portal/login/ allowed so bots see the X-Robots-Tag: noindex header
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain")

urlpatterns = [
    path('login/', noindex(auth_views.LoginView.as_view(
        template_name='portal/login.html',
        redirect_authenticated_user=True,
    )), name='portal-login'),
    path("robots.txt", robots_txt),
    path('logout/', noindex(auth_views.LogoutView.as_view()), name='portal-logout'),
    path('', web_views.dashboard, name='portal-dashboard'),
    path('logs/', web_views.api_logs, name='portal-api-logs-page'),
    path('playground/', web_views.api_playground, name='portal-api-playground'),
    path('merchants/', web_views.merchant_access, name='portal-merchant-access'),
    path('accounts/', web_views.managed_accounts, name='portal-managed-accounts'),
    path('accounts/create/', web_views.create_account, name='portal-create-account'),
    path('profile/', web_views.profile, name='portal-profile-page'),
]
