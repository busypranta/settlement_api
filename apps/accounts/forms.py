from django import forms
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from apps.merchants.models import PortalMerchant
from .models import User
from .validators import normalize_bd_mobile


class UserCreateForm(forms.ModelForm):
    password = forms.CharField(
        max_length=16,
        widget=forms.PasswordInput(attrs={
            'autocomplete': 'new-password',
            'data-password-strength': 'true',
            'minlength': '8',
            'maxlength': '16',
            'data-password-toggle': 'true',
        }),
        help_text='Use 8–16 characters. Combine upper- and lowercase letters, numbers, and a symbol. Avoid personal details.',
    )

    class Meta:
        model = User
        fields = ('username', 'name', 'mobile', 'email', 'role')
        labels = {'name': 'Name'}
        widgets = {
            'name': forms.TextInput(attrs={'autocomplete': 'name', 'minlength': '2', 'maxlength': '100'}),
            'mobile': forms.TelInput(attrs={'autocomplete': 'tel', 'inputmode': 'tel', 'maxlength': '16'}),
            'email': forms.EmailInput(attrs={'autocomplete': 'email'}),
        }

    def __init__(self, *args, actor, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor
        if actor.role == User.ROLE_SUPERADMIN or actor.is_superuser:
            self.fields['role'].choices = (
                (User.ROLE_USER, 'User'),
                (User.ROLE_ADMIN, 'Admin'),
            )
        else:
            self.fields['role'].choices = ((User.ROLE_USER, 'User'),)
            self.fields['role'].initial = User.ROLE_USER
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')
        self.fields['name'].required = True
        self.fields['name'].min_length = 2
        self.fields['mobile'].required = True
        self.fields['email'].required = True

    def clean_mobile(self):
        return normalize_bd_mobile(self.cleaned_data['mobile'])

    def clean_name(self):
        name = self.cleaned_data['name'].strip()
        if len(name) < 2:
            raise forms.ValidationError('Name must be at least 2 characters long.')
        return name

    def clean_password(self):
        password = self.cleaned_data['password']
        try:
            validate_password(password)
        except ValidationError as exc:
            raise forms.ValidationError(exc.messages) from exc
        return password

    def save(self, commit=True):
        user = super().save(commit=False)
        user.created_by = self.actor
        user.is_staff = False
        user.is_superuser = False
        user.set_password(self.cleaned_data['password'])
        if commit:
            user.save()
        return user


class AdminPasswordResetForm(forms.Form):
    password = forms.CharField(
        label='Temporary password',
        max_length=16,
        widget=forms.PasswordInput(attrs={
            'autocomplete': 'new-password',
            'class': 'form-control',
            'data-password-strength': 'true',
            'maxlength': '16',
            'data-password-toggle': 'true',
        }),
        help_text='Use 8–16 characters with a mix of letters, numbers, and symbols.',
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_password(self):
        password = self.cleaned_data['password']
        try:
            validate_password(password, self.user)
        except ValidationError as exc:
            raise forms.ValidationError(exc.messages) from exc
        return password


class MerchantCreateForm(forms.ModelForm):
    class Meta:
        model = PortalMerchant
        fields = ('merchant_uid', 'merchant_name')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')


class MerchantAssignmentForm(forms.Form):
    user = forms.ModelChoiceField(queryset=User.objects.none(), label='Account')
    merchant = forms.ModelChoiceField(queryset=PortalMerchant.objects.none(), label='Merchant')

    def __init__(self, *args, users, merchants=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['user'].queryset = users.filter(role=User.ROLE_USER).order_by('username')
        merchant_queryset = merchants if merchants is not None else PortalMerchant.objects.all()
        self.fields['merchant'].queryset = merchant_queryset.filter(is_active=True).order_by('merchant_name')
        self.fields['user'].widget.attrs.update({
            'class': 'form-control',
            'data-searchable-select': 'true',
        })
        self.fields['merchant'].widget.attrs.update({
            'class': 'form-control',
            'data-searchable-select': 'true',
        })


class AdminUserAssignmentForm(forms.Form):
    admin = forms.ModelChoiceField(queryset=User.objects.none(), label='Admin')
    user = forms.ModelChoiceField(queryset=User.objects.none(), label='User')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['admin'].queryset = User.objects.filter(role=User.ROLE_ADMIN).order_by('username')
        self.fields['user'].queryset = User.objects.filter(role=User.ROLE_USER).order_by('username')
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'
            field.widget.attrs['data-searchable-select'] = 'true'


class ProfileForm(forms.ModelForm):
    def __init__(self, *args, partial=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['name'].required = not partial
        self.fields['mobile'].required = not partial
        self.fields['email'].required = not partial
        if partial and self.is_bound:
            for field in ('name', 'mobile', 'email'):
                if field not in self.data:
                    self.fields.pop(field)

    class Meta:
        model = User
        fields = ('name', 'mobile', 'email')
        widgets = {
            'name': forms.TextInput(attrs={'autocomplete': 'name', 'minlength': '2', 'maxlength': '100'}),
            'mobile': forms.TelInput(attrs={'autocomplete': 'tel', 'inputmode': 'tel', 'maxlength': '16'}),
            'email': forms.EmailInput(attrs={'autocomplete': 'email'}),
        }
        labels = {'name': 'Name'}

    def clean_name(self):
        name = self.cleaned_data.get('name', '').strip()
        if not name or len(name) < 2:
            raise forms.ValidationError('Name must be at least 2 characters long.')
        return name

    def clean_mobile(self):
        mobile = self.cleaned_data.get('mobile', '')
        return normalize_bd_mobile(mobile)

    def clean_email(self):
        email = self.cleaned_data.get('email', '').strip()
        if not email:
            raise forms.ValidationError('Email address is required.')
        return email


class PortalPasswordChangeForm(PasswordChangeForm):
    old_password = forms.CharField(
        label='Current password',
        widget=forms.PasswordInput(attrs={'autocomplete': 'current-password', 'data-password-toggle': 'true', 'placeholder': 'Enter current password'}),
    )
    new_password1 = forms.CharField(
        label='New password',
        max_length=16,
        widget=forms.PasswordInput(attrs={
            'autocomplete': 'new-password',
            'data-password-strength': 'true',
            'minlength': '8',
            'maxlength': '16',
            'data-password-toggle': 'true',
            'placeholder': 'Enter new password',
        }),
        help_text='Use 8-16 chars. Avoid reused passwords and personal details.',
        strip=False,
    )
    new_password2 = forms.CharField(
        label='Confirm new password',
        max_length=16,
        widget=forms.PasswordInput(attrs={
            'autocomplete': 'new-password', 'minlength': '8', 'maxlength': '16',
            'data-password-toggle': 'true',
            'placeholder': 'Confirm new password',
            }),
        strip=False,
    )
