from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
    TokenVerifyView,
)
from drf_spectacular.views import SpectacularSwaggerView
from rest_framework.permissions import AllowAny
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.authentication import SessionAuthentication
from django.db import connections, DatabaseError
from rest_framework_simplejwt.authentication import JWTAuthentication
from config.schema import RoleFilteredSchemaView

DOC_AUTHENTICATION = (SessionAuthentication, JWTAuthentication)
DOC_PERMISSIONS = (IsAuthenticated,)


class HealthView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        try:
            for alias in ('default', 'source'):
                with connections[alias].cursor() as cursor:
                    cursor.execute('SELECT 1 FROM DUAL')
                    cursor.fetchone()
        except DatabaseError:
            return Response({'status': 'unavailable'}, status=503)
        return Response({'status': 'ok'})

urlpatterns = [
    path('health/', HealthView.as_view(), name='health'),
    path('admin/', admin.site.urls),
    path('api/auth/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/auth/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('api/auth/token/verify/', TokenVerifyView.as_view(), name='token_verify'),
    path('api/schema/', RoleFilteredSchemaView.as_view(
        authentication_classes=DOC_AUTHENTICATION,
        permission_classes=DOC_PERMISSIONS,
    ), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(
        url_name='schema',
        authentication_classes=DOC_AUTHENTICATION,
        permission_classes=DOC_PERMISSIONS,
    ), name='swagger-ui'),
    path('v1/api/', include('apps.settlement_api.urls')),
    path('api/portal/', include('apps.accounts.urls_api')),
    path('portal/', include('apps.accounts.urls_web')),
    path('', RedirectView.as_view(url='/portal/', permanent=False)),
]
