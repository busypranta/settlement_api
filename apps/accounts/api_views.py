from datetime import timedelta
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from django.utils import timezone

from apps.api_logs.models import ApiLog
from apps.audit.models import AuditLog
from apps.merchants.models import AdminUserAssignment, PortalMerchant, UserMerchant
from apps.settlement_api.permissions import (
    can_manage_merchant,
    can_manage_user,
    managed_user_ids,
)
from .tokens import revoke_user_refresh_tokens
from .forms import ProfileForm
from .models import User


def ensure_password_valid(password, user=None):
    try:
        validate_password(password, user)
    except DjangoValidationError as exc:
        raise serializers.ValidationError({'password': exc.messages}) from exc


def record_audit(request, action, target=None, metadata=None):
    AuditLog.objects.create(
        actor=request.user,
        action=action,
        target_type=target._meta.label_lower if target is not None else None,
        target_id=str(target.pk) if target is not None else None,
        request_id=getattr(request, 'request_id', None),
        metadata=metadata or {},
    )


def serialize_user(user):
    return {
        'id': user.pk,
        'username': user.username,
        'name': user.name,
        'mobile': user.mobile,
        'email': user.email,
        'role': user.role,
        'is_active': user.is_active,
        'created_at': user.created_at,
    }


def serialize_merchant(merchant):
    return {
        'merchant_uid': merchant.merchant_uid,
        'merchant_name': merchant.merchant_name,
        'is_active': merchant.is_active,
    }


class ProfileView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(serialize_user(request.user))

    def patch(self, request):
        allowed = {'name', 'mobile', 'email'}
        if set(request.data) - allowed:
            raise serializers.ValidationError('Only name, mobile, and email may be updated here')
        profile_form = ProfileForm(request.data, instance=request.user, partial=True)
        if not profile_form.is_valid():
            raise serializers.ValidationError(profile_form.errors)
        profile_form.save()
        return Response(serialize_user(request.user))


class PasswordChangeView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        current_password = request.data.get('current_password', '')
        new_password = request.data.get('new_password', '')
        if not request.user.check_password(current_password):
            raise serializers.ValidationError({'current_password': 'Current password is incorrect'})
        if not new_password:
            raise serializers.ValidationError({'new_password': 'This field is required'})
        ensure_password_valid(new_password, request.user)
        request.user.set_password(new_password)
        with transaction.atomic():
            request.user.save(update_fields=['password', 'updated_at'])
            revoke_user_refresh_tokens(request.user)
        record_audit(request, 'user.password_changed', request.user)
        return Response({'success': True})


class UserListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role == User.ROLE_SUPERADMIN or request.user.is_superuser:
            users = User.objects.all()
        elif request.user.role == User.ROLE_ADMIN:
            users = User.objects.filter(pk__in=managed_user_ids(request.user))
        else:
            return Response({'error': 'Administration access required'}, status=status.HTTP_403_FORBIDDEN)
        return Response([serialize_user(user) for user in users.order_by('username')])

    def post(self, request):
        actor = request.user
        if actor.role not in (User.ROLE_ADMIN, User.ROLE_SUPERADMIN) and not actor.is_superuser:
            return Response({'error': 'Administration access required'}, status=status.HTTP_403_FORBIDDEN)
        role = request.data.get('role', User.ROLE_USER)
        if role not in (User.ROLE_USER, User.ROLE_ADMIN) or (
            role == User.ROLE_ADMIN and actor.role != User.ROLE_SUPERADMIN and not actor.is_superuser
        ):
            return Response({'error': 'You cannot create an account with this role'}, status=status.HTTP_403_FORBIDDEN)
        required = ('username', 'password')
        if any(not request.data.get(field) for field in required):
            raise serializers.ValidationError({'detail': 'username and password are required'})
        if 'is_superuser' in request.data or 'is_staff' in request.data:
            raise serializers.ValidationError('Privilege flags cannot be set through this endpoint')

        password = request.data['password']
        ensure_password_valid(password)
        details = {
            field: request.data[field]
            for field in ('name', 'mobile', 'email')
            if field in request.data
        }
        details_form = ProfileForm(details, instance=User())
        if not details_form.is_valid():
            raise serializers.ValidationError(details_form.errors)
        details = details_form.cleaned_data
        try:
            user = User.objects.create_user(
                username=request.data['username'],
                email=details.get('email', ''),
                password=password,
                name=details.get('name', ''),
                mobile=details.get('mobile', ''),
                role=role,
                created_by=actor,
                is_staff=False,
                is_superuser=False,
            )
        except IntegrityError:
            raise serializers.ValidationError({'username': 'This username is already in use'})
        record_audit(request, 'user.created', user, {'role': role})
        return Response(serialize_user(user), status=status.HTTP_201_CREATED)


class UserDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get_target(self, request, user_id):
        target = get_object_or_404(User, pk=user_id)
        if not can_manage_user(request.user, target):
            return None
        return target

    def get(self, request, user_id):
        target = self.get_target(request, user_id)
        if target is None:
            return Response({'error': 'Not authorized'}, status=status.HTTP_403_FORBIDDEN)
        return Response(serialize_user(target))

    def patch(self, request, user_id):
        target = self.get_target(request, user_id)
        if target is None:
            return Response({'error': 'Not authorized'}, status=status.HTTP_403_FORBIDDEN)
        allowed = {'email', 'name', 'mobile', 'is_active'}
        if set(request.data) - allowed:
            if set(request.data) - (allowed | {'role'}):
                raise serializers.ValidationError('Only name, mobile, email, active state, and authorized role changes are allowed')
        details = {field: request.data[field] for field in ('email', 'name', 'mobile') if field in request.data}
        details_form = ProfileForm(details, instance=target, partial=True)
        if not details_form.is_valid():
            raise serializers.ValidationError(details_form.errors)
        for field, value in details_form.cleaned_data.items():
            if field in details:
                setattr(target, field, value)
        if 'role' in request.data:
            if (request.user.role != User.ROLE_SUPERADMIN and not request.user.is_superuser) or request.data['role'] not in (
                User.ROLE_ADMIN, User.ROLE_USER,
            ):
                raise serializers.ValidationError({'role': 'Only SUPERADMIN may set ADMIN or USER roles'})
            target.role = request.data['role']
            target.is_staff = False
            target.is_superuser = False
        if 'is_active' in request.data:
            if not isinstance(request.data['is_active'], bool):
                raise serializers.ValidationError({'is_active': 'Expected a boolean value'})
            target.is_active = request.data['is_active']
        update_fields = list(allowed.intersection(request.data)) + ['updated_at']
        if 'role' in request.data:
            update_fields.extend(['role', 'is_staff', 'is_superuser'])
        target.save(update_fields=update_fields)
        record_audit(request, 'user.updated', target, {'fields': sorted(set(request.data))})
        return Response(serialize_user(target))


class UserPasswordResetView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, user_id):
        target = get_object_or_404(User, pk=user_id)
        if not can_manage_user(request.user, target):
            return Response({'error': 'Not authorized'}, status=status.HTTP_403_FORBIDDEN)
        password = request.data.get('password', '')
        if not password:
            raise serializers.ValidationError({'password': 'This field is required'})
        ensure_password_valid(password, target)
        target.set_password(password)
        with transaction.atomic():
            target.save(update_fields=['password', 'updated_at'])
            revoke_user_refresh_tokens(target)
        record_audit(request, 'user.password_reset', target)
        return Response({'success': True})


class MerchantListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        merchants = PortalMerchant.objects.all()
        if request.user.role == User.ROLE_USER:
            merchants = merchants.filter(usermerchant__user=request.user, is_active=True)
        elif request.user.role == User.ROLE_ADMIN and not request.user.is_superuser:
            scoped_user_ids = managed_user_ids(request.user)
            merchants = merchants.filter(
                Q(created_by=request.user) | Q(usermerchant__user=request.user)
                | Q(usermerchant__user_id__in=scoped_user_ids),
            ).distinct()
        elif request.user.role != User.ROLE_SUPERADMIN and not request.user.is_superuser:
            return Response({'error': 'Not authorized'}, status=status.HTTP_403_FORBIDDEN)
        return Response([serialize_merchant(item) for item in merchants.order_by('merchant_uid')])

    def post(self, request):
        if request.user.role not in (User.ROLE_ADMIN, User.ROLE_SUPERADMIN) and not request.user.is_superuser:
            return Response({'error': 'Administration access required'}, status=status.HTTP_403_FORBIDDEN)
        merchant_uid = request.data.get('merchant_uid', '').strip()
        merchant_name = request.data.get('merchant_name', '').strip()
        if not merchant_uid or not merchant_name:
            raise serializers.ValidationError({'detail': 'merchant_uid and merchant_name are required'})
        merchant = PortalMerchant(
            merchant_uid=merchant_uid,
            merchant_name=merchant_name,
            merchant_name_key=' '.join(merchant_name.casefold().split()),
            created_by=request.user,
        )
        try:
            with transaction.atomic():
                merchant.save()
        except IntegrityError:
            raise serializers.ValidationError({'detail': 'Merchant UID or normalized name is already registered'})
        record_audit(request, 'merchant.created', merchant)
        return Response(serialize_merchant(merchant), status=status.HTTP_201_CREATED)


class MerchantDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, merchant_uid):
        if request.user.role not in (User.ROLE_ADMIN, User.ROLE_SUPERADMIN) and not request.user.is_superuser:
            return Response({'error': 'Administration access required'}, status=status.HTTP_403_FORBIDDEN)
        merchant = get_object_or_404(PortalMerchant, merchant_uid=merchant_uid)
        if not can_manage_merchant(request.user, merchant):
            return Response({'error': 'Not authorized to manage this merchant'}, status=status.HTTP_403_FORBIDDEN)
        allowed = {'merchant_name', 'is_active'}
        if set(request.data) - allowed:
            raise serializers.ValidationError('Only merchant_name and is_active may be updated')
        if 'merchant_name' in request.data:
            if not isinstance(request.data['merchant_name'], str) or not request.data['merchant_name'].strip():
                raise serializers.ValidationError({'merchant_name': 'A non-empty name is required'})
            merchant.merchant_name = request.data['merchant_name']
        if 'is_active' in request.data:
            if not isinstance(request.data['is_active'], bool):
                raise serializers.ValidationError({'is_active': 'Expected a boolean value'})
            merchant.is_active = request.data['is_active']
        try:
            with transaction.atomic():
                merchant.save()
        except IntegrityError:
            raise serializers.ValidationError({'merchant_name': 'This normalized merchant name is already registered'})
        record_audit(request, 'merchant.updated', merchant, {'fields': sorted(set(request.data))})
        return Response(serialize_merchant(merchant))


