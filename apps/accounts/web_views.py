from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import redirect, render

from apps.api_logs.models import ApiLog
from apps.audit.models import AuditLog
from apps.merchants.models import AdminUserAssignment, PortalMerchant, UserMerchant
from apps.settlement_api.permissions import (
    can_manage_merchant,
    can_manage_user,
    managed_user_ids,
    merchant_is_allowed,
)
from .forms import (
    AdminPasswordResetForm,
    AdminUserAssignmentForm,
    MerchantAssignmentForm,
    MerchantCreateForm,
    PortalPasswordChangeForm,
    ProfileForm,
    UserCreateForm,
)
from .models import User


def scoped_users(user):
    if user.role == User.ROLE_SUPERADMIN or user.is_superuser:
        return User.objects.all()
    if user.role == User.ROLE_ADMIN:
        return User.objects.filter(pk__in=managed_user_ids(user))
    return User.objects.none()


def scoped_merchants(user):
    if user.role == User.ROLE_SUPERADMIN or user.is_superuser:
        return PortalMerchant.objects.all()
    if user.role == User.ROLE_ADMIN:
        return PortalMerchant.objects.filter(
            Q(created_by=user) | Q(usermerchant__user=user)
            | Q(usermerchant__user_id__in=managed_user_ids(user)),
        ).distinct()
    return PortalMerchant.objects.filter(usermerchant__user=user)


def scoped_logs(user):
    if user.role == User.ROLE_SUPERADMIN or user.is_superuser:
        return ApiLog.objects.select_related('user')
    if user.role == User.ROLE_ADMIN:
        return ApiLog.objects.select_related('user').filter(
            Q(user_id__in=managed_user_ids(user)) | Q(user=user),
        )
    return ApiLog.objects.filter(user=user)


def audit_action(request, action, target=None, metadata=None):
    AuditLog.objects.create(
        actor=request.user,
        action=action,
        target_type=target._meta.label_lower if target is not None else None,
        target_id=str(target.pk) if target is not None else None,
        request_id=getattr(request, 'request_id', None),
        metadata=metadata or {},
    )


@login_required
def dashboard(request):
    users = scoped_users(request.user)
    merchants = scoped_merchants(request.user)
    logs = scoped_logs(request.user)
    active_merchants = merchants.filter(is_active=True)
    user_assignments = UserMerchant.objects.filter(
        user__in=users,
    ).select_related('user', 'merchant')
    context = {
        'user_count': users.count(),
        'active_user_count': users.filter(is_active=True).count(),
        'merchant_count': active_merchants.count(),
        'api_call_count': logs.count(),
        'failed_call_count': logs.filter(http_status__gte=400).count(),
        'recent_logs': logs.order_by('-created_at')[:8],
        'managed_users': users.order_by('username')[:8],
        'user_assignments': user_assignments[:8],
        'assigned_merchants': active_merchants.order_by('merchant_name')[:8],
        'is_admin': request.user.role in (User.ROLE_ADMIN, User.ROLE_SUPERADMIN) or request.user.is_superuser,
    }
    return render(request, 'portal/dashboard.html', context)


@login_required
def api_logs(request):
    logs = scoped_logs(request.user).order_by('-created_at')
    status_filter = request.GET.get('status', '')
    if status_filter == 'failed':
        logs = logs.filter(http_status__gte=400)
    elif status_filter == 'success':
        logs = logs.filter(http_status__lt=400)
    paginator = Paginator(logs, 25)
    page = paginator.get_page(request.GET.get('page'))
    return render(request, 'portal/logs.html', {
        'page_obj': page,
        'status_filter': status_filter,
    })


@login_required
def api_playground(request):
    merchants = [
        merchant
        for merchant in scoped_merchants(request.user).filter(is_active=True).order_by('merchant_name')
        if merchant_is_allowed(request.user, merchant)
    ]
    can_use_multi_merchant_api = (
        request.user.role != User.ROLE_USER
        or request.user.allow_multiple_merchants
    )
    return render(request, 'portal/playground.html', {
        'playground_merchants': merchants,
        'can_use_multi_merchant_api': can_use_multi_merchant_api and len(merchants) > 1,
    })

