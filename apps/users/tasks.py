"""
apps/users/tasks.py
────────────────────
Celery worker: sends the welcome email for a newly created user.

The plaintext password is passed in as a task argument and used only to
render the email body — it is never persisted anywhere (not on the User
model, not in logs). Once the task finishes, the plaintext no longer exists.
"""

import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse

logger = logging.getLogger(__name__)


def _build_desktop_download_url(user_id, os_name="win") -> str:
    """
    Issues a DesktopDownloadToken for the user and returns its absolute
    fetch URL — a passthrough that streams the installer from update-server
    without ever putting update-server's PUBLISH_TOKEN in the email.
    """
    from apps.downloads.models import DesktopDownloadToken

    token = DesktopDownloadToken.objects.create(user_id=user_id, os=os_name)
    fetch_path = reverse("downloads-fetch", kwargs={"token": token.token})
    return f"{settings.API_BASE_URL.rstrip('/')}{fetch_path}"


@shared_task(name="apps.users.tasks.send_welcome_email")
def send_welcome_email(
    email: str,
    plaintext_password: str,
    is_temp_password: bool,
    first_name: str = "",
    user_id=None,
):
    """
    Sends the welcome email for a newly created user.

    - is_temp_password=True  -> "here is your temporary password, you'll be asked to change it on login"
    - is_temp_password=False -> "here is the password shared by your admin"
    """
    greeting = f"Hi {first_name}," if first_name else "Hi,"

    if is_temp_password:
        password_line = (
            "A temporary password has been generated for your account. "
            "You will be asked to set a new password the first time you log in."
        )
    else:
        password_line = "Please use the password shared by your admin to log in."

    download_url = _build_desktop_download_url(user_id) if user_id else None

    subject = "Welcome to TCX Connect — your account is ready"
    context = {
        "greeting": greeting,
        "email": email,
        "plaintext_password": plaintext_password,
        "password_line": password_line,
        "download_url": download_url,
        "download_ttl_hours": settings.DOWNLOAD_TOKEN_TTL_HOURS,
    }
    text_body = (
        f"{greeting}\n\n"
        f"Your account has been created.\n\n"
        f"Email: {email}\n"
        f"Password: {plaintext_password}\n\n"
        f"{password_line}\n\n"
        + (
            f"Download TCX Connect for Windows: {download_url}\n"
            f"(this link is valid for {settings.DOWNLOAD_TOKEN_TTL_HOURS} hours after you first open it)\n\n"
            if download_url
            else ""
        )
        + "If you did not expect this email, please contact your administrator."
    )
    html_body = render_to_string("users/email/welcome_email.html", context)

    try:
        message = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[email],
        )
        message.attach_alternative(html_body, "text/html")
        message.send(fail_silently=False)
    except Exception as exc:
        logger.error("Failed to send welcome email to %s: %s", email, exc, exc_info=True)
        raise
