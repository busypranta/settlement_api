from django.conf import settings
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.api_logs.models import ApiLog
from apps.audit.models import AuditLog
from apps.merchants.models import AdminUserAssignment, PortalMerchant, UserMerchant
from apps.settlement_api.permissions import (
    can_manage_merchant,
    can_manage_user,
    manageable_merchant_ids,
    managed_user_ids,
    merchant_access_ids,
)
from . import merchant_sync as sync_service
from .forms import (
    AccountEditForm,
    AdminPasswordResetForm,
    AdminUserAssignmentForm,
    MerchantAssignmentForm,
    MerchantBulkAssignmentForm,
    MerchantCreateForm,
    MerchantEditForm,
    PortalPasswordChangeForm,
    ProfileForm,
    UserCreateForm,
)
from .models import User
from .tokens import revoke_user_refresh_tokens


class PrecountedQuerySet:
    """Minimal wrapper that lets Paginator reuse an already-known count.

    Django's Paginator issues its own COUNT(*) each time it is constructed.
    The merchant page already calculates the filtered total in its summary
    aggregate, so wrapping the queryset avoids a second, near-identical query.
    """

    def __init__(self, queryset, count):
        self.queryset = queryset
        self._count = count

    def count(self):
        return self._count

    @property
    def ordered(self):
        return self.queryset.ordered

    def __getitem__(self, key):
        return self.queryset[key]


def _is_superadmin(user):
    return bool(user.is_superuser or user.role == User.ROLE_SUPERADMIN)


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
    return ApiLog.objects.select_related('user').filter(user=user)


def audit_action(request, action, target=None, metadata=None):
    AuditLog.objects.create(
        actor=request.user,
        action=action,
        target_type=target._meta.label_lower if target is not None else None,
        target_id=str(target.pk) if target is not None else None,
        request_id=getattr(request, 'request_id', None),
        metadata=metadata or {},
    )


def assign_merchants(request, account, merchants):
    """Create any missing merchant grants and audit each new assignment."""
    merchant_ids = [merchant.pk for merchant in merchants]
    existing_ids = set(
        UserMerchant.objects.filter(
            user=account,
            merchant_id__in=merchant_ids,
        ).values_list('merchant_id', flat=True)
    )
    created_merchants = [
        merchant for merchant in merchants if merchant.pk not in existing_ids
    ]
    if created_merchants:
        assignments = [
            UserMerchant(user=account, merchant=merchant, assigned_by=request.user)
            for merchant in created_merchants
        ]
        UserMerchant.objects.bulk_create(assignments)
        for assignment, merchant in zip(assignments, created_merchants):
            audit_action(request, 'merchant.assigned', assignment, {
                'user_id': account.pk,
                'merchant_uid': merchant.merchant_uid,
            })
    return created_merchants


@login_required
def dashboard(request):
    merchants = scoped_merchants(request.user)
    logs = scoped_logs(request.user)
    active_merchants = merchants.filter(is_active=True)
    is_admin = request.user.role in (User.ROLE_ADMIN, User.ROLE_SUPERADMIN) or request.user.is_superuser

    log_stats = logs.aggregate(
        api_call_count=Count('pk'),
        failed_call_count=Count('pk', filter=Q(http_status__gte=400)),
    )
    context = {
        'merchant_count': active_merchants.count(),
        'api_call_count': log_stats['api_call_count'] or 0,
        'failed_call_count': log_stats['failed_call_count'] or 0,
        'recent_logs': logs.order_by('-created_at')[:8],
        'assigned_merchants': active_merchants.order_by('merchant_name')[:8],
        'is_admin': is_admin,
    }
    if is_admin:
        user_stats = scoped_users(request.user).aggregate(
            user_count=Count('pk'),
            active_user_count=Count('pk', filter=Q(is_active=True)),
        )
        context.update(user_stats)
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
    merchants = scoped_merchants(request.user).filter(is_active=True)
    allowed_merchant_ids = merchant_access_ids(request.user, merchants)
    merchants = merchants.filter(pk__in=allowed_merchant_ids)
    merchant_count = merchants.count()
    can_use_multi_merchant_api = (
        request.user.role != User.ROLE_USER
        or request.user.allow_multiple_merchants
    )
    can_use_settlement_transactions_api = (
        request.user.role != User.ROLE_USER
        or request.user.allow_settlement_transactions
    )
    return render(request, 'portal/playground.html', {
        'has_playground_merchants': merchant_count > 0,
        'playground_merchant_options_url': reverse('portal-playground-merchant-options'),
        'can_use_multi_merchant_api': can_use_multi_merchant_api and merchant_count > 1,
        'can_use_settlement_transactions_api': can_use_settlement_transactions_api,
    })

