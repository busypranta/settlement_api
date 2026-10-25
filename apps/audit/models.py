from django.db import models
from apps.accounts.models import User


class AuditLog(models.Model):
    actor = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=128)
    target_type = models.CharField(max_length=64, null=True, blank=True)
    target_id = models.CharField(max_length=64, null=True, blank=True)
    request_id = models.CharField(max_length=64, null=True, blank=True)
    metadata = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'audit_log'
        ordering = ['-created_at']
