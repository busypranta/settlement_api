from django.urls import path

from .api_views import (
    AdminUserAssignmentView,
    ApiLogListView,
    AuditLogListView,
    DashboardView,
    MerchantAssignmentView,
    MerchantDetailView,
    MerchantListCreateView,
    PasswordChangeView,
    ProfileView,
    UserDetailView,
    UserListCreateView,
    UserPasswordResetView,
)

urlpatterns = [
    path('dashboard/', DashboardView.as_view(), name='portal-dashboard-api'),
    path('profile/', ProfileView.as_view(), name='portal-profile-api'),
    path('profile/password/', PasswordChangeView.as_view(), name='portal-password-change-api'),
    path('users/', UserListCreateView.as_view(), name='portal-users-api'),
    path('users/<int:user_id>/', UserDetailView.as_view(), name='portal-user-detail-api'),
    path('users/<int:user_id>/password-reset/', UserPasswordResetView.as_view(), name='portal-user-password-reset-api'),
    path('admin-user-assignments/', AdminUserAssignmentView.as_view(), name='portal-admin-user-assignments-api'),
    path('merchants/', MerchantListCreateView.as_view(), name='portal-merchants-api'),
    path('merchants/<str:merchant_uid>/', MerchantDetailView.as_view(), name='portal-merchant-detail-api'),
    path('merchants/<str:merchant_uid>/assignments/', MerchantAssignmentView.as_view(), name='portal-merchant-assignments-api'),
    path('logs/', ApiLogListView.as_view(), name='portal-api-logs-api'),
    path('audit-logs/', AuditLogListView.as_view(), name='portal-audit-logs-api'),
]