@login_required
def merchant_access(request):
    merchants = scoped_merchants(request.user).order_by('merchant_name')
    if request.user.role == User.ROLE_USER:
        merchants = merchants.filter(usermerchant__user=request.user)
    is_admin = request.user.role in (User.ROLE_ADMIN, User.ROLE_SUPERADMIN) or request.user.is_superuser
    merchant_form = MerchantCreateForm()
    assignment_form = MerchantAssignmentForm(
        users=scoped_users(request.user),
        merchants=scoped_merchants(request.user),
    )
    if request.method == 'POST':
        action = request.POST.get('action')
        if not is_admin:
            messages.error(request, 'You do not have permission to manage merchants.')
            return redirect('portal-merchant-access')

        if action == 'create_merchant':
            merchant_form = MerchantCreateForm(request.POST)
            if merchant_form.is_valid():
                merchant = merchant_form.save(commit=False)
                merchant.created_by = request.user
                try:
                    with transaction.atomic():
                        merchant.save()
                except IntegrityError:
                    merchant_form.add_error(None, 'That merchant UID or normalized name is already registered.')
                else:
                    audit_action(request, 'merchant.created', merchant)
                    messages.success(request, f'{merchant.merchant_name} was added.')
                    return redirect('portal-merchant-access')

        elif action == 'assign_merchant':
            assignment_form = MerchantAssignmentForm(
                request.POST,
                users=scoped_users(request.user),
                merchants=scoped_merchants(request.user),
            )
            if assignment_form.is_valid():
                account = assignment_form.cleaned_data['user']
                merchant = assignment_form.cleaned_data['merchant']
                if not can_manage_user(request.user, account):
                    messages.error(request, 'That account is outside your management scope.')
                else:
                    assignment, created = UserMerchant.objects.get_or_create(
                        user=account,
                        merchant=merchant,
                        defaults={'assigned_by': request.user},
                    )
                    if created:
                        audit_action(request, 'merchant.assigned', assignment, {
                            'user_id': account.pk,
                            'merchant_uid': merchant.merchant_uid,
                        })
                        messages.success(request, f'{merchant.merchant_name} was assigned to {account.username}.')
                    else:
                        messages.info(request, 'That merchant is already assigned to this account.')
                    return redirect('portal-merchant-access')

        elif action == 'toggle_merchant':
            merchant = PortalMerchant.objects.filter(pk=request.POST.get('merchant_id')).first()
            if merchant is None:
                messages.error(request, 'Merchant not found.')
            elif not can_manage_merchant(request.user, merchant):
                messages.error(request, 'You cannot change this merchant because it is outside your scope.')
            else:
                merchant.is_active = not merchant.is_active
                merchant.save(update_fields=['is_active', 'updated_at'])
                audit_action(request, 'merchant.activated' if merchant.is_active else 'merchant.deactivated', merchant)
                messages.success(request, f'{merchant.merchant_name} is now {"active" if merchant.is_active else "inactive"}.')
                return redirect('portal-merchant-access')
        else:
            messages.error(request, 'Unknown merchant action.')

    merchant_rows = merchants.distinct().prefetch_related('usermerchant_set__user')
    manageable_merchant_ids = {
        merchant.pk for merchant in merchant_rows
        if can_manage_merchant(request.user, merchant)
    }
    return render(request, 'portal/merchants.html', {
        'merchants': merchant_rows,
        'is_admin': is_admin,
        'merchant_form': merchant_form,
        'assignment_form': assignment_form,
        'manageable_merchant_ids': manageable_merchant_ids,
    })


@login_required
def create_account(request):
    if request.user.role not in (User.ROLE_ADMIN, User.ROLE_SUPERADMIN) and not request.user.is_superuser:
        messages.error(request, 'Account administration is available to admins only.')
        return redirect('portal-dashboard')

    user_form = UserCreateForm(actor=request.user)
    if request.method == 'POST':
        user_form = UserCreateForm(request.POST, actor=request.user)
        if user_form.is_valid():
            try:
                with transaction.atomic():
                    account = user_form.save()
            except IntegrityError:
                user_form.add_error('username', 'This username is already in use.')
            else:
                audit_action(request, 'user.created', account, {'role': account.role})
                messages.success(request, f'Account {account.username} was created.')
                return redirect('portal-managed-accounts')
    return render(request, 'portal/create_account.html', {'user_form': user_form})