@login_required
def merchant_options(request):
    """Session-authenticated merchant options for the grant-access dropdown."""
    is_admin = request.user.role in (User.ROLE_ADMIN, User.ROLE_SUPERADMIN) or request.user.is_superuser
    if not is_admin:
        return JsonResponse({'error': 'Not allowed.'}, status=403)

    query = request.GET.get('q', '').strip()[:100]
    merchant_id = request.GET.get('id', '').strip()
    try:
        offset = max(0, int(request.GET.get('offset', 0)))
    except (TypeError, ValueError):
        offset = 0
    page_size = 10

    merchants = scoped_merchants(request.user).filter(is_active=True)
    if merchant_id.isdigit():
        merchants = merchants.filter(pk=merchant_id)
    elif query:
        merchants = merchants.filter(
            Q(merchant_name__icontains=query) | Q(merchant_uid__icontains=query),
        )
    merchants = merchants.order_by('merchant_name').distinct()
    total = merchants.count()

    assigned_ids = set()
    user_id = request.GET.get('user_id', '').strip()
    if user_id.isdigit():
        account = User.objects.filter(pk=user_id, role=User.ROLE_USER).first()
        if account is not None and can_manage_user(request.user, account):
            assigned_ids = set(
                UserMerchant.objects.filter(user=account).values_list('merchant_id', flat=True),
            )

    rows = list(merchants[offset:offset + page_size])
    results = [
        {
            'id': merchant.pk,
            'name': merchant.merchant_name,
            'uid': merchant.merchant_uid,
            'assigned': merchant.pk in assigned_ids,
        }
        for merchant in rows
    ]
    next_offset = offset + len(results)
    return JsonResponse({
        'results': results,
        'total': total,
        'next_offset': next_offset,
        'has_more': next_offset < total,
        'page_size': page_size,
    })


@login_required
def playground_merchant_options(request):
    """Lazy, searchable merchant options scoped to the signed-in playground user."""
    query = request.GET.get('q', '').strip()[:100]
    try:
        offset = max(0, int(request.GET.get('offset', 0)))
    except (TypeError, ValueError):
        offset = 0
    page_size = 10

    merchants = scoped_merchants(request.user).filter(is_active=True)
    allowed_ids = merchant_access_ids(request.user, merchants)
    merchants = merchants.filter(pk__in=allowed_ids)
    if query:
        merchants = merchants.filter(
            Q(merchant_name__icontains=query) | Q(merchant_uid__icontains=query),
        )
    merchants = merchants.order_by('merchant_name').distinct()
    total = merchants.count()

    results = [
        {
            'id': merchant.pk,
            'name': merchant.merchant_name,
            'uid': merchant.merchant_uid,
            'assigned': False,
        }
        for merchant in merchants[offset:offset + page_size]
    ]
    next_offset = offset + len(results)
    return JsonResponse({
        'results': results,
        'total': total,
        'next_offset': next_offset,
        'has_more': next_offset < total,
        'page_size': page_size,
    })


def _sync_offset(request):
    try:
        return max(0, int(request.session.get(sync_service.OFFSET_KEY, 0)))
    except (TypeError, ValueError):
        return 0


def _save_sync_preview(request, preview):
    """Store the remaining batch, or advance the offset when it is finished."""
    if preview['items']:
        sync_service.refresh_counts(preview)
        request.session[sync_service.SESSION_KEY] = preview
    else:
        request.session.pop(sync_service.SESSION_KEY, None)
        request.session[sync_service.OFFSET_KEY] = preview['next_offset']
    request.session.modified = True


def _sync_preview_message(preview, skipped=0):
    message = (
        f'Read {preview["row_count"]} source row(s): '
        f'{preview["create_count"]} new, {preview["update_count"]} to update.'
    )
    if skipped:
        message += f' {skipped} row(s) were skipped (missing UID/name or duplicates).'
    return message


