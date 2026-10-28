from django.contrib.auth.models import AbstractUser
from django.db import models
from django.core.validators import MinLengthValidator
from .managers import UserManager
from .validators import validate_bd_mobile


class User(AbstractUser):
    ROLE_SUPERADMIN = 'SUPERADMIN'
    ROLE_ADMIN = 'ADMIN'
    ROLE_USER = 'USER'
    ROLE_CHOICES = (
        (ROLE_SUPERADMIN, 'Super Admin'),
        (ROLE_ADMIN, 'Admin'),
        (ROLE_USER, 'User'),
    )

    name = models.CharField(max_length=100, blank=True, validators=[MinLengthValidator(2)])
    mobile = models.CharField(max_length=16, blank=True, validators=[validate_bd_mobile])
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_USER)
    allow_multiple_merchants = models.BooleanField(default=False)
    created_by = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='created_users')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    class Meta:
        db_table = 'portal_user'

    def is_superadmin(self):
        return self.role == self.ROLE_SUPERADMIN

    def is_admin(self):
        return self.role == self.ROLE_ADMIN

    def is_portal_user(self):
        return self.role == self.ROLE_USER

    def get_full_name(self):
        return self.name.strip() or super().get_full_name().strip() or self.username
