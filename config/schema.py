from drf_spectacular.views import SpectacularAPIView


class RoleFilteredSchemaView(SpectacularAPIView):
    def _get_schema_response(self, request):
        response = super()._get_schema_response(request)
        user = getattr(request, 'user', None)
        if not user or not user.is_authenticated or (
            user.role != 'SUPERADMIN' and not user.is_superuser
        ):
            response.data['paths'] = {
                path: operations
                for path, operations in response.data.get('paths', {}).items()
                if not path.startswith('/api/portal/')
            }
        return response