@login_required
def merchant_sync(request):
    """Preview one batch of source merchants, then apply it."""
    if not _is_superadmin(request.user):
        messages.error(request, 'Only super admins can sync merchants.')
        return redirect('portal-merchant-access')

    preview = request.session.get(sync_service.SESSION_KEY)
    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'fetch_database':
            offset = _sync_offset(request)
            try:
                rows, skipped, fetched = sync_service.fetch_database_rows(offset=offset)
                new_preview = sync_service.build_preview(rows, offset, offset + fetched)
                if not fetched:
                    messages.info(
                        request,
                        'No more source merchants found. Use Start over to scan from the beginning.',
                    )
                    request.session.pop(sync_service.SESSION_KEY, None)
                elif not new_preview['items']:
                    messages.info(
                        request,
                        f'{len(rows)} source row(s) read; no new or changed merchants.',
                    )
                    _save_sync_preview(request, new_preview)
                else:
                    request.session[sync_service.SESSION_KEY] = new_preview
                    request.session.modified = True
                    messages.success(request, _sync_preview_message(new_preview, skipped))
            except sync_service.SyncError as exc:
                messages.error(request, str(exc))
            return redirect('portal-merchant-sync')

        if action == 'apply_one':
            if not preview:
                messages.error(request, 'No preview is available. Sync a batch first.')
                return redirect('portal-merchant-sync')

            key = request.POST.get('row_key', '')
            item = next((row for row in preview['items'] if row['key'] == key), None)
            if item is None:
                messages.error(request, 'That preview row was not found. Sync the batch again.')
            elif item.get('error'):
                messages.error(request, f'{item["uid"]}: {item["error"]}')
            else:
                try:
                    merchant, applied_action = sync_service.apply_item(item, request.user)
                except sync_service.SyncApplyError as exc:
                    item['error'] = str(exc)
                    _save_sync_preview(request, preview)
                    messages.error(request, f'{item["uid"]}: {exc}')
                else:
                    audit_action(
                        request,
                        'merchant.created' if applied_action == 'create' else 'merchant.updated',
                        merchant,
                        {'source': 'sync', 'sync_source': preview['source']},
                    )
                    preview['items'] = [row for row in preview['items'] if row['key'] != key]
                    _save_sync_preview(request, preview)
                    verb = 'created' if applied_action == 'create' else 'updated'
                    messages.success(request, f'{merchant.merchant_name} was {verb}.')
            return redirect('portal-merchant-sync')

        if action == 'apply_all':
            if not preview:
                messages.error(request, 'No preview is available. Sync a batch first.')
                return redirect('portal-merchant-sync')

            applied_keys = set()
            errors = []
            created_count = 0
            updated_count = 0
            # Update existing names first so a rename can free up a name that a
            # new row wants to use.
            ordered = sorted(preview['items'], key=lambda row: (row['action'] != 'update', row['key']))
            for item in ordered:
                if item.get('error'):
                    errors.append(f'{item["uid"]}: {item["error"]}')
                    continue
                try:
                    merchant, applied_action = sync_service.apply_item(item, request.user)
                except sync_service.SyncApplyError as exc:
                    item['error'] = str(exc)
                    errors.append(f'{item["uid"]}: {exc}')
                    continue
                applied_keys.add(item['key'])
                if applied_action == 'create':
                    created_count += 1
                else:
                    updated_count += 1
                audit_action(
                    request,
                    'merchant.created' if applied_action == 'create' else 'merchant.updated',
                    merchant,
                    {'source': 'sync', 'sync_source': preview['source']},
                )

            preview['items'] = [row for row in preview['items'] if row['key'] not in applied_keys]
            _save_sync_preview(request, preview)

            messages.success(
                request,
                f'Created {created_count} and updated {updated_count} merchant(s).',
            )
            if errors:
                messages.error(request, 'Could not apply: ' + '; '.join(errors[:5]))
            return redirect('portal-merchant-sync')

        if action == 'clear_preview':
            request.session.pop(sync_service.SESSION_KEY, None)
            request.session.modified = True
            messages.info(request, 'Merchant sync preview cleared.')
            return redirect('portal-merchant-sync')

        if action == 'reset_offset':
            request.session.pop(sync_service.SESSION_KEY, None)
            request.session[sync_service.OFFSET_KEY] = 0
            request.session.modified = True
            messages.info(request, 'Merchant sync restarted from the first batch.')
            return redirect('portal-merchant-sync')

        messages.error(request, 'Unknown merchant sync action.')
        return redirect('portal-merchant-sync')

    return render(request, 'portal/merchant_sync.html', {
        'preview': preview,
        'source_schema': getattr(settings, 'SOURCE_SCHEMA', ''),
        'batch_size': sync_service.batch_size(),
        'offset': _sync_offset(request),
    })


