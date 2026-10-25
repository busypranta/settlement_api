from rest_framework import serializers


class DateRangeSerializer(serializers.Serializer):
    from_date = serializers.DateField(input_formats=['%d-%m-%Y'])
    to_date = serializers.DateField(input_formats=['%d-%m-%Y'])

    def validate(self, attrs):
        if attrs['from_date'] > attrs['to_date']:
            raise serializers.ValidationError('from_date cannot be after to_date')
        return attrs


class MultiMerchantReportSerializer(DateRangeSerializer):
    merchants = serializers.ListField(
        child=serializers.CharField(max_length=64, allow_blank=False, trim_whitespace=True),
        allow_empty=False,
    )


class PageNumberSerializer(serializers.Serializer):
    page = serializers.IntegerField(min_value=1, default=1)
