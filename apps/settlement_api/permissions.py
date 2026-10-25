from rest_framework.permissions import BasePermission

from apps.merchants.models import AdminUserAssignment, PortalMerchant, UserMerchant


def managed_user_ids(admin):
    explicitly_assigned = AdminUserAssignment.objects.filter(
        admin=admin, user__role='USER',
    ).values_list('user_id', flat=True)
    created_by_admin = admin.created_users.filter(role='USER').values_list('pk', flat=True)
    return explicitly_assigned.union(created_by_admin)


def can_manage_user(actor, target):
    if actor.is_superuser or actor.role == 'SUPERADMIN':
        return True
    if actor.role != 'ADMIN' or target.role != 'USER':
        return False
    return target.pk in managed_user_ids(actor)


def merchant_is_allowed(user, merchant):
    if not user.is_authenticated or not merchant.is_active:
        return False
    if user.is_superuser or user.role == 'SUPERADMIN':
        return True
    if user.role == 'USER':
        return UserMerchant.objects.filter(user=user, merchant=merchant).exists()
    if user.role == 'ADMIN':
        scoped_users = managed_user_ids(user)
        return (
            UserMerchant.objects.filter(user=user, merchant=merchant).exists()
            or UserMerchant.objects.filter(user_id__in=scoped_users, merchant=merchant).exists()
        )
    return False


def can_manage_merchant(user, merchant):
    if user.role == 'SUPERADMIN' or user.is_superuser:
        return True
    if user.role != 'ADMIN':
        return False

    assigned_user_ids = set(
        UserMerchant.objects.filter(merchant=merchant).values_list('user_id', flat=True),
    )
    if not assigned_user_ids:
        return merchant.created_by_id == user.pk
    scoped_ids = set(managed_user_ids(user))
    return assigned_user_ids.issubset(scoped_ids) and (
        merchant.created_by_id == user.pk or bool(assigned_user_ids.intersection(scoped_ids))
    )


class HasMerchantAccess(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        return isinstance(obj, PortalMerchant) and merchant_is_allowed(request.user, obj)