@login_required
def merchant_access(request):
    merchants = scoped_merchants(request.user).order_by('merchant_name')
    if request.user.role == User.ROLE_USER:
        merchants = merchants.filter(usermerchant__user=request.user)
    query = request.GET.get('q', '').strip()[:100]
    query_filter = (
        Q(merchant_name__icontains=query) | Q(merchant_uid__icontains=query)
        if query else Q()
    )
    # Calculate the filtered total once and let Paginator reuse it instead of
    # issuing a second, near-identical COUNT(*) query.
    filtered_merchants = merchants.aggregate(
        filtered=Count('pk', distinct=True, filter=query_filter),
    )['filtered'] or 0
    is_admin = request.user.role in (User.ROLE_ADMIN, User.ROLE_SUPERADMIN) or request.user.is_superuser
    merchants = merchants.filter(query_filter).distinct()

    merchant_form = MerchantCreateForm()
    # The edit form lives in a dialog below the create form. Give its fields
    # distinct ids so the dialog script never updates the create form instead.
    edit_merchant_form = MerchantEditForm(auto_id='id_edit_%s')
    edit_merchant_open = False
    assignment_form = MerchantAssignmentForm(
        users=scoped_users(request.user),
        merchants=scoped_merchants(request.user),
        lazy_merchant=True,
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
                lazy_merchant=True,
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

        elif action == 'edit_merchant':
            merchant = PortalMerchant.objects.filter(pk=request.POST.get('merchant_id')).first()
            if merchant is None:
                messages.error(request, 'Merchant not found.')
            elif not can_manage_merchant(request.user, merchant):
                messages.error(request, 'You cannot edit this merchant because it is outside your scope.')
            else:
                before = {
                    'merchant_name': merchant.merchant_name,
                    'merchant_uid': merchant.merchant_uid,
                }
                edit_merchant_form = MerchantEditForm(
                    request.POST,
                    instance=merchant,
                    auto_id='id_edit_%s',
                )
                if edit_merchant_form.is_valid():
                    try:
                        with transaction.atomic():
                            updated = edit_merchant_form.save()
                    except IntegrityError:
                        edit_merchant_form.add_error(None, 'That merchant UID or normalized name is already registered.')
                    else:
                        audit_action(request, 'merchant.updated', updated, {
                            'before': before,
                            'after': {
                                'merchant_name': updated.merchant_name,
                                'merchant_uid': updated.merchant_uid,
                            },
                        })
                        messages.success(request, f'{updated.merchant_name} was updated.')
                        return redirect('portal-merchant-access')
                edit_merchant_open = True

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

    page_obj = Paginator(PrecountedQuerySet(merchants, filtered_merchants), 25).get_page(request.GET.get('page'))
    merchant_rows = list(page_obj.object_list)
    manageable_ids = manageable_merchant_ids(request.user, merchant_rows)
    return render(request, 'portal/merchants.html', {
        'merchants': merchant_rows,
        'page_obj': page_obj,
        'query': query,
        'is_admin': is_admin,
        'can_sync_merchants': _is_superadmin(request.user),
        'merchant_form': merchant_form,
        'edit_merchant_form': edit_merchant_form,
        'edit_merchant_open': edit_merchant_open,
        'assignment_form': assignment_form,
        'lazy_merchant_select': True,
        'manageable_merchant_ids': manageable_ids,
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
    query = request.GET.get('q', '').strip()[:100]
    role_filter = request.GET.get('role', '')
    status_filter = request.GET.get('status', '')
    list_filter = Q()
    if query:
        list_filter |= (
            Q(username__icontains=query)
            | Q(name__icontains=query)
            | Q(email__icontains=query)
            | Q(mobile__icontains=query)
        )
    if role_filter in (User.ROLE_ADMIN, User.ROLE_USER, User.ROLE_SUPERADMIN):
        list_filter &= Q(role=role_filter)
    if status_filter == 'active':
        list_filter &= Q(is_active=True)
    elif status_filter == 'inactive':
        list_filter &= Q(is_active=False)

    # One aggregate supplies the summary counts and the paginator total,
    # replacing five separate COUNT(*) queries on the same table.
    account_totals = all_users.aggregate(
        total=Count('pk'),
        admin=Count('pk', filter=Q(role=User.ROLE_ADMIN)),
        user=Count('pk', filter=Q(role=User.ROLE_USER)),
        inactive=Count('pk', filter=Q(is_active=False)),
        filtered=Count('pk', filter=list_filter),
    )
    filtered_users = account_totals['filtered'] or 0
    users = all_users.filter(list_filter).prefetch_related('usermerchant_set__merchant').order_by('username')
    page_obj = Paginator(PrecountedQuerySet(users, filtered_users), 25).get_page(request.GET.get('page'))

    # Both assignment forms need the same role=USER options. Sharing one
    # queryset lets Django cache the results after the first evaluation.
    assignable_users = all_users.filter(role=User.ROLE_USER).order_by('username')
    merchant_assignment_form = MerchantBulkAssignmentForm(
        users=all_users,
        user_queryset=assignable_users,
        merchants=scoped_merchants(request.user),
    )
    is_superadmin = request.user.role == User.ROLE_SUPERADMIN or request.user.is_superuser
    admin_assignment_form = AdminUserAssignmentForm(user_queryset=assignable_users) if is_superadmin else None
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

        elif action == 'toggle_settlement_transactions':
            account = User.objects.filter(pk=request.POST.get('user_id')).first()
            if (
                account is None
                or not can_manage_user(request.user, account)
                or account.role != User.ROLE_USER
            ):
                messages.error(request, 'That account is outside your transaction API access scope.')
            else:
                account.allow_settlement_transactions = not account.allow_settlement_transactions
                account.save(update_fields=['allow_settlement_transactions', 'updated_at'])
                audit_action(
                    request,
                    'user.settlement_transactions_enabled'
                    if account.allow_settlement_transactions
                    else 'user.settlement_transactions_disabled',
                    account,
                )
                state = 'enabled' if account.allow_settlement_transactions else 'disabled'
                messages.success(request, f'Settlement transactions API access is {state} for {account.username}.')
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
                    with transaction.atomic():
                        account.save(update_fields=['password', 'updated_at'])
                        revoke_user_refresh_tokens(account)
                    audit_action(request, 'user.password_reset', account)
                    messages.success(request, f'A new password was set for {account.username}.')
                    return redirect('portal-managed-accounts')
                messages.error(request, 'Password was not changed. Use a stronger password and try again.')

        elif action == 'assign_merchant':
            show_merchant_tools = is_superadmin
            merchant_assignment_form = MerchantBulkAssignmentForm(
                request.POST,
                users=all_users,
                user_queryset=assignable_users,
                merchants=scoped_merchants(request.user),
            )
            if merchant_assignment_form.is_valid():
                account = merchant_assignment_form.cleaned_data['user']
                assigned_merchants = merchant_assignment_form.cleaned_data['merchants']
                created = assign_merchants(request, account, assigned_merchants)
                if created:
                    names = ', '.join(merchant.merchant_name for merchant in created)
                    messages.success(request, f'Merchant access granted to {account.username}: {names}.')
                else:
                    messages.info(request, 'Those merchants are already assigned to this account.')
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
            admin_assignment_form = AdminUserAssignmentForm(request.POST, user_queryset=assignable_users)
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
    # Both assignment forms render the same role=USER options. Materialize them
    # once so the second select reuses the same choices instead of re-querying.
    shared_user_choices = [choice for choice in merchant_assignment_form.fields['user'].choices]
    merchant_assignment_form.fields['user'].choices = shared_user_choices
    if admin_assignment_form is not None:
        admin_assignment_form.fields['user'].choices = shared_user_choices
    return render(request, 'portal/users.html', {
        'users': page_obj.object_list,
        'page_obj': page_obj,
        'query': query,
        'role_filter': role_filter,
        'status_filter': status_filter,
        'account_count': account_totals['total'] or 0,
        'admin_count': account_totals['admin'] or 0,
        'user_count': account_totals['user'] or 0,
        'inactive_count': account_totals['inactive'] or 0,
        'merchant_assignment_form': merchant_assignment_form,
        'is_superadmin': is_superadmin,
        'admin_assignment_form': admin_assignment_form,
        'admin_assignments': explicit_assignments,
        'show_merchant_tools': show_merchant_tools,
        'can_manage_user': can_manage_user,
        'merchant_options_url': reverse('portal-merchant-options'),
    })


@login_required
def account_detail(request, user_id):
    """Single-account workspace for editing details and managing access."""
    if request.user.role not in (User.ROLE_ADMIN, User.ROLE_SUPERADMIN) and not request.user.is_superuser:
        messages.error(request, 'Account administration is available to admins only.')
        return redirect('portal-dashboard')

    all_users = scoped_users(request.user).select_related('created_by')
    account = get_object_or_404(all_users, pk=user_id)
    is_superadmin = request.user.role == User.ROLE_SUPERADMIN or request.user.is_superuser
    is_user = account.role == User.ROLE_USER
    can_edit = can_manage_user(request.user, account)
    detail_url = reverse('portal-account-detail', args=[account.pk])

    assignable_users = all_users.filter(role=User.ROLE_USER).order_by('username')
    merchant_queryset = scoped_merchants(request.user)
    edit_form = AccountEditForm(instance=account, actor=request.user)
    bulk_form = MerchantBulkAssignmentForm(
        users=all_users,
        user_queryset=assignable_users,
        merchants=merchant_queryset,
        fixed_user=account,
    )
    admin_form = (
        AdminUserAssignmentForm(user_queryset=assignable_users, fixed_user=account)
        if is_superadmin and is_user else None
    )

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'edit_account':
            edit_form = AccountEditForm(request.POST, instance=account, actor=request.user)
            if not can_edit:
                messages.error(request, 'That account is outside your management scope.')
            elif edit_form.is_valid():
                before_role = account.role
                updated = edit_form.save()
                audit_action(request, 'user.updated', updated, {
                    'before_role': before_role,
                    'after_role': updated.role,
                })
                messages.success(request, f'Account {updated.username} was updated.')
                return redirect(detail_url)

        elif action == 'toggle_user':
            if not can_edit or account.role == User.ROLE_SUPERADMIN:
                messages.error(request, 'That account cannot be changed here.')
            else:
                account.is_active = not account.is_active
                account.save(update_fields=['is_active', 'updated_at'])
                audit_action(
                    request,
                    'user.activated' if account.is_active else 'user.deactivated',
                    account,
                )
                messages.success(request, f'{account.username} is now {"active" if account.is_active else "inactive"}.')
                return redirect(detail_url)

        elif action == 'reset_password':
            if not can_edit or account.role == User.ROLE_SUPERADMIN:
                messages.error(request, 'That account is outside your password-reset scope.')
            else:
                password_form = AdminPasswordResetForm(request.POST, user=account)
                if password_form.is_valid():
                    account.set_password(password_form.cleaned_data['password'])
                    with transaction.atomic():
                        account.save(update_fields=['password', 'updated_at'])
                        revoke_user_refresh_tokens(account)
                    audit_action(request, 'user.password_reset', account)
                    messages.success(request, f'A new password was set for {account.username}.')
                    return redirect(detail_url)
                messages.error(request, 'Password was not changed. Use a stronger password and try again.')

        elif action in ('toggle_multi_merchant_access', 'toggle_settlement_transactions') and is_user:
            if not can_edit:
                messages.error(request, 'That account is outside your management scope.')
            else:
                multi = action == 'toggle_multi_merchant_access'
                field = 'allow_multiple_merchants' if multi else 'allow_settlement_transactions'
                setattr(account, field, not getattr(account, field))
                account.save(update_fields=[field, 'updated_at'])
                audit_action(
                    request,
                    f'user.{"multiple_merchant_access" if multi else "settlement_transactions"}_'
                    f'{"enabled" if getattr(account, field) else "disabled"}',
                    account,
                )
                label = 'Multiple-merchant API access' if multi else 'Settlement transactions API access'
                state = 'enabled' if getattr(account, field) else 'disabled'
                messages.success(request, f'{label} is {state} for {account.username}.')
                return redirect(detail_url)

        elif action == 'assign_merchant':
            bulk_form = MerchantBulkAssignmentForm(
                request.POST,
                users=all_users,
                user_queryset=assignable_users,
                merchants=merchant_queryset,
                fixed_user=account,
            )
            if not can_edit or not is_user:
                messages.error(request, 'That account is outside your merchant assignment scope.')
            elif bulk_form.is_valid():
                created = assign_merchants(request, account, bulk_form.cleaned_data['merchants'])
                if created:
                    names = ', '.join(merchant.merchant_name for merchant in created)
                    messages.success(request, f'Merchant access granted: {names}.')
                else:
                    messages.info(request, 'Those merchants are already assigned to this account.')
                return redirect(detail_url)

        elif action == 'unassign_merchant':
            assignment = UserMerchant.objects.select_related('merchant').filter(
                pk=request.POST.get('assignment_id'),
                user=account,
            ).first()
            if not can_edit or not is_user or assignment is None:
                messages.error(request, 'That assignment is outside your management scope.')
            else:
                audit_action(request, 'merchant.unassigned', assignment, {
                    'user_id': account.pk,
                    'merchant_uid': assignment.merchant.merchant_uid,
                })
                assignment.delete()
                messages.success(request, f'{assignment.merchant.merchant_name} was removed from {account.username}.')
                return redirect(detail_url)

        elif action in ('assign_admin_user', 'unassign_admin_user') and is_superadmin and is_user:
            admin_form = AdminUserAssignmentForm(
                request.POST,
                user_queryset=assignable_users,
                fixed_user=account,
            )
            if admin_form.is_valid():
                admin = admin_form.cleaned_data['admin']
                user = admin_form.cleaned_data['user']
                if action == 'assign_admin_user':
                    assignment, created = AdminUserAssignment.objects.get_or_create(
                        admin=admin,
                        user=user,
                        defaults={'assigned_by': request.user},
                    )
                    if created:
                        audit_action(request, 'admin_user.assigned', assignment, {
                            'admin_id': admin.pk,
                            'user_id': user.pk,
                        })
                        messages.success(request, f'{user.username} was assigned to {admin.username}.')
                    else:
                        messages.info(request, 'That user is already assigned to this admin.')
                else:
                    assignment = AdminUserAssignment.objects.filter(admin=admin, user=user).first()
                    if assignment:
                        audit_action(request, 'admin_user.unassigned', assignment)
                        assignment.delete()
                        messages.success(request, f'{user.username} was removed from {admin.username}.')
                    else:
                        messages.info(request, 'That admin-user assignment does not exist.')
                return redirect(detail_url)

        else:
            messages.error(request, 'Unknown account action.')

    assigned_merchants = list(
        UserMerchant.objects.filter(user=account)
        .select_related('merchant')
        .order_by('merchant__merchant_name')
    )
    admin_assignments = (
        list(
            AdminUserAssignment.objects.filter(user=account)
            .select_related('admin')
            .order_by('admin__username')
        )
        if is_superadmin and is_user else []
    )
    return render(request, 'portal/account_detail.html', {
        'account': account,
        'is_superadmin': is_superadmin,
        'is_user': is_user,
        'can_edit': can_edit,
        'edit_form': edit_form,
        'merchant_form': bulk_form,
        'admin_form': admin_form,
        'assigned_merchants': assigned_merchants,
        'admin_assignments': admin_assignments,
        'merchant_options_url': reverse('portal-merchant-options'),
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
                with transaction.atomic():
                    user = password_form.save()
                    revoke_user_refresh_tokens(user)
                update_session_auth_hash(request, user)
                messages.success(request, 'Your password was changed.')
                return redirect('portal-profile-page')
    return render(request, 'portal/profile.html', {
        'profile_form': profile_form,
        'password_form': password_form,
    })
