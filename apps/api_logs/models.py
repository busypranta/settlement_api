from django.db import models
from apps.accounts.models import User


class ApiLog(models.Model):
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    request_id = models.CharField(max_length=64, unique=True)
    endpoint = models.CharField(max_length=255)
    http_method = models.CharField(max_length=10)
    http_status = models.IntegerField()
    duration_ms = models.IntegerField(null=True, blank=True)
    merchant_uid = models.CharField(max_length=64, null=True, blank=True)
    page_number = models.IntegerField(null=True, blank=True)
    result_count = models.IntegerField(null=True, blank=True)
    client_ip = models.GenericIPAddressField(null=True, blank=True)
    error_code = models.CharField(max_length=64, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'api_log'
        ordering = ['-created_at']
