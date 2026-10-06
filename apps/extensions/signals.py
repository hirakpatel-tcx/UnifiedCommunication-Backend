from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.extensions.models import Extension


@receiver(post_save, sender=Extension)
@receiver(post_delete, sender=Extension)
def bust_extension_label_cache(sender, instance, **kwargs):
    if instance.tenant_id:
        from apps.common.cdr_views import invalidate_extension_label_cache
        invalidate_extension_label_cache(instance.tenant_id)
