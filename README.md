# Settlement Portal

Settlement Portal is a standalone Django portal for merchant settlement API users and administrators. A portal account uses the same username and password for browser sign-in and JWT token issuance; browser sessions do not replace the JWT required by business API calls.

## Portal features

- Username/password sign-in at `/portal/login/` for USER, ADMIN, and SUPERADMIN accounts.
- Role-scoped dashboard with recent API activity, accessible merchants, and managed-account summaries.
- API call history with response codes, request IDs, timing, merchant context, and status filters.
- Live API playground for settlement reports and transactions, restricted to merchants the signed-in account can access.
- Merchant directory showing assigned merchants and whether each is active for API use.
- Profile and password management.
- New passwords must be 8–16 characters. Account names are 2–100 characters; mobile numbers must be valid Bangladesh numbers and email addresses must be valid.
- ADMIN account views scoped to users created by or explicitly assigned to that admin.
- Admin web tools to create USER accounts, activate/deactivate scoped accounts, reset their passwords, add/deactivate merchants, and assign/unassign merchants to users in scope.
- SUPERADMIN global account and merchant visibility, ADMIN creation, and explicit ADMIN-to-USER assignments.
- Authenticated OpenAPI/Swagger documentation at `/api/docs/`. Business API users and admins see settlement endpoints; portal-management APIs appear only in the SUPERADMIN schema.

The browser portal uses Django's session authentication and CSRF protection. API endpoints continue to use JWT authentication. Access tokens are valid for two hours. The live playground requests a token with the user's portal credentials and keeps the token in page memory only.

## Local setup

1. Copy `.env.example` to `.env`, provide local Oracle connection details and a strong `SECRET_KEY`, and set `DEBUG=True` for local HTTP development.
2. Install dependencies: `pip install -r requirements.txt`.
3. Check settings: `python manage.py check`.
4. Apply portal-schema migrations: `python manage.py migrate --database default`.
5. Bootstrap an administrator: `python manage.py createsuperadmin --username <name>` and enter the password at the hidden prompt.
6. Start Django: `python manage.py runserver`.
7. Open `http://127.0.0.1:8000/portal/login/`. Sign in with the same username/password used to obtain a JWT at `/api/auth/token/`.

Set `DEBUG=False` and secure cookie settings in production. Terminate HTTPS at the approved deployment boundary. Never commit `.env` or use production credentials locally.

## Docker

The portal has its own compose project; Oracle is external:

```text
docker compose -f docker-compose.portal.yml up --build
```

The web container health check validates both configured database connections. Apply migrations against the portal schema only, then bootstrap the first SUPERADMIN through the management command. See [SCHEMA_GUIDE.md](./SCHEMA_GUIDE.md) for least-privilege Oracle setup.

## API

- JWT: `POST /api/auth/token/`, `POST /api/auth/token/refresh/`, `POST /api/auth/token/verify/`
- Multi-merchant report: `POST /v1/api/settlement-report`
- Single-merchant report: `POST /v1/api/settlement-report/{merchantUID}`
- Transactions: `POST /v1/api/settlement-transactions/{merchantUID}?page=1` (maximum 200 records/page)
- Authenticated documentation: `GET /api/docs/`

Browser administration is available under **Portal → Accounts** and **Portal → Merchant access**. Portal administration APIs remain available under `/api/portal/`. Admin views and actions are enforced server-side: admins can only manage users they created or were assigned, and merchant controls are limited to merchants in their own scope. SUPERADMIN has global visibility and management.
