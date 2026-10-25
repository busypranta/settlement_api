from django.urls import path
from . import views

urlpatterns = [
    path('settlement-report', views.SettlementReportMultiView.as_view()),
    path('settlement-report/<str:merchant_uid>', views.SettlementReportSingleView.as_view()),
    path('settlement-transactions/<str:merchant_uid>', views.SettlementTransactionsView.as_view()),
]
