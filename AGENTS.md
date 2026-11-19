# Settlement Portal codebase notes

## Stack
- Django 5 templates + vanilla inline JavaScript; no HTMX/jQuery/React/Bootstrap.
- DRF + SimpleJWT for APIs; portal web pages use Django session auth + CSRF.
- Single stylesheet: `static/portal/portal.css`; shared JS/dialogs live in `templates/portal/base.html`.
- No inline `<style>` blocks or inline `style=` attributes in portal templates; new styles go in `portal.css`.

## Portal URL/view/template map
All paths are under the `/portal/` include in `config/urls.py`; names are from `apps/accounts/urls_web.py`.

| Path | Name | View | Template / response |
| --- | --- | --- | --- |
| `/portal/login/` | `portal-login` | `django.contrib.auth.views.LoginView` | `templates/portal/login.html` |
| `/portal/logout/` | `portal-logout` | `django.contrib.auth.views.LogoutView` | redirect |
| `/portal/` | `portal-dashboard` | `web_views.dashboard` | `templates/portal/dashboard.html` |
| `/portal/logs/` | `portal-api-logs-page` | `web_views.api_logs` | `templates/portal/logs.html` |
| `/portal/playground/` | `portal-api-playground` | `web_views.api_playground` | `templates/portal/playground.html` |
| `/portal/playground/merchants/options/` | `portal-playground-merchant-options` | `web_views.playground_merchant_options` | JSON merchant options (lazy playground picker) |
| `/portal/merchants/options/` | `portal-merchant-options` | `web_views.merchant_options` | JSON merchant options |
| `/portal/merchants/sync/` | `portal-merchant-sync` | `web_views.merchant_sync` | `templates/portal/merchant_sync.html` |
| `/portal/merchants/` | `portal-merchant-access` | `web_views.merchant_access` | `templates/portal/merchants.html` |
| `/portal/accounts/` | `portal-managed-accounts` | `web_views.managed_accounts` | `templates/portal/users.html` |
| `/portal/accounts/create/` | `portal-create-account` | `web_views.create_account` | `templates/portal/create_account.html` |
| `/portal/accounts/<int:user_id>/` | `portal-account-detail` | `web_views.account_detail` | `templates/portal/account_detail.html` |
| `/portal/profile/` | `portal-profile-page` | `web_views.profile` | `templates/portal/profile.html` |
| `/portal/robots.txt` | — | `urls_web.robots_txt` | plain text |

## Merchant portal page
- URL: `/portal/merchants/` -> `apps/accounts/urls_web.py` name `portal-merchant-access`.
- View: `apps/accounts/web_views.py::merchant_access`.
- Template: `templates/portal/merchants.html` extends `templates/portal/base.html`.
- POST actions handled on the same view: `create_merchant`, `assign_merchant`, `toggle_merchant`, `edit_merchant`.
- Scoping helpers live in `apps/accounts/web_views.py::scoped_merchants` / `scoped_users` and
  `apps/settlement_api/permissions.py::can_manage_merchant`, `can_manage_user`, `manageable_merchant_ids`.

## Reusable patterns
- Pagination: `Paginator(queryset, 25).get_page(request.GET.get('page'))`; template nav pattern in
  `templates/portal/users.html` (`.pagination-controls`) and `templates/portal/logs.html`.
- Server-side search: GET form with `name="q"`, filter with `Q(...icontains=query)`, preserve `q` in page links.
- Modals: native `<dialog>`; form-modal example is the reset-password dialog in `templates/portal/users.html`
  with open/close JS in `templates/portal/base.html`. Confirmation dialog is shared in `base.html`.
- Searchable select: `data-searchable-select` JS in `templates/portal/base.html`; CSS `.searchable-select*`.
- Merchant picker: `templates/portal/includes/merchant_picker.html` is the single component.
  Remote mode uses `data-options-url` (admin assignment and the lazy playground picker); local
  mode passes `local_merchants`. Pass `show_bulk_actions=True` for Select All/Clear and
  `optional_selection=True` when the picker is not a required form field. Pass
  `single_select=True` for a one-merchant combobox and `value_field="uid"` when the caller needs
  merchant UIDs instead of primary keys. Shared JS lives in `base.html` under
  `[data-merchant-picker]` and CSS under `/* Shared multi-merchant picker */` in `portal.css`.

## CSS maintenance
- Keep all portal styles in `static/portal/portal.css`; do not add `<style>` blocks or `style=` attributes.
- Prefer the `:root` design tokens (documented at the top of `portal.css`) over repeating exact hex values.
  Add a token only when the same value is reused; do not substitute a near-match.
- Cleanups must be cascade-preserving: remove selectors/declarations that are unused or already
  overridden in the same context, and do not reintroduce styles removed in commit `73f932e clean up`.

## Merchant dropdown / lazy options
- Grant-access merchant field uses `MerchantAssignmentForm(..., lazy_merchant=True)` in the merchant page.
- `lazy_merchant=True` renders the merchant field as a hidden input; the visible combobox is in
  `templates/portal/merchants.html` with `data-lazy-merchant-select`.
- JSON endpoint: `/portal/merchants/options/` -> name `portal-merchant-options`, view `merchant_options`.
  Supports `q`, `offset`, `id`, `user_id`; returns 10 active scoped merchants at a time and flags
  merchants already assigned to the selected user as `assigned: true`.
- Playground lazy endpoint: `/portal/playground/merchants/options/` -> name
  `portal-playground-merchant-options`, view `playground_merchant_options`. Supports `q` and
  `offset`, returns 10 active merchants accessible to the signed-in user, and feeds both the
  required single-merchant picker and the optional multi-merchant picker with UID values.

## Merchant model notes
- `apps/merchants/models.py::PortalMerchant` has unique `merchant_uid` and normalized `merchant_name_key`.
- `save()` strips UID and normalizes name/name_key, so editing must preserve normalization and uniqueness.
- `merchant_uid` is used in API URLs; changing it has downstream effects.

## Tests
- Main test module: `apps/accounts/tests.py`.
- Run with: `python manage.py test apps.accounts --settings=config.test_settings`.
- Baseline caveats in this checkout: tests render templates with `CompressedManifestStaticFilesStorage`
  (needs `collectstatic`) and some source unmanaged models reference `JPAY_RECON.*`, which SQLite test
  runs cannot resolve. Use a local settings override for isolated verification if needed.
