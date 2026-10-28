from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.merchants.models import PortalMerchant
from .permissions import merchant_is_allowed
from .serializers import (
    DateRangeSerializer,
    MultiMerchantReportSerializer,
    PageNumberSerializer,
    SettlementReportDateRangeSerializer,
)
from .services import approved_settlement_batch_ids_for_merchant, compute_settlement_for_merchant
from .source_models import OwnTransaction


class SettlementReportMultiView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if request.user.role == User.ROLE_USER and not request.user.allow_multiple_merchants:
            return Response(
                {'success': False, 'error': 'Multiple-merchant API access is not enabled for this account'},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = MultiMerchantReportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        merchants = PortalMerchant.objects.filter(
            merchant_uid__in=data['merchants'],
            is_active=True,
        )
        merchants_by_uid = {merchant.merchant_uid: merchant for merchant in merchants}
        if len(merchants_by_uid) != len(set(data['merchants'])):
            return Response(
                {'success': False, 'error': 'One or more merchants do not exist or are inactive'},
                status=status.HTTP_404_NOT_FOUND,
            )

        result = []
        for merchant_uid in data['merchants']:
            merchant = merchants_by_uid[merchant_uid]
            if not merchant_is_allowed(request.user, merchant):
                return Response(
                    {'success': False, 'error': 'Not authorized for this merchant'},
                    status=status.HTTP_403_FORBIDDEN,
                )
            result.append({
                'merchant_uid': merchant.merchant_uid,
                'merchant_name': merchant.merchant_name,
                'settlements': compute_settlement_for_merchant(
                    merchant, data['from_date'], data['to_date'],
                ),
            })
        return Response({'success': True, 'data': result})


class SettlementReportSingleView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, merchant_uid):
        serializer = SettlementReportDateRangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        merchant = get_object_or_404(PortalMerchant, merchant_uid=merchant_uid, is_active=True)
        if not merchant_is_allowed(request.user, merchant):
            return Response(
                {'success': False, 'error': 'Not authorized for this merchant'},
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response({'success': True, 'data': [{
            'merchant_uid': merchant.merchant_uid,
            'merchant_name': merchant.merchant_name,
            'settlements': compute_settlement_for_merchant(
                merchant, data['from_date'], data['to_date'],
            ),
        }]})


class SettlementTransactionsView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, merchant_uid):
        date_serializer = DateRangeSerializer(data=request.data)
        date_serializer.is_valid(raise_exception=True)
        page_serializer = PageNumberSerializer(data={'page': request.query_params.get('page', 1)})
        page_serializer.is_valid(raise_exception=True)
        data = date_serializer.validated_data
        page = page_serializer.validated_data['page']

        merchant = get_object_or_404(PortalMerchant, merchant_uid=merchant_uid, is_active=True)
        if not merchant_is_allowed(request.user, merchant):
            return Response(
                {'success': False, 'error': 'Not authorized for this merchant'},
                status=status.HTTP_403_FORBIDDEN,
            )

        queryset = OwnTransaction.objects.using('source').filter(
            merchant_name__iexact=merchant.merchant_name,
            settlement_batch__isnull=False,
            settlement_batch__status__in=('PARTIAL', 'COMPLETED'),
            txn_datetime__date__gte=data['from_date'],
            txn_datetime__date__lte=data['to_date'],
        ).order_by('txn_datetime', 'pk')
        try:
            total_records = queryset.count()
        except Exception as e:
            return Response(
                {'success': False, 'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        page_size = 200
        total_pages = (total_records + page_size - 1) // page_size
        transactions = list(queryset[(page - 1) * page_size:page * page_size])
        approved_batch_ids = approved_settlement_batch_ids_for_merchant(
            merchant,
            {transaction.settlement_batch_id for transaction in transactions},
        )

        results = []
        for transaction in transactions:
            amount = transaction.merchant_amount
            results.append({
                'date': transaction.txn_datetime.strftime('%d-%m-%Y'),
                'amount': f'{amount:.2f}' if amount is not None else None,
                'status': 'SUCCESS' if transaction.settlement_batch_id in approved_batch_ids else 'PENDING',
            })
        return Response({
            'success': True,
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total_records': total_records,
                'total_pages': total_pages,
            },
            'data': results,
        })
