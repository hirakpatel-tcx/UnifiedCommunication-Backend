from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from apps.users.models import User


@receiver(pre_save, sender=User)
def _track_name_change(sender, instance, **kwargs):
    if not instance.pk:
        return
    try:
        old = User.objects.only("first_name", "last_name").get(pk=instance.pk)
        instance._name_changed = (
            old.first_name != instance.first_name
            or old.last_name != instance.last_name
        )
    except User.DoesNotExist:
        instance._name_changed = False


@receiver(post_save, sender=User)
def bust_extension_label_cache_on_name_change(sender, instance, created, **kwargs):
    if created or not getattr(instance, "_name_changed", False):
        return
    tenant_id = instance.tenant_id
    if not tenant_id:
        return
    from apps.common.cdr_views import invalidate_extension_label_cache
    invalidate_extension_label_cache(tenant_id)
