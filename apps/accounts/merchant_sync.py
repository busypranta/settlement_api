"""Batched merchant sync from the unmanaged source merchant table.

Each button press reads only one batch of source rows (200 by default). Rows
without a UID are skipped. Existing portal merchants keep their UID and get
only their name updated.
"""
from django.conf import settings
from django.db import DatabaseError, IntegrityError, transaction
from django.utils import timezone

from apps.merchants.models import PortalMerchant
from apps.settlement_api.source_models import Merchant as SourceMerchant

SESSION_KEY = 'merchant_sync_preview'
OFFSET_KEY = 'merchant_sync_offset'


class SyncError(Exception):
    """Base class for user-facing sync failures."""


class SyncFetchError(SyncError):
    """The source merchant table could not be read."""


class SyncApplyError(SyncError):
    """A preview row could not be written to the database."""


def batch_size():
    return int(getattr(settings, 'MERCHANT_SYNC_BATCH_SIZE', 200))


def normalize_source_rows(source_rows):
    """Keep only usable source rows; return (rows, skipped_count)."""
    rows = []
    seen_uids = set()
    seen_names = set()
    skipped = 0
    for row in source_rows:
        uid = str(row.get('uid') or '').strip()
        name = ' '.join(str(row.get('name') or '').split())
        # A UID is required: it is the only key used to match portal merchants.
        if not uid or not name or len(uid) > 64 or len(name) > 150:
            skipped += 1
            continue
        name_key = ' '.join(name.casefold().split())
        if uid in seen_uids or name_key in seen_names:
            skipped += 1
            continue
        seen_uids.add(uid)
        seen_names.add(name_key)
        rows.append({'uid': uid, 'name': name})
    return rows, skipped


def fetch_database_rows(offset=0):
    """Read one batch of (uid, name) pairs from the source database."""
    size = batch_size()
    try:
        source_rows = list(
            SourceMerchant.objects.values('uid', 'name')
            .order_by('uid')[offset:offset + size]
        )
    except DatabaseError as exc:
        raise SyncFetchError(
            f'Could not read merchants from the source database: {exc}'
        ) from exc

    rows, skipped = normalize_source_rows(source_rows)
    return rows, skipped, len(source_rows)


def refresh_counts(preview):
    """Keep the summary counts in sync with the remaining preview rows."""
    items = preview.get('items', [])
    preview['create_count'] = sum(1 for item in items if item['action'] == 'create')
    preview['update_count'] = sum(1 for item in items if item['action'] == 'update')
    preview['total_count'] = len(items)
    return preview


def build_preview(rows, offset=0, next_offset=0):
    """Compare one source batch with the database and split new from changed."""
    merchants = {
        merchant.merchant_uid: merchant
        for merchant in PortalMerchant.objects.filter(merchant_uid__in=[row['uid'] for row in rows])
    }
    items = []
    for index, row in enumerate(rows):
        existing = merchants.get(row['uid'])
        if existing is None:
            items.append({
                'key': f'row-{index}',
                'action': 'create',
                'uid': row['uid'],
                'name': row['name'],
                'current_name': '',
                'error': '',
            })
        elif existing.merchant_name != row['name']:
            items.append({
                'key': f'row-{index}',
                'action': 'update',
                'uid': row['uid'],
                'name': row['name'],
                'current_name': existing.merchant_name,
                'error': '',
            })

    preview = {
        'source': 'database',
        'source_label': 'Source database',
        'row_count': len(rows),
        'offset': offset,
        'next_offset': next_offset,
        'generated_at': timezone.now().isoformat(timespec='seconds'),
        'items': items,
    }
    return refresh_counts(preview)


def apply_item(item, created_by):
    """Create or rename one merchant. Only the name is changed on updates."""
    uid = item['uid']
    name = item['name']
    name_key = ' '.join(name.casefold().split())
    action = item['action']

    with transaction.atomic():
        existing = PortalMerchant.objects.filter(merchant_uid=uid).first()

        if action == 'create':
            if existing is not None:
                raise SyncApplyError('This UID now exists; refresh the preview.')
            if PortalMerchant.objects.filter(merchant_name_key=name_key).exists():
                raise SyncApplyError('That name is already used by another merchant.')
            merchant = PortalMerchant(
                merchant_uid=uid,
                merchant_name=name,
                created_by=created_by,
            )
        elif action == 'update':
            if existing is None:
                raise SyncApplyError('This merchant no longer exists; refresh the preview.')
            if PortalMerchant.objects.filter(merchant_name_key=name_key).exclude(pk=existing.pk).exists():
                raise SyncApplyError('That name is already used by another merchant.')
            merchant = existing
            merchant.merchant_name = name
        else:
            raise SyncApplyError('Unknown preview action.')

        try:
            # Nested atomic creates a savepoint so an IntegrityError here does
            # not poison the outer transaction for the rest of an Apply-all run.
            with transaction.atomic():
                merchant.save()
        except IntegrityError as exc:
            raise SyncApplyError('This UID or name was just taken; refresh the preview.') from exc
        return merchant, action
