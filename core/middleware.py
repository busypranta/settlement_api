import time
import uuid
import logging

from django.http import JsonResponse
from apps.api_logs.models import ApiLog


logger = logging.getLogger(__name__)


class APILogMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        api_path = request.path.startswith(('/v1/api/', '/api/portal/', '/api/auth/'))
        if api_path:
            start = time.perf_counter()
            request_id = str(uuid.uuid4())
            request.request_id = request_id
            try:
                response = self.get_response(request)
            except Exception as exc:
                logger.error(
                    'Unhandled API exception type=%s request_id=%s',
                    type(exc).__name__,
                    request_id,
                )
                response = JsonResponse(
                    {
                        'success': False,
                        'error': 'Internal server error',
                        'request_id': request_id,
                    },
                    status=500,
                )
            renderer_media_type = getattr(getattr(response, 'accepted_renderer', None), 'media_type', '')
            content_type = (
                renderer_media_type or response.get('Content-Type', '')
            ).split(';', 1)[0].lower()
            if not content_type.endswith('/json') and '+json' not in content_type:
                status_code = response.status_code
                if status_code < 400:
                    status_code = 502
                logger.warning(
                    'Non-JSON API response replaced path=%s status=%s request_id=%s',
                    request.path,
                    response.status_code,
                    request_id,
                )
                response = JsonResponse(
                    {
                        'success': False,
                        'error': 'The API could not complete this request. Please try again or contact support.',
                        'request_id': request_id,
                    },
                    status=status_code,
                )
            response_data = getattr(response, 'data', None)
            if response.status_code >= 400 and isinstance(response_data, dict):
                response_data.setdefault('request_id', request_id)
            duration_ms = int((time.perf_counter() - start) * 1000)
            result_data = response_data.get('data') if isinstance(response_data, dict) else None
            result_count = len(result_data) if isinstance(result_data, (list, tuple)) else None
            merchant_uid = None
            path_parts = request.path.strip('/').split('/')
            if len(path_parts) == 4 and path_parts[:2] == ['v1', 'api']:
                if path_parts[2] in ('settlement-report', 'settlement-transactions'):
                    merchant_uid = path_parts[3]
            page_number = None
            page_value = request.GET.get('page')
            if page_value and page_value.isdecimal():
                page_number = int(page_value)
            try:
                user = getattr(request, 'user', None)
                ApiLog.objects.create(
                    user=user if user and user.is_authenticated else None,
                    request_id=request_id,
                    endpoint=request.path,
                    http_method=request.method,
                    http_status=response.status_code,
                    duration_ms=duration_ms,
                    client_ip=request.META.get('REMOTE_ADDR'),
                    merchant_uid=merchant_uid,
                    page_number=page_number,
                    result_count=result_count,
                    error_code=f'HTTP_{response.status_code}' if response.status_code >= 400 else None,
                )
            except Exception:
                logger.exception('Could not persist API request log request_id=%s', request_id)
            response['X-Request-ID'] = request_id
            return response
        return self.get_response(request)
