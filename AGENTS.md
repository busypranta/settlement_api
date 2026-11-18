# Settlement Portal codebase notes

## Stack
- Django 5 templates + vanilla inline JavaScript; no HTMX/jQuery/React/Bootstrap.
- DRF + SimpleJWT for APIs; portal web pages use Django session auth + CSRF.
- Single stylesheet: `static/portal/portal.css`; shared JS/dialogs live in `templates/portal/base.html`.

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
- Searchable select: `data-searchable-select` JS in `base.html:138-256`; CSS `.searchable-select*`.

## Merchant dropdown / lazy options
- Grant-access merchant field uses `MerchantAssignmentForm(..., lazy_merchant=True)` in the merchant page.
- `lazy_merchant=True` renders the merchant field as a hidden input; the visible combobox is in
  `templates/portal/merchants.html` with `data-lazy-merchant-select`.
- JSON endpoint: `/portal/merchants/options/` -> name `portal-merchant-options`, view `merchant_options`.
  Supports `q`, `offset`, `id`, `user_id`; returns 10 active scoped merchants at a time and flags
  merchants already assigned to the selected user as `assigned: true`.

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
