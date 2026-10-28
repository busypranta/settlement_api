import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from apps.api_logs.models import ApiLog
from apps.accounts.models import User
from apps.merchants.models import AdminUserAssignment, PortalMerchant, UserMerchant
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase
from rest_framework.test import APIClient
from rest_framework.response import Response as DRFResponse

from core.middleware import APILogMiddleware
from apps.settlement_api.serializers import SettlementReportDateRangeSerializer
from apps.settlement_api.services import (
    approved_settlement_batch_ids_for_merchant,
    compute_settlement_for_merchants,
)
from apps.settlement_api.permissions import (
    manageable_merchant_ids,
    merchant_access_ids,
)
from django.utils import timezone


class PortalAuthenticationTests(TestCase):
    def setUp(self):
        self.password = 'SettlementUser!9284'
        self.user = User.objects.create_user(
            username='merchant-user',
            password=self.password,
            role=User.ROLE_USER,
        )

    def test_user_can_sign_in_to_portal_and_api_with_same_credentials(self):
        response = self.client.post(
            reverse('portal-login'),
            {'username': self.user.username, 'password': self.password},
        )
        self.assertRedirects(response, reverse('portal-dashboard'))
        token_response = self.client.post(
            '/api/v1/auth/token/',
            {'username': self.user.username, 'password': self.password},
            content_type='application/json',
        )
        self.assertEqual(token_response.status_code, 200)
        self.assertIn('access', token_response.json())

    def test_refresh_tokens_rotate_and_previous_token_is_blacklisted(self):
        response = self.client.post('/api/v1/auth/token/', {
            'username': self.user.username,
            'password': self.password,
        }, content_type='application/json')
        original_refresh = response.json()['refresh']

        rotated_response = self.client.post(
            '/api/v1/auth/token/refresh/',
            {'refresh': original_refresh},
            content_type='application/json',
        )
        self.assertEqual(rotated_response.status_code, 200)
        rotated_refresh = rotated_response.json()['refresh']
        self.assertNotEqual(rotated_refresh, original_refresh)

        reused_response = self.client.post(
            '/api/v1/auth/token/refresh/',
            {'refresh': original_refresh},
            content_type='application/json',
        )
        self.assertEqual(reused_response.status_code, 401)

    def test_password_change_revokes_existing_refresh_tokens(self):
        token_response = self.client.post('/api/v1/auth/token/', {
            'username': self.user.username,
            'password': self.password,
        }, content_type='application/json')
        refresh_token = token_response.json()['refresh']

        api_client = APIClient()
        api_client.force_authenticate(self.user)
        changed = api_client.post(reverse('portal-password-change-api'), {
            'current_password': self.password,
            'new_password': 'NewSecure!9284',
        }, format='json')
        self.assertEqual(changed.status_code, 200)

        refreshed = self.client.post(
            '/api/v1/auth/token/refresh/',
            {'refresh': refresh_token},
            content_type='application/json',
        )
        self.assertEqual(refreshed.status_code, 401)

    def test_portal_password_change_revokes_existing_refresh_tokens(self):
        token_response = self.client.post('/api/v1/auth/token/', {
            'username': self.user.username,
            'password': self.password,
        }, content_type='application/json')
        refresh_token = token_response.json()['refresh']

        self.client.force_login(self.user)
        changed = self.client.post(reverse('portal-profile-page'), {
            'action': 'password',
            'old_password': self.password,
            'new_password1': 'NewSecure!9284',
            'new_password2': 'NewSecure!9284',
        })
        self.assertRedirects(changed, reverse('portal-profile-page'))

        refreshed = self.client.post(
            '/api/v1/auth/token/refresh/',
            {'refresh': refresh_token},
            content_type='application/json',
        )
        self.assertEqual(refreshed.status_code, 401)

    def test_refresh_token_can_be_revoked_through_logout_endpoint(self):
        token_response = self.client.post('/api/v1/auth/token/', {
            'username': self.user.username,
            'password': self.password,
        }, content_type='application/json')
        refresh_token = token_response.json()['refresh']

        logout_response = self.client.post(
            '/api/v1/auth/token/logout/',
            {'refresh': refresh_token},
            content_type='application/json',
        )
        self.assertEqual(logout_response.status_code, 200)

        refreshed = self.client.post(
            '/api/v1/auth/token/refresh/',
            {'refresh': refresh_token},
            content_type='application/json',
        )
        self.assertEqual(refreshed.status_code, 401)

    def test_inactive_account_cannot_sign_in(self):
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        response = self.client.post(
            reverse('portal-login'),
            {'username': self.user.username, 'password': self.password},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "We couldn't sign you in")

    def test_signed_in_user_can_open_api_documentation(self):
        self.client.force_login(self.user)
        response = self.client.get('/api/docs/')
        self.assertEqual(response.status_code, 200)

    def test_business_user_schema_hides_portal_management_apis(self):
        self.client.force_login(self.user)
        response = self.client.get('/api/schema/')
        self.assertEqual(response.status_code, 200)
        paths = response.data['paths']
        self.assertTrue(any(path.startswith('/api/v1/settlement-') for path in paths))
        self.assertIn('/api/v1/auth/token/', paths)
        self.assertIn('/api/v1/auth/token/logout/', paths)
        transaction_path = paths['/api/v1/settlement-transactions/{merchant_uid}']['post']
        self.assertIn('enables settlement transactions API access', transaction_path['description'])
        self.assertFalse(any(path.startswith('/api/portal/') for path in paths))

    def test_superadmin_schema_includes_portal_management_apis(self):
        superadmin = User.objects.create_superuser(
            username='schema-superadmin',
            email='schema-admin@example.test',
            password='SchemaAdmin!9284',
        )
        self.client.force_login(superadmin)
        response = self.client.get('/api/schema/')
        self.assertEqual(response.status_code, 200)
        paths = response.data['paths']
        self.assertIn('/api/portal/users/', paths)
        self.assertIn('/api/portal/merchants/', paths)

    def test_admin_schema_does_not_include_superadmin_portal_apis(self):
        admin = User.objects.create_user(
            username='docs-admin',
            password='DocsAdmin!9284',
            role=User.ROLE_ADMIN,
        )
        self.client.force_login(admin)
        response = self.client.get('/api/schema/')
        self.assertEqual(response.status_code, 200)
        paths = response.data['paths']
        self.assertTrue(any(path.startswith('/api/v1/settlement-') for path in paths))
        self.assertFalse(any(path.startswith('/api/portal/') for path in paths))

    def test_login_uses_settlement_portal_branding(self):
        response = self.client.get(reverse('portal-login'))
        self.assertContains(response, 'Settlement Portal')
        self.assertNotContains(response, 'SettleDesk')

    def test_profile_updates_name_mobile_and_email(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('portal-profile-page'), {
            'action': 'profile',
            'name': 'Taylor Morgan',
            'mobile': '01712-345678',
            'email': 'taylor@example.test',
        })
        self.assertRedirects(response, reverse('portal-profile-page'))
        self.user.refresh_from_db()
        self.assertEqual(self.user.name, 'Taylor Morgan')
        self.assertEqual(self.user.mobile, '+8801712345678')
        self.assertEqual(self.user.email, 'taylor@example.test')

    def test_profile_rejects_invalid_email_and_non_bangladesh_mobile(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('portal-profile-page'), {
            'action': 'profile',
            'name': 'Taylor Morgan',
            'mobile': '+1 555 010 2468',
            'email': 'invalid-email',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context['profile_form'], 'mobile', 'Enter a valid Bangladesh mobile number, such as 01XXXXXXXXX or +8801XXXXXXXXX.')
        self.assertFormError(response.context['profile_form'], 'email', 'Enter a valid email address.')

    def test_password_validator_enforces_eight_to_sixteen_characters(self):
        with self.assertRaises(ValidationError):
            validate_password('Ab1!')
        validate_password('Abcdef1!')
        with self.assertRaises(ValidationError):
            validate_password('Abcdef1!' + 'longer-than-sixteen')

    def test_profile_api_validates_mobile_email_and_name(self):
        api_client = APIClient()
        api_client.force_authenticate(self.user)
        response = api_client.patch(
            reverse('portal-profile-api'),
            {'mobile': '01234', 'email': 'not-an-email', 'name': 'x'},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response['Content-Type'], 'application/json')
        errors = response.json()['errors']
        self.assertIn('mobile', errors)
        self.assertIn('email', errors)
        self.assertIn('name', errors)

        response = api_client.patch(
            reverse('portal-profile-api'),
            {'name': 'N' * 101},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response['Content-Type'], 'application/json')
        self.assertIn('name', response.json()['errors'])

    def test_admin_api_account_creation_requires_valid_contact_details(self):
        admin = User.objects.create_user(
            username='api-account-admin',
            password='AdminPassword!9284',
            role=User.ROLE_ADMIN,
        )
        api_client = APIClient()
        api_client.force_authenticate(admin)
        response = api_client.post(reverse('portal-users-api'), {
            'username': 'api-created-user',
            'password': 'NewUser!9284',
            'name': 'Jordan Lee',
            'mobile': '01712 345678',
            'email': 'jordan@example.test',
        }, format='json')
        self.assertEqual(response.status_code, 201)
        account = User.objects.get(username='api-created-user')
        self.assertEqual(account.mobile, '+8801712345678')
        self.assertEqual(account.email, 'jordan@example.test')

    def test_profile_shows_password_strength_calculator(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('portal-profile-page'))
        self.assertContains(response, 'data-password-strength="true"')
        self.assertContains(response, 'data-strength-for="id_new_password1"')

    def test_settlement_report_date_range_is_limited_to_60_calendar_days(self):
        allowed = SettlementReportDateRangeSerializer(data={
            'from_date': '01-01-2026',
            'to_date': '01-03-2026',
        })
        self.assertTrue(allowed.is_valid(), allowed.errors)
        rejected = SettlementReportDateRangeSerializer(data={
            'from_date': '01-01-2026',
            'to_date': '02-03-2026',
        })
        self.assertFalse(rejected.is_valid())
        self.assertIn('The date range cannot exceed 60 calendar days.', str(rejected.errors))

    def test_transaction_date_range_remains_unlimited(self):
        from apps.settlement_api.serializers import DateRangeSerializer

        serializer = DateRangeSerializer(data={
            'from_date': '01-01-2025',
            'to_date': '31-12-2026',
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)


class SettlementLineStatusTests(SimpleTestCase):
    @patch('apps.settlement_api.services.SourceContentType.objects')
    @patch('apps.settlement_api.services.SettlementLine.objects')
    @patch('apps.settlement_api.services.Merchant.objects')
    def test_multi_merchant_settlement_report_loads_source_rows_in_batches(
        self,
        merchant_objects,
        line_objects,
        content_type_objects,
    ):
        content_type_objects.filter.return_value.values_list.return_value.first.return_value = 4
        line = SimpleNamespace(
            cr_account=SimpleNamespace(owner_id=31),
            amount=Decimal('12.50'),
            status='APPROVED',
            approved_at=None,
            batch=SimpleNamespace(
                date_from=date(2026, 1, 1),
                date_to=date(2026, 1, 31),
            ),
        )
        line_objects.filter.return_value.only.return_value.select_related.return_value.order_by.return_value = [line]
        merchant_objects.filter.return_value.values_list.return_value = [(31, 'North Star')]
        portal_merchants = [
            SimpleNamespace(merchant_uid='north', merchant_name='North Star'),
            SimpleNamespace(merchant_uid='south', merchant_name='South Star'),
        ]

        results = compute_settlement_for_merchants(
            portal_merchants,
            date(2026, 1, 1),
            date(2026, 1, 31),
        )

        self.assertEqual(len(results['north']), 1)
        self.assertEqual(results['north'][0]['total_amount'], '12.50')
        self.assertEqual(results['south'], [])
        self.assertEqual(content_type_objects.filter.call_count, 1)
        self.assertEqual(line_objects.filter.call_count, 1)
        self.assertEqual(merchant_objects.filter.call_count, 1)
        selected_fields = line_objects.filter.return_value.only.call_args.args
        self.assertIn('cr_account__owner_id', selected_fields)
        self.assertIn('cr_account__owner_type_id', selected_fields)

    @patch('apps.settlement_api.services.SourceContentType.objects')
    @patch('apps.settlement_api.services.SettlementLine.objects')
    @patch('apps.settlement_api.services.Merchant.objects')
    def test_only_approved_lines_for_the_target_merchant_mark_a_batch_successful(
        self,
        merchant_objects,
        line_objects,
        content_type_objects,
    ):
        content_type_objects.filter.return_value.values_list.return_value.first.return_value = 4
        line_objects.filter.return_value.values_list.return_value = [
            (10, 'APPROVED', 101),
            (11, 'DRAFT', 101),
            (12, 'APPROVED', 202),
        ]
        merchant_objects.filter.return_value = [
            type('SourceMerchant', (), {'pk': 101, 'name': 'Visible Merchant'})(),
            type('SourceMerchant', (), {'pk': 202, 'name': 'Other Merchant'})(),
        ]

        approved = approved_settlement_batch_ids_for_merchant(
            type('PortalMerchant', (), {'merchant_name': 'Visible Merchant'})(),
            {10, 11, 12},
        )

        self.assertEqual(approved, {10})


class ApiJsonResponseMiddlewareTests(TestCase):
    def test_api_debug_html_errors_are_returned_as_json(self):
        for path in ('/api/v1/settlement-report/merchant', '/api/v1/auth/token/'):
            with self.subTest(path=path):
                request = RequestFactory().post(path)
                middleware = APILogMiddleware(
                    lambda request: HttpResponse(
                        '<html><body>Debug traceback and database credentials</body></html>',
                        status=500,
                        content_type='text/html',
                    ),
                )
                with patch('core.middleware.ApiLog.objects.create'):
                    response = middleware(request)
                self.assertEqual(response.status_code, 500)
                self.assertEqual(response['Content-Type'], 'application/json')
                self.assertNotIn(b'Debug traceback', response.content)
                self.assertEqual(json.loads(response.content)['request_id'], response['X-Request-ID'])

    def test_api_json_responses_are_not_rewritten(self):
        request = RequestFactory().post('/api/v1/settlement-report/merchant')
        api_response = DRFResponse({'success': True, 'data': []})
        api_response.accepted_renderer = type('JSONRenderer', (), {'media_type': 'application/json'})()
        middleware = APILogMiddleware(lambda request: api_response)
        with patch('core.middleware.ApiLog.objects.create'):
            response = middleware(request)
        self.assertEqual(response.status_code, 200)
        self.assertIs(response, api_response)
        self.assertEqual(response.data, {'success': True, 'data': []})


class PortalVisibilityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='visible-user',
            password='UserPassword!9284',
            role=User.ROLE_USER,
        )
        self.other_user = User.objects.create_user(
            username='other-user',
            password='UserPassword!9284',
            role=User.ROLE_USER,
        )
        self.merchant = PortalMerchant.objects.create(
            merchant_uid='merchant-visible',
            merchant_name='Visible Merchant',
        )
        self.other_merchant = PortalMerchant.objects.create(
            merchant_uid='merchant-hidden',
            merchant_name='Hidden Merchant',
        )
        UserMerchant.objects.create(user=self.user, merchant=self.merchant)
        ApiLog.objects.create(
            user=self.user,
            request_id='visible-request-id',
            endpoint='/api/v1/settlement-report',
            http_method='POST',
            http_status=200,
        )
        ApiLog.objects.create(
            user=self.other_user,
            request_id='hidden-request-id',
            endpoint='/api/v1/private',
            http_method='POST',
            http_status=200,
        )

    def test_user_sees_only_assigned_merchants(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('portal-merchant-access'))
        self.assertContains(response, self.merchant.merchant_uid)
        self.assertNotContains(response, self.other_merchant.merchant_uid)

    def test_user_sees_only_their_api_history(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('portal-api-logs-page'))
        self.assertContains(response, 'visible-request-id')
        self.assertNotContains(response, 'hidden-request-id')

    def test_playground_lists_only_merchants_the_user_can_access(self):
        self.client.force_login(self.user)
        playground = self.client.get(reverse('portal-api-playground'))
        self.assertContains(playground, 'Test an API call')
        self.assertContains(playground, self.merchant.merchant_uid)
        self.assertNotContains(playground, self.other_merchant.merchant_uid)
        self.assertContains(playground, '/api/v1/auth/token/')
        self.assertNotContains(playground, '<option value="transactions">Transactions</option>')
        self.assertContains(playground, 'merchant-picker-search')
        self.assertContains(playground, 'merchant-picker-option')
        self.assertContains(playground, 'merchant-picker-selection')
        self.assertContains(playground, 'merchant-visible-count')
        self.assertNotContains(playground, 'merchant-picker-trigger')
        self.assertNotContains(playground, '<select id="playground-merchants"')

    def test_playground_shows_transactions_only_when_enabled_for_user(self):
        self.client.force_login(self.user)
        disabled = self.client.get(reverse('portal-api-playground'))
        self.assertNotContains(disabled, '<option value="transactions">Transactions</option>')

        self.user.allow_settlement_transactions = True
        self.user.save(update_fields=['allow_settlement_transactions'])
        enabled = self.client.get(reverse('portal-api-playground'))
        self.assertContains(enabled, '<option value="transactions">Transactions</option>')

    def test_playground_shows_multi_merchant_mode_only_when_enabled(self):
        UserMerchant.objects.create(user=self.user, merchant=self.other_merchant)
        self.client.force_login(self.user)
        disabled = self.client.get(reverse('portal-api-playground'))
        self.assertNotContains(disabled, 'Settlement report · multiple merchants')

        self.user.allow_multiple_merchants = True
        self.user.save(update_fields=['allow_multiple_merchants'])
        enabled = self.client.get(reverse('portal-api-playground'))
        self.assertContains(enabled, 'Settlement Report · Multiple')

    def test_user_without_multi_merchant_permission_is_denied_multi_report_api(self):
        api_client = APIClient()
        api_client.force_authenticate(self.user)
        response = api_client.post('/api/v1/settlement-report', {}, format='json')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json()['error'],
            'Multiple-merchant API access is not enabled for this account',
        )

    def test_settlement_report_rejects_ranges_longer_than_60_days(self):
        api_client = APIClient()
        api_client.force_authenticate(self.user)
        response = api_client.post(
            '/api/v1/settlement-report/merchant-visible',
            {'from_date': '01-01-2026', 'to_date': '02-03-2026'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)

    @patch('apps.settlement_api.views.approved_settlement_batch_ids_for_merchant')
    @patch('apps.settlement_api.views.OwnTransaction.objects.filter')
    def test_transaction_status_uses_approved_merchant_line_in_its_batch(
        self,
        transaction_filter,
        approved_batch_lookup,
    ):
        transaction = SimpleNamespace(
            txn_datetime=timezone.now(),
            merchant_amount=Decimal('6500.00'),
            settlement_batch_id=55,
        )
        queryset = transaction_filter.return_value.order_by.return_value
        queryset.count.return_value = 1
        queryset.__getitem__.return_value = [transaction]
        approved_batch_lookup.side_effect = [{55}, set()]

        api_client = APIClient()
        api_client.force_authenticate(self.user)
        self.user.allow_settlement_transactions = True
        self.user.save(update_fields=['allow_settlement_transactions'])
        request_data = {'from_date': '01-01-2026', 'to_date': '01-03-2026'}
        success = api_client.post(
            '/api/v1/settlement-transactions/merchant-visible',
            request_data,
            format='json',
        )
        pending = api_client.post(
            '/api/v1/settlement-transactions/merchant-visible',
            request_data,
            format='json',
        )

        self.assertEqual(success.json()['data'][0]['status'], 'SUCCESS')
        self.assertEqual(pending.json()['data'][0]['status'], 'PENDING')

    def test_admin_can_enable_multi_merchant_access_for_scoped_user(self):
        admin = User.objects.create_user(
            username='multi-access-admin',
            password='AdminPassword!9284',
            role=User.ROLE_ADMIN,
        )
        managed_user = User.objects.create_user(
            username='multi-access-user',
            password='UserPassword!9284',
            role=User.ROLE_USER,
            created_by=admin,
        )
        self.client.force_login(admin)
        response = self.client.post(reverse('portal-managed-accounts'), {
            'action': 'toggle_multi_merchant_access',
            'user_id': managed_user.pk,
        })
        self.assertRedirects(response, reverse('portal-managed-accounts'))
        managed_user.refresh_from_db()
        self.assertTrue(managed_user.allow_multiple_merchants)
        response = self.client.post(reverse('portal-managed-accounts'), {
            'action': 'toggle_settlement_transactions',
            'user_id': managed_user.pk,
        })
        self.assertRedirects(response, reverse('portal-managed-accounts'))
        managed_user.refresh_from_db()
        self.assertTrue(managed_user.allow_settlement_transactions)

    def test_manageable_merchant_access_query_count_does_not_scale_with_merchant_count(self):
        admin = User.objects.create_user(
            username='merchant-scope-admin',
            password='AdminPassword!9284',
            role=User.ROLE_ADMIN,
        )
        managed_user = User.objects.create_user(
            username='merchant-scope-user',
            password='UserPassword!9284',
            role=User.ROLE_USER,
            created_by=admin,
        )
        for index in range(3):
            merchant = PortalMerchant.objects.create(
                merchant_uid=f'merchant-scope-{index}',
                merchant_name=f'Merchant Scope {index}',
                created_by=admin,
            )
            UserMerchant.objects.create(user=managed_user, merchant=merchant)

        merchants = list(PortalMerchant.objects.filter(merchant_uid__startswith='merchant-scope-'))
        with self.assertNumQueries(2):
            manageable_ids = manageable_merchant_ids(admin, merchants)

        self.assertEqual(manageable_ids, {merchant.pk for merchant in merchants})

    def test_user_merchant_access_is_checked_with_one_query_for_multiple_merchants(self):
        merchants = [self.merchant, self.other_merchant]
        UserMerchant.objects.create(user=self.user, merchant=self.other_merchant)

        with self.assertNumQueries(1):
            allowed_ids = merchant_access_ids(self.user, merchants)

        self.assertEqual(allowed_ids, {self.merchant.pk, self.other_merchant.pk})

    def test_user_without_transaction_api_access_is_denied(self):
        api_client = APIClient()
        api_client.force_authenticate(self.user)
        response = api_client.post(
            '/api/v1/settlement-transactions/merchant-visible',
            {'from_date': '01-01-2026', 'to_date': '02-01-2026'},
            format='json',
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json()['error'],
            'Settlement transactions API access is not enabled for this account',
        )

    def test_authenticated_workspace_pages_render(self):
        self.client.force_login(self.user)
        for route_name in (
            'portal-dashboard',
            'portal-api-logs-page',
            'portal-api-playground',
            'portal-merchant-access',
            'portal-profile-page',
        ):
            with self.subTest(route=route_name):
                self.assertEqual(self.client.get(reverse(route_name)).status_code, 200)

    def test_admin_sees_only_users_in_their_management_scope(self):
        admin = User.objects.create_user(
            username='scoped-admin',
            password='AdminPassword!9284',
            role=User.ROLE_ADMIN,
        )
        managed_user = User.objects.create_user(
            username='managed-user',
            password='UserPassword!9284',
            role=User.ROLE_USER,
            created_by=admin,
        )
        self.client.force_login(admin)
        response = self.client.get(reverse('portal-managed-accounts'))
        self.assertContains(response, managed_user.username)
        self.assertNotContains(response, self.user.username)

    def test_admin_activity_and_merchants_are_scoped_to_managed_users(self):
        admin = User.objects.create_user(
            username='activity-admin',
            password='AdminPassword!9284',
            role=User.ROLE_ADMIN,
        )
        AdminUserAssignment.objects.create(admin=admin, user=self.user)
        self.client.force_login(admin)

        logs_response = self.client.get(reverse('portal-api-logs-page'))
        self.assertContains(logs_response, 'visible-request-id')
        self.assertNotContains(logs_response, 'hidden-request-id')

        merchant_response = self.client.get(reverse('portal-merchant-access'))
        self.assertContains(merchant_response, self.merchant.merchant_uid)
        self.assertNotContains(merchant_response, self.other_merchant.merchant_uid)

    def test_admin_can_create_user_and_assign_merchant_in_the_portal(self):
        admin = User.objects.create_user(
            username='portal-admin',
            password='AdminPassword!9284',
            role=User.ROLE_ADMIN,
        )
        self.merchant.created_by = admin
        self.merchant.save(update_fields=['created_by'])
        self.client.force_login(admin)
        self.assertEqual(self.client.get(reverse('portal-create-account')).status_code, 200)
        response = self.client.post(reverse('portal-create-account'), {
            'username': 'new-merchant-user',
            'password': 'NewUser!9284',
            'name': 'Jordan Lee',
            'mobile': '01812 345678',
            'email': 'jordan@example.test',
            'role': User.ROLE_USER,
        })
        self.assertRedirects(response, reverse('portal-managed-accounts'))
        account = User.objects.get(username='new-merchant-user')
        self.assertEqual(account.created_by, admin)
        self.assertEqual(account.name, 'Jordan Lee')
        self.assertEqual(account.mobile, '+8801812345678')
        self.assertEqual(account.email, 'jordan@example.test')

        response = self.client.post(reverse('portal-managed-accounts'), {
            'action': 'assign_merchant',
            'user': account.pk,
            'merchant': self.merchant.pk,
        })
        self.assertRedirects(response, reverse('portal-managed-accounts'))
        self.assertTrue(UserMerchant.objects.filter(user=account, merchant=self.merchant).exists())
        account_page = self.client.get(reverse('portal-managed-accounts'))
        self.assertContains(account_page, self.merchant.merchant_uid)
        self.assertContains(account_page, 'confirm-action-dialog')

        merchant_page = self.client.get(reverse('portal-merchant-access'))
        self.assertEqual(merchant_page.status_code, 200)
        self.assertEqual(merchant_page.content.count(b'data-searchable-select="true"'), 2)

    def test_admin_cannot_create_another_admin_or_manage_out_of_scope_user(self):
        admin = User.objects.create_user(
            username='restricted-admin',
            password='AdminPassword!9284',
            role=User.ROLE_ADMIN,
        )
        self.client.force_login(admin)
        response = self.client.post(reverse('portal-managed-accounts'), {
            'action': 'create_user',
            'username': 'unexpected-admin',
            'password': 'NewPortalUser!9284',
            'role': User.ROLE_ADMIN,
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='unexpected-admin').exists())

        response = self.client.post(reverse('portal-managed-accounts'), {
            'action': 'toggle_user',
            'user_id': self.user.pk,
        })
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)

    def test_superadmin_can_assign_a_user_to_an_admin(self):
        superadmin = User.objects.create_superuser(
            username='global-admin',
            email='root@example.test',
            password='GlobalAdmin!9284',
        )
        admin = User.objects.create_user(
            username='assigned-admin',
            password='AdminPassword!9284',
            role=User.ROLE_ADMIN,
        )
        self.client.force_login(superadmin)
        directory_response = self.client.get(reverse('portal-managed-accounts'))
        self.assertContains(directory_response, 'All portal accounts')
        self.assertContains(directory_response, 'Assign users to an admin')
        self.assertContains(directory_response, 'name="status"')
        self.assertContains(directory_response, 'ADMIN SCOPE DIRECTORY')
        self.assertContains(directory_response, 'account-role-filter')

        response = self.client.post(reverse('portal-managed-accounts'), {
            'action': 'assign_admin_user',
            'admin': admin.pk,
            'user': self.user.pk,
        })
        self.assertRedirects(response, reverse('portal-managed-accounts'))
        self.assertTrue(AdminUserAssignment.objects.filter(admin=admin, user=self.user).exists())

    def test_superadmin_can_create_admin_and_view_global_accounts_and_merchants(self):
        superadmin = User.objects.create_superuser(
            username='global-operator',
            email='global-operator@example.test',
            password='GlobalAdmin!9284',
        )
        self.client.force_login(superadmin)
        response = self.client.post(reverse('portal-create-account'), {
            'username': 'new-portal-admin',
            'password': 'Admin!9284',
            'name': 'Alex Admin',
            'mobile': '01912 345678',
            'email': 'alex.admin@example.test',
            'role': User.ROLE_ADMIN,
        })
        self.assertRedirects(response, reverse('portal-managed-accounts'))
        self.assertEqual(User.objects.get(username='new-portal-admin').role, User.ROLE_ADMIN)

        account_response = self.client.get(reverse('portal-managed-accounts'))
        self.assertContains(account_response, self.user.username)
        self.assertContains(account_response, self.other_user.username)
        merchant_response = self.client.get(reverse('portal-merchant-access'))
        self.assertContains(merchant_response, self.merchant.merchant_uid)
        self.assertContains(merchant_response, self.other_merchant.merchant_uid)

    def test_admin_can_add_merchant_and_assign_it_to_scoped_user(self):
        admin = User.objects.create_user(
            username='merchant-admin',
            password='AdminPassword!9284',
            role=User.ROLE_ADMIN,
        )
        managed_user = User.objects.create_user(
            username='owned-user',
            password='UserPassword!9284',
            role=User.ROLE_USER,
            created_by=admin,
        )
        self.client.force_login(admin)
        response = self.client.post(reverse('portal-merchant-access'), {
            'action': 'create_merchant',
            'merchant_uid': 'created-merchant',
            'merchant_name': 'Created Merchant LLC',
        })
        self.assertRedirects(response, reverse('portal-merchant-access'))
        merchant = PortalMerchant.objects.get(merchant_uid='created-merchant')
        self.assertEqual(merchant.created_by, admin)

        response = self.client.post(reverse('portal-merchant-access'), {
            'action': 'assign_merchant',
            'user': managed_user.pk,
            'merchant': merchant.pk,
        })
        self.assertRedirects(response, reverse('portal-merchant-access'))
        self.assertTrue(UserMerchant.objects.filter(user=managed_user, merchant=merchant).exists())

    def test_admin_cannot_deactivate_merchant_shared_with_out_of_scope_user(self):
        admin = User.objects.create_user(
            username='merchant-scope-admin',
            password='AdminPassword!9284',
            role=User.ROLE_ADMIN,
        )
        managed_user = User.objects.create_user(
            username='in-scope-merchant-user',
            password='UserPassword!9284',
            role=User.ROLE_USER,
            created_by=admin,
        )
        UserMerchant.objects.create(user=managed_user, merchant=self.merchant)
        UserMerchant.objects.create(user=self.other_user, merchant=self.merchant)
        self.client.force_login(admin)

        self.client.post(reverse('portal-merchant-access'), {
            'action': 'toggle_merchant',
            'merchant_id': self.merchant.pk,
        })
        self.merchant.refresh_from_db()
        self.assertTrue(self.merchant.is_active)

    def test_superadmin_can_remove_an_admin_assignment(self):
        superadmin = User.objects.create_superuser(
            username='assignment-owner',
            email='assignment-owner@example.test',
            password='GlobalAdmin!9284',
        )
        admin = User.objects.create_user(
            username='admin-to-unassign',
            password='AdminPassword!9284',
            role=User.ROLE_ADMIN,
        )
        AdminUserAssignment.objects.create(admin=admin, user=self.user)
        self.client.force_login(superadmin)

        response = self.client.post(reverse('portal-managed-accounts'), {
            'action': 'unassign_admin_user',
            'admin': admin.pk,
            'user': self.user.pk,
        })
        self.assertRedirects(response, reverse('portal-managed-accounts'))
        self.assertFalse(AdminUserAssignment.objects.filter(admin=admin, user=self.user).exists())
