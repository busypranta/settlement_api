from django.db.models import Q
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


def merchant_access_ids(user, merchants):
    merchant_ids = {merchant.pk for merchant in merchants if merchant.is_active}
    if not user.is_authenticated or not merchant_ids:
        return set()
    if user.is_superuser or user.role == 'SUPERADMIN':
        return merchant_ids
    assignments = UserMerchant.objects.filter(
        merchant_id__in=merchant_ids,
    )
    if user.role == 'USER':
        assignments = assignments.filter(user=user)
    elif user.role == 'ADMIN':
        assignments = assignments.filter(
            Q(user=user) | Q(user_id__in=managed_user_ids(user)),
        )
    else:
        return set()
    return set(assignments.values_list('merchant_id', flat=True).distinct())


def manageable_merchant_ids(user, merchants):
    if user.is_superuser or user.role == 'SUPERADMIN':
        return {merchant.pk for merchant in merchants}
    if user.role != 'ADMIN' or not merchants:
        return set()

    merchant_by_id = {merchant.pk: merchant for merchant in merchants}
    assignments_by_merchant = {}
    assignments = UserMerchant.objects.filter(
        merchant_id__in=merchant_by_id,
    ).values_list('merchant_id', 'user_id')
    for merchant_id, user_id in assignments:
        assignments_by_merchant.setdefault(merchant_id, set()).add(user_id)

    managed_ids = set(managed_user_ids(user))
    manageable = set()
    for merchant_id, merchant in merchant_by_id.items():
        assigned_user_ids = assignments_by_merchant.get(merchant_id, set())
        if not assigned_user_ids:
            if merchant.created_by_id == user.pk:
                manageable.add(merchant_id)
        elif assigned_user_ids.issubset(managed_ids):
            manageable.add(merchant_id)
    return manageable


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
