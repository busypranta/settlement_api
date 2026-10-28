from rest_framework import serializers


class DateRangeSerializer(serializers.Serializer):
    from_date = serializers.DateField(input_formats=['%d-%m-%Y'])
    to_date = serializers.DateField(input_formats=['%d-%m-%Y'])

    def validate(self, attrs):
        if attrs['from_date'] > attrs['to_date']:
            raise serializers.ValidationError({'from_date': 'From date cannot be after to date.'})
        return attrs


class SettlementReportDateRangeSerializer(DateRangeSerializer):
    def validate(self, attrs):
        attrs = super().validate(attrs)
        if (attrs['to_date'] - attrs['from_date']).days + 1 > 60:
            raise serializers.ValidationError({'to_date': 'The date range cannot exceed 60 calendar days.'})
        return attrs


class MultiMerchantReportSerializer(SettlementReportDateRangeSerializer):
    merchants = serializers.ListField(
        child=serializers.CharField(max_length=64, allow_blank=False, trim_whitespace=True),
        allow_empty=False,
    )


class PageNumberSerializer(serializers.Serializer):
    page = serializers.IntegerField(min_value=1, default=1)
