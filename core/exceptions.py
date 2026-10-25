from rest_framework.views import exception_handler


def api_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        return None

    request = context.get('request')
    response.data = {
        'success': False,
        'errors': response.data,
        'request_id': getattr(request, 'request_id', None),
    }
    return response
