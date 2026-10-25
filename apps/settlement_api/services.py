from collections import defaultdict
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType

from apps.settlement_api.source_models import Merchant, SettlementBatch, SettlementLine


def normalize_name_key(name):
    return ' '.join((name or '').strip().casefold().split())


def get_merchant_owner_from_line(line):
    account = line.cr_account
    if account is None or account.owner_type_id is None:
        return None

    content_type = account.owner_type
    if content_type.app_label != 'merchants' or content_type.model != 'merchant':
        return None

    return Merchant.objects.using('source').filter(pk=account.owner_id).first()


def compute_settlement_for_merchant(portal_merchant, from_date, to_date):
    merchant_content_type_id = (
        ContentType.objects.using('source')
        .filter(app_label='merchants', model='merchant')
        .values_list('pk', flat=True)
        .first()
    )
    if merchant_content_type_id is None:
        return []

    batches = SettlementBatch.objects.using('source').filter(
        date_from__lte=to_date,
        date_to__gte=from_date,
        status__in=('PARTIAL', 'COMPLETED'),
    ).order_by('date_from', 'pk')

    merchant_name_key = normalize_name_key(portal_merchant.merchant_name)
    results = []
    for batch in batches:
        lines = (
            SettlementLine.objects.using('source')
            .filter(batch_id=batch.pk, cr_account__owner_type_id=merchant_content_type_id)
            .select_related('cr_account')
        )
        owner_ids = {line.cr_account.owner_id for line in lines}
        merchants_by_id = {
            merchant.pk: merchant
            for merchant in Merchant.objects.using('source').filter(pk__in=owner_ids)
        }

        total = Decimal('0')
        paid = Decimal('0')
        successful_count = 0
        unsuccessful_count = 0
        for line in lines:
            owner = merchants_by_id.get(line.cr_account.owner_id)
            if owner is None or normalize_name_key(owner.name) != merchant_name_key:
                continue

            amount = line.amount or Decimal('0')
            total += amount
            if line.ft_status == 'SUCCESS':
                successful_count += 1
                paid += amount
            else:
                unsuccessful_count += 1

        if successful_count + unsuccessful_count == 0:
            continue
        if unsuccessful_count == 0:
            status = 'SUCCESS'
        elif successful_count == 0:
            status = 'PENDING'
        else:
            status = 'PARTIAL'

        results.append({
            'batch_id': batch.pk,
            'date_from': batch.date_from.strftime('%d-%m-%Y'),
            'date_to': batch.date_to.strftime('%d-%m-%Y'),
            'total_amount': f'{total:.2f}',
            'paid_amount': f'{paid:.2f}',
            'status': status,
        })
    return results
