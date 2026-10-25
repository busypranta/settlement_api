from django.db import models
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType


class SettlementBatch(models.Model):
    STATUS_CHOICES = [
        ('DRAFT', 'Draft'),
        ('PARTIAL', 'Partially Approved'),
        ('COMPLETED', 'Completed'),
    ]
    date_from = models.DateField()
    date_to = models.DateField()
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='DRAFT')
    created_at = models.DateTimeField()

    class Meta:
        app_label = 'source'
        db_table = 'SETTLEMENT_BATCH'
        managed = False

    def __str__(self):
        return f'Batch #{self.pk}'


class BankAccount(models.Model):
    owner_type = models.ForeignKey(ContentType, on_delete=models.DO_NOTHING)
    owner_id = models.PositiveIntegerField()
    owner = GenericForeignKey('owner_type', 'owner_id')
    name = models.CharField(max_length=150)
    account_number = models.CharField(max_length=50)
    account_title = models.CharField(max_length=150)
    is_active = models.BooleanField(default=True)

    class Meta:
        app_label = 'source'
        db_table = 'bank_accounts'
        managed = False


class AccountOwner(models.Model):
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta:
        app_label = 'source'
        db_table = 'account_owners'
        managed = False


class Vendor(models.Model):
    name = models.CharField(max_length=150)
    is_active = models.BooleanField(default=True)

    class Meta:
        app_label = 'source'
        db_table = 'vendors'
        managed = False


class Processor(models.Model):
    name = models.CharField(max_length=150)
    is_active = models.BooleanField(default=True)

    class Meta:
        app_label = 'source'
        db_table = 'processors'
        managed = False


class Merchant(models.Model):
    name = models.CharField(max_length=150)
    is_active = models.BooleanField(default=True)

    class Meta:
        app_label = 'source'
        db_table = 'merchants'
        managed = False


class SettlementLine(models.Model):
    batch = models.ForeignKey(SettlementBatch, on_delete=models.DO_NOTHING, db_column='batch_id', related_name='lines')
    dr_account = models.ForeignKey(BankAccount, on_delete=models.DO_NOTHING, db_column='dr_account_id', related_name='settlement_dr_lines')
    cr_account = models.ForeignKey(BankAccount, on_delete=models.DO_NOTHING, db_column='cr_account_id', related_name='settlement_cr_lines')
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    ft_status = models.CharField(max_length=20, blank=True, default='')
    status = models.CharField(max_length=20, blank=True, default='')
    created_at = models.DateTimeField()

    class Meta:
        app_label = 'source'
        db_table = 'SETTLEMENT_LINE'
        managed = False


class OwnTransaction(models.Model):
    txn_ref = models.CharField(max_length=64)
    merchant_name = models.CharField(max_length=150, blank=True)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    merchant_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=20)
    txn_datetime = models.DateTimeField()
    settlement_batch = models.ForeignKey(SettlementBatch, on_delete=models.DO_NOTHING, null=True, blank=True, db_column='settlement_batch_id')
    created_at = models.DateTimeField()

    class Meta:
        app_label = 'source'
        db_table = 'INGESTION_OWN_TRANSACTION'
        managed = False
