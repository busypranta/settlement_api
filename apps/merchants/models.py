from django.db import models
from apps.accounts.models import User


class PortalMerchant(models.Model):
    merchant_uid = models.CharField(max_length=64, unique=True)
    merchant_name = models.CharField(max_length=150, unique=True)
    merchant_name_key = models.CharField(max_length=150, unique=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'merchant'

    def save(self, *args, **kwargs):
        self.merchant_uid = self.merchant_uid.strip()
        self.merchant_name = ' '.join(self.merchant_name.strip().split())
        self.merchant_name_key = ' '.join(self.merchant_name.casefold().split())
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.merchant_uid} - {self.merchant_name}'


class UserMerchant(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    merchant = models.ForeignKey(PortalMerchant, on_delete=models.CASCADE)
    assigned_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='merchant_assignments_made')
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'user_merchant'
        unique_together = (('user', 'merchant'),)


class AdminUserAssignment(models.Model):
    admin = models.ForeignKey(User, on_delete=models.CASCADE, related_name='admin_assignments')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='assigned_to_admins')
    assigned_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='admin_user_assignments_made')
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'admin_user_assignment'
        unique_together = (('admin', 'user'),)
