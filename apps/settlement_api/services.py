from decimal import Decimal
from apps.settlement_api.source_models import Merchant, SettlementLine, SourceContentType

def normalize_name_key(name):
    return ' '.join((name or '').strip().casefold().split())


def get_merchant_owner_from_line(line):
    account = line.cr_account
    if account is None or account.owner_type_id is None:
        return None

    content_type = account.owner_type
    if content_type.app_label != 'merchants' or content_type.model != 'merchant':
        return None

    return Merchant.objects.filter(pk=account.owner_id).first()


def approved_settlement_batch_ids_for_merchant(portal_merchant, batch_ids):
    if not batch_ids:
        return set()

    merchant_content_type_id = (
        SourceContentType.objects
        .filter(app_label='merchants', model='merchant')
        .values_list('id', flat=True)
        .first()
    )
    if merchant_content_type_id is None:
        return set()

    line_data = list(
        SettlementLine.objects
        .filter(
            batch_id__in=batch_ids,
            cr_account__owner_type_id=merchant_content_type_id,
        )
        .values_list('batch_id', 'status', 'cr_account__owner_id')
    )
    owner_ids = {owner_id for _, _, owner_id in line_data}
    merchants_by_id = {
        merchant.pk: merchant
        for merchant in Merchant.objects.filter(pk__in=owner_ids)
    }
    merchant_name_key = normalize_name_key(portal_merchant.merchant_name)
    approved_batch_ids = set()
    for batch_id, line_status, owner_id in line_data:
        owner = merchants_by_id.get(owner_id)
        if (
            owner is not None
            and normalize_name_key(owner.name) == merchant_name_key
            and (line_status or '').strip().upper() == 'APPROVED'
        ):
            approved_batch_ids.add(batch_id)
    return approved_batch_ids


def compute_settlement_for_merchant(portal_merchant, from_date, to_date):
    merchant_content_type_id = (
        SourceContentType.objects
        .filter(app_label='merchants', model='merchant')
        .values_list('id', flat=True)
        .first()
    )
    if merchant_content_type_id is None:
        return []

    merchant_name_key = normalize_name_key(portal_merchant.merchant_name)
    results = []
    lines = (
        SettlementLine.objects
        .filter(
            cr_account__owner_type_id=merchant_content_type_id,
            batch__date_from__gte=from_date,
            batch__date_from__lte=to_date,
        )
        .only(
            'cr_account',
            'amount',
            'status',
            'created_at',
            'approved_at',
            'batch__date_from',
            'batch__date_to',
        )
        .select_related('cr_account', 'batch')
        .order_by('created_at', 'pk')
    )
    owner_ids = {line.cr_account.owner_id for line in lines}
    merchants_by_id = {
        merchant.pk: merchant
        for merchant in Merchant.objects.filter(pk__in=owner_ids)
    }

    for line in lines:
        owner = merchants_by_id.get(line.cr_account.owner_id)
        if owner is None or normalize_name_key(owner.name) != merchant_name_key:
            continue

        amount = line.amount or Decimal('0')
        status = 'SUCCESS' if (line.status or '').strip().upper() == 'APPROVED' else 'PENDING'
        batch_period = (
            f'{line.batch.date_from:%d-%m-%Y} - {line.batch.date_to:%d-%m-%Y}'
        )
        results.append({
            'transaction_period': batch_period,
            'payment_date': (
                line.approved_at.strftime('%d-%m-%Y')
                if line.approved_at is not None
                else None
            ),
            'total_amount': f'{amount:.2f}',
            'paid_amount': f'{amount:.2f}' if status == 'SUCCESS' else '0.00',
            'status': status,
        })
    return results
