-- Run as a DBA after replacing the substitution values with verified schema
-- and account identifiers. Do not place passwords in this file.
DEFINE source_schema = SOURCE_SCHEMA
DEFINE source_readonly_user = PORTAL_SOURCE_RO

GRANT SELECT ON &&source_schema..SETTLEMENT_BATCH TO &&source_readonly_user;
GRANT SELECT ON &&source_schema..SETTLEMENT_LINE TO &&source_readonly_user;
GRANT SELECT ON &&source_schema..BANK_ACCOUNTS TO &&source_readonly_user;
GRANT SELECT ON &&source_schema..ACCOUNT_OWNERS TO &&source_readonly_user;
GRANT SELECT ON &&source_schema..MERCHANTS TO &&source_readonly_user;
GRANT SELECT ON &&source_schema..VENDORS TO &&source_readonly_user;
GRANT SELECT ON &&source_schema..PROCESSORS TO &&source_readonly_user;
GRANT SELECT ON &&source_schema..INGESTION_OWN_TRANSACTION TO &&source_readonly_user;
GRANT SELECT ON &&source_schema..DJANGO_CONTENT_TYPE TO &&source_readonly_user;