class MerchantAssignmentView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, merchant_uid):
        merchant = get_object_or_404(PortalMerchant, merchant_uid=merchant_uid, is_active=True)
        user = get_object_or_404(User, pk=request.data.get('user_id'))
        if user.role != User.ROLE_USER or not can_manage_user(request.user, user):
            return Response({'error': 'Not authorized to assign this user'}, status=status.HTTP_403_FORBIDDEN)
        if not can_manage_merchant(request.user, merchant):
            return Response({'error': 'Not authorized to assign this merchant'}, status=status.HTTP_403_FORBIDDEN)
        if request.user.role not in (User.ROLE_ADMIN, User.ROLE_SUPERADMIN) and not request.user.is_superuser:
            return Response({'error': 'Administration access required'}, status=status.HTTP_403_FORBIDDEN)
        assignment, created = UserMerchant.objects.get_or_create(
            user=user, merchant=merchant, defaults={'assigned_by': request.user},
        )
        if created:
            record_audit(request, 'merchant.assigned', assignment, {
                'merchant_uid': merchant.merchant_uid,
                'user_id': user.pk,
            })
        return Response({'assigned': True}, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    def delete(self, request, merchant_uid):
        merchant = get_object_or_404(PortalMerchant, merchant_uid=merchant_uid)
        user = get_object_or_404(User, pk=request.data.get('user_id'))
        if user.role != User.ROLE_USER or not can_manage_user(request.user, user):
            return Response({'error': 'Not authorized to manage this user'}, status=status.HTTP_403_FORBIDDEN)
        if not can_manage_merchant(request.user, merchant):
            return Response({'error': 'Not authorized to manage this merchant'}, status=status.HTTP_403_FORBIDDEN)
        if request.user.role not in (User.ROLE_ADMIN, User.ROLE_SUPERADMIN) and not request.user.is_superuser:
            return Response({'error': 'Administration access required'}, status=status.HTTP_403_FORBIDDEN)
        assignment = get_object_or_404(UserMerchant, user=user, merchant=merchant)
        record_audit(request, 'merchant.unassigned', assignment, {
            'merchant_uid': merchant.merchant_uid,
            'user_id': user.pk,
        })
        assignment.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class AdminUserAssignmentView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role != User.ROLE_SUPERADMIN and not request.user.is_superuser:
            return Response({'error': 'SUPERADMIN access required'}, status=status.HTTP_403_FORBIDDEN)
        assignments = AdminUserAssignment.objects.select_related('admin', 'user').order_by('admin__username', 'user__username')
        return Response([{
            'admin_id': item.admin_id,
            'admin_username': item.admin.username,
            'user_id': item.user_id,
            'user_username': item.user.username,
            'assigned_at': item.assigned_at,
        } for item in assignments])

    def post(self, request):
        if request.user.role != User.ROLE_SUPERADMIN and not request.user.is_superuser:
            return Response({'error': 'SUPERADMIN access required'}, status=status.HTTP_403_FORBIDDEN)
        admin = get_object_or_404(User, pk=request.data.get('admin_id'), role=User.ROLE_ADMIN)
        user = get_object_or_404(User, pk=request.data.get('user_id'), role=User.ROLE_USER)
        assignment, created = AdminUserAssignment.objects.get_or_create(
            admin=admin, user=user, defaults={'assigned_by': request.user},
        )
        if created:
            record_audit(request, 'admin_user.assigned', assignment, {
                'admin_id': admin.pk,
                'user_id': user.pk,
            })
        return Response({'assigned': True}, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    def delete(self, request):
        if request.user.role != User.ROLE_SUPERADMIN and not request.user.is_superuser:
            return Response({'error': 'SUPERADMIN access required'}, status=status.HTTP_403_FORBIDDEN)
        assignment = get_object_or_404(
            AdminUserAssignment,
            admin_id=request.data.get('admin_id'),
            user_id=request.data.get('user_id'),
        )
        record_audit(request, 'admin_user.unassigned', assignment)
        assignment.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ApiLogListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        logs = ApiLog.objects.select_related('user')
        if request.user.role == User.ROLE_SUPERADMIN or request.user.is_superuser:
            pass
        elif request.user.role == User.ROLE_ADMIN:
            logs = logs.filter(user_id__in=managed_user_ids(request.user)) | logs.filter(user=request.user)
        else:
            logs = logs.filter(user=request.user)
        data = [{
            'request_id': item.request_id,
            'endpoint': item.endpoint,
            'http_method': item.http_method,
            'http_status': item.http_status,
            'duration_ms': item.duration_ms,
            'merchant_uid': item.merchant_uid,
            'page_number': item.page_number,
            'result_count': item.result_count,
            'error_code': item.error_code,
            'created_at': item.created_at,
        } for item in logs.order_by('-created_at')[:500]]
        return Response(data)


class AuditLogListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role not in (User.ROLE_ADMIN, User.ROLE_SUPERADMIN) and not request.user.is_superuser:
            return Response({'error': 'Administration access required'}, status=status.HTTP_403_FORBIDDEN)
        logs = AuditLog.objects.select_related('actor')
        if request.user.role != User.ROLE_SUPERADMIN and not request.user.is_superuser:
            logs = logs.filter(actor_id__in=managed_user_ids(request.user)) | logs.filter(actor=request.user)
        return Response([{
            'actor_id': item.actor_id,
            'action': item.action,
            'target_type': item.target_type,
            'target_id': item.target_id,
            'request_id': item.request_id,
            'metadata': item.metadata,
            'created_at': item.created_at,
        } for item in logs.order_by('-created_at')[:500]])


class DashboardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = request.user
        if actor.role == User.ROLE_SUPERADMIN or actor.is_superuser:
            user_scope = User.objects.all()
            merchant_scope = PortalMerchant.objects.all()
            log_scope = ApiLog.objects.all()
        elif actor.role == User.ROLE_ADMIN:
            user_scope = User.objects.filter(pk__in=managed_user_ids(actor))
            merchant_scope = PortalMerchant.objects.filter(
                Q(created_by=actor) | Q(usermerchant__user=actor)
                | Q(usermerchant__user_id__in=managed_user_ids(actor)),
            ).distinct()
            log_scope = ApiLog.objects.filter(
                Q(user_id__in=managed_user_ids(actor)) | Q(user=actor),
            )
        else:
            user_scope = User.objects.filter(pk=actor.pk)
            merchant_scope = PortalMerchant.objects.filter(usermerchant__user=actor)
            log_scope = ApiLog.objects.filter(user=actor)

        return Response({
            'role': actor.role,
            'users': {
                'total': user_scope.count(),
                'active': user_scope.filter(is_active=True).count(),
                'inactive': user_scope.filter(is_active=False).count(),
            },
            'merchants': {
                'total': merchant_scope.count(),
                'active': merchant_scope.filter(is_active=True).count(),
                'inactive': merchant_scope.filter(is_active=False).count(),
            },
            'api_calls': {
                'total': log_scope.count(),
                'failures_last_24_hours': log_scope.filter(
                    http_status__gte=400,
                    created_at__gte=timezone.now() - timedelta(hours=24),
                ).count(),
            },
        })