@login_required
def managed_accounts(request):
    if request.user.role not in (User.ROLE_ADMIN, User.ROLE_SUPERADMIN) and not request.user.is_superuser:
        messages.error(request, 'Account administration is available to admins only.')
        return redirect('portal-dashboard')
    all_users = scoped_users(request.user)
    users = all_users
    query = request.GET.get('q', '').strip()[:100]
    role_filter = request.GET.get('role', '')
    status_filter = request.GET.get('status', '')
    if query:
        users = users.filter(
            Q(username__icontains=query)
            | Q(name__icontains=query)
            | Q(email__icontains=query)
            | Q(mobile__icontains=query)
        )
    if role_filter in (User.ROLE_ADMIN, User.ROLE_USER, User.ROLE_SUPERADMIN):
        users = users.filter(role=role_filter)
    if status_filter == 'active':
        users = users.filter(is_active=True)
    elif status_filter == 'inactive':
        users = users.filter(is_active=False)
    users = users.prefetch_related('usermerchant_set__merchant').order_by('username')
    page_obj = Paginator(users, 25).get_page(request.GET.get('page'))

    merchant_assignment_form = MerchantAssignmentForm(
        users=all_users,
        merchants=scoped_merchants(request.user),
    )
    is_superadmin = request.user.role == User.ROLE_SUPERADMIN or request.user.is_superuser
    admin_assignment_form = AdminUserAssignmentForm() if is_superadmin else None
    show_merchant_tools = False

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'toggle_user':
            account = User.objects.filter(pk=request.POST.get('user_id')).first()
            if account is None or not can_manage_user(request.user, account):
                messages.error(request, 'That account is outside your management scope.')
            elif account.role == User.ROLE_SUPERADMIN:
                messages.error(request, 'SUPERADMIN accounts cannot be changed here.')
            else:
                account.is_active = not account.is_active
                account.save(update_fields=['is_active', 'updated_at'])
                audit_action(request, 'user.activated' if account.is_active else 'user.deactivated', account)
                messages.success(request, f'{account.username} is now {"active" if account.is_active else "inactive"}.')
                return redirect('portal-managed-accounts')

        elif action == 'toggle_multi_merchant_access':
            account = User.objects.filter(pk=request.POST.get('user_id')).first()
            if (
                account is None
                or not can_manage_user(request.user, account)
                or account.role != User.ROLE_USER
            ):
                messages.error(request, 'That account is outside your multiple-merchant access scope.')
            else:
                account.allow_multiple_merchants = not account.allow_multiple_merchants
                account.save(update_fields=['allow_multiple_merchants', 'updated_at'])
                audit_action(
                    request,
                    'user.multiple_merchant_access_enabled'
                    if account.allow_multiple_merchants
                    else 'user.multiple_merchant_access_disabled',
                    account,
                )
                state = 'enabled' if account.allow_multiple_merchants else 'disabled'
                messages.success(request, f'Multiple-merchant API access is {state} for {account.username}.')
                return redirect('portal-managed-accounts')

        elif action == 'reset_password':
            account = User.objects.filter(pk=request.POST.get('user_id')).first()
            if account is None or not can_manage_user(request.user, account) or account.role == User.ROLE_SUPERADMIN:
                messages.error(request, 'That account is outside your password-reset scope.')
            else:
                password_form = AdminPasswordResetForm(
                    request.POST,
                    user=account,
                )
                if password_form.is_valid():
                    account.set_password(password_form.cleaned_data['password'])
                    account.save(update_fields=['password', 'updated_at'])
                    audit_action(request, 'user.password_reset', account)
                    messages.success(request, f'A new password was set for {account.username}.')
                    return redirect('portal-managed-accounts')
                messages.error(request, 'Password was not changed. Use a stronger password and try again.')

        elif action == 'assign_merchant':
            show_merchant_tools = is_superadmin
            merchant_assignment_form = MerchantAssignmentForm(
                request.POST,
                users=all_users,
                merchants=scoped_merchants(request.user),
            )
            if merchant_assignment_form.is_valid():
                account = merchant_assignment_form.cleaned_data['user']
                merchant = merchant_assignment_form.cleaned_data['merchant']
                assignment, created = UserMerchant.objects.get_or_create(
                    user=account,
                    merchant=merchant,
                    defaults={'assigned_by': request.user},
                )
                if created:
                    audit_action(request, 'merchant.assigned', assignment, {
                        'user_id': account.pk,
                        'merchant_uid': merchant.merchant_uid,
                    })
                    messages.success(request, f'{merchant.merchant_name} was assigned to {account.username}.')
                else:
                    messages.info(request, 'That merchant is already assigned to this account.')
                return redirect('portal-managed-accounts')

        elif action == 'unassign_merchant':
            account = User.objects.filter(pk=request.POST.get('user_id')).first()
            assignment = UserMerchant.objects.select_related('merchant').filter(
                pk=request.POST.get('assignment_id'),
                user=account,
            ).first() if account else None
            if account is None or not can_manage_user(request.user, account) or assignment is None:
                messages.error(request, 'That assignment is outside your management scope.')
            else:
                audit_action(request, 'merchant.unassigned', assignment, {
                    'user_id': account.pk,
                    'merchant_uid': assignment.merchant.merchant_uid,
                })
                assignment.delete()
                messages.success(request, f'{assignment.merchant.merchant_name} was removed from {account.username}.')
                return redirect('portal-managed-accounts')

        elif action in ('assign_admin_user', 'unassign_admin_user') and is_superadmin:
            admin_assignment_form = AdminUserAssignmentForm(request.POST)
            if admin_assignment_form.is_valid():
                admin = admin_assignment_form.cleaned_data['admin']
                account = admin_assignment_form.cleaned_data['user']
                if action == 'assign_admin_user':
                    assignment, created = AdminUserAssignment.objects.get_or_create(
                        admin=admin,
                        user=account,
                        defaults={'assigned_by': request.user},
                    )
                    if created:
                        audit_action(request, 'admin_user.assigned', assignment, {
                            'admin_id': admin.pk,
                            'user_id': account.pk,
                        })
                        messages.success(request, f'{account.username} was assigned to {admin.username}.')
                    else:
                        messages.info(request, 'That user is already assigned to this admin.')
                else:
                    assignment = AdminUserAssignment.objects.filter(admin=admin, user=account).first()
                    if assignment:
                        audit_action(request, 'admin_user.unassigned', assignment)
                        assignment.delete()
                        messages.success(request, f'{account.username} was removed from {admin.username}.')
                    else:
                        messages.info(request, 'That admin-user assignment does not exist.')
                return redirect('portal-managed-accounts')

        else:
            messages.error(request, 'Unknown account action.')

    explicit_assignments = AdminUserAssignment.objects.none()
    if is_superadmin:
        explicit_assignments = AdminUserAssignment.objects.select_related('admin', 'user').order_by(
            'admin__username', 'user__username',
        )
    return render(request, 'portal/users.html', {
        'users': page_obj.object_list,
        'page_obj': page_obj,
        'query': query,
        'role_filter': role_filter,
        'status_filter': status_filter,
        'account_count': all_users.count(),
        'admin_count': all_users.filter(role=User.ROLE_ADMIN).count(),
        'user_count': all_users.filter(role=User.ROLE_USER).count(),
        'inactive_count': all_users.filter(is_active=False).count(),
        'merchant_assignment_form': merchant_assignment_form,
        'is_superadmin': is_superadmin,
        'admin_assignment_form': admin_assignment_form,
        'admin_assignments': explicit_assignments,
        'show_merchant_tools': show_merchant_tools,
        'can_manage_user': can_manage_user,
    })


@login_required
def profile(request):
    profile_form = ProfileForm(instance=request.user)
    password_form = PortalPasswordChangeForm(user=request.user)
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'profile':
            profile_form = ProfileForm(request.POST, instance=request.user)
            if profile_form.is_valid():
                profile_form.save()
                messages.success(request, 'Your profile was updated.')
                return redirect('portal-profile-page')
        elif action == 'password':
            password_form = PortalPasswordChangeForm(user=request.user, data=request.POST)
            if password_form.is_valid():
                user = password_form.save()
                update_session_auth_hash(request, user)
                messages.success(request, 'Your password was changed.')
                return redirect('portal-profile-page')
    return render(request, 'portal/profile.html', {
        'profile_form': profile_form,
        'password_form': password_form,
    })
