# Oracle Schema Setup

The portal uses two Oracle connections:

- `PORTAL_ORACLE_*` points to the dedicated portal schema and can write only its own portal tables.
- `SOURCE_ORACLE_*` points to a separate read-only account with `SELECT` on the specific source objects needed by the reports.

Do not grant the source account write, DDL, `SELECT ANY TABLE`, or broad schema privileges. Do not grant the reconciliation application write access to the portal schema. Provision credentials through the deployment secret manager; never store real passwords in SQL files, `.env.example`, or source control.

## Portal schema

Create a dedicated schema through the organization's DBA process, supplying the password from the approved secret manager. The Django migration account needs only the privileges required to connect and create/manage portal-owned tables, sequences, and indexes:

```sql
GRANT CREATE SESSION, CREATE TABLE, CREATE SEQUENCE TO <PORTAL_SCHEMA>;
```

Set `PORTAL_ORACLE_DSN`, `PORTAL_ORACLE_USER`, and `PORTAL_ORACLE_PASSWORD` in the deployment environment. Run migrations with this account only. All portal models use this connection by default.

## Source read-only account

Create a separate source account with `CREATE SESSION`, then grant `SELECT` only on the verified source tables. The likely objects based on the source model contract are:

- `SETTLEMENT_BATCH`
- `SETTLEMENT_LINE`
- `BANK_ACCOUNTS`
- `ACCOUNT_OWNERS`
- `MERCHANTS`
- `VENDORS` and `PROCESSORS`, only if required for owner-type validation
- `INGESTION_OWN_TRANSACTION`
- `DJANGO_CONTENT_TYPE`

The precise schema owner, Oracle object names, and required grants must be verified against the deployed schema before running grants. The example `script.sql` contains only narrow `SELECT` grants and uses SQL*Plus substitution variables for schema and account names.

Set `SOURCE_ORACLE_DSN`, `SOURCE_ORACLE_USER`, and `SOURCE_ORACLE_PASSWORD` to this read-only account. Source Django models are unmanaged and routed to the `source` connection. The database account's lack of write privileges is the authoritative protection; a Django router alone cannot prevent writes made by code that explicitly selects a database alias.

## Deployment and verification

1. Copy `.env.example` to `.env` and insert locally managed values; keep `.env` out of version control.
2. Run `python manage.py check`.
3. Run `python manage.py migrate --database default`; source models are unmanaged and migrations are blocked on `source`.
4. Run `python manage.py createsuperadmin --username <name>` to be prompted for a password, or use `--password-stdin` with a secure secret pipe.
5. Verify `/health/` checks both Oracle connections.

Do not use production credentials in a local development environment. Before production, verify read-only behavior by attempting a controlled write using the source account and confirming Oracle rejects it.
