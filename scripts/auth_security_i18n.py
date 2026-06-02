"""Propagate the auth-security i18n keys to dashboard locales.

EN already has the source-of-truth values.  This script merges them
into every other locale (ES/FR/RU/UK get hand-translated; AM/PA/SO/UZ
fall through to EN for now).  Idempotent — re-runs leave the JSON
byte-identical.
"""
from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path

LOCALE_DIR = (
    Path(__file__).resolve().parent.parent
    / "interfaces" / "dashboard" / "src" / "locales"
)
LOCALES = ["es", "fr", "ru", "uk", "pa", "am", "uz", "so"]

NEW_KEYS = (
    "forgot_password", "forgot_title", "forgot_heading", "forgot_helper",
    "forgot_sent_title", "forgot_sent_body", "forgot_failed",
    "send_reset_link", "back_to_login",
    "reset_title", "reset_heading", "reset_helper", "reset_submit",
    "reset_failed", "reset_success_title", "reset_success_body",
    "go_to_login", "new_password", "confirm_password",
    "pwd_min_length", "pwd_needs_letter", "pwd_needs_digit", "pwd_mismatch",
    "verify_title", "verify_in_progress", "verify_success_title",
    "verify_success_body", "verify_failed_title", "verify_failed",
    "verify_no_token",
    "error_email_not_verified", "verification_resend_button",
    "verification_resending", "verification_resent",
    "register_check_inbox",
)

EN_AUTH = json.loads(
    (LOCALE_DIR / "en.json").read_text(encoding="utf-8"),
    object_pairs_hook=OrderedDict,
)["auth"]

OVERRIDES: dict[str, dict] = {
    "es": {
        "forgot_password": "¿Olvidaste tu contraseña?",
        "forgot_title": "Restablecer tu contraseña",
        "forgot_heading": "¿Olvidaste tu contraseña?",
        "forgot_helper": "Introduce tu email y te enviaremos un enlace para elegir una nueva contraseña.",
        "forgot_sent_title": "Revisa tu bandeja de entrada",
        "forgot_sent_body": "Si existe una cuenta con ese email, hemos enviado un enlace de restablecimiento. El enlace expira en 1 hora.",
        "forgot_failed": "No se pudo enviar el correo de restablecimiento",
        "send_reset_link": "Enviar enlace de restablecimiento",
        "back_to_login": "Volver al inicio de sesión",
        "reset_title": "Elige una nueva contraseña",
        "reset_heading": "Establece una nueva contraseña",
        "reset_helper": "Elige una contraseña de al menos 8 caracteres, con una letra y un dígito.",
        "reset_submit": "Establecer nueva contraseña",
        "reset_failed": "No se pudo restablecer la contraseña",
        "reset_success_title": "Contraseña actualizada",
        "reset_success_body": "Inicia sesión con tu nueva contraseña para continuar.",
        "go_to_login": "Ir al inicio de sesión",
        "new_password": "Nueva contraseña",
        "confirm_password": "Confirmar nueva contraseña",
        "pwd_min_length": "Al menos 8 caracteres",
        "pwd_needs_letter": "Debe incluir una letra",
        "pwd_needs_digit": "Debe incluir un dígito",
        "pwd_mismatch": "Las contraseñas no coinciden",
        "verify_title": "Verificación de email",
        "verify_in_progress": "Verificando tu email…",
        "verify_success_title": "Email verificado",
        "verify_success_body": "Tu cuenta está lista. Inicia sesión para comenzar a usar 4truck.",
        "verify_failed_title": "Verificación fallida",
        "verify_failed": "No se pudo verificar el enlace.",
        "verify_no_token": "No hay token de verificación en la URL.",
        "error_email_not_verified": "Por favor verifica tu email antes de iniciar sesión. Revisa tu bandeja de entrada.",
        "verification_resend_button": "Reenviar correo de verificación",
        "verification_resending": "Enviando…",
        "verification_resent": "Correo de verificación enviado. Revisa tu bandeja de entrada.",
        "register_check_inbox": "Hemos enviado un enlace de verificación a {{email}}. Haz clic para activar tu cuenta y luego inicia sesión.",
    },
    "fr": {
        "forgot_password": "Mot de passe oublié ?",
        "forgot_title": "Réinitialiser votre mot de passe",
        "forgot_heading": "Mot de passe oublié ?",
        "forgot_helper": "Saisissez votre email et nous vous enverrons un lien pour choisir un nouveau mot de passe.",
        "forgot_sent_title": "Vérifiez votre boîte de réception",
        "forgot_sent_body": "Si un compte existe pour cet email, nous avons envoyé un lien de réinitialisation. Le lien expire dans 1 heure.",
        "forgot_failed": "Impossible d'envoyer l'email de réinitialisation",
        "send_reset_link": "Envoyer le lien de réinitialisation",
        "back_to_login": "Retour à la connexion",
        "reset_title": "Choisir un nouveau mot de passe",
        "reset_heading": "Définir un nouveau mot de passe",
        "reset_helper": "Choisissez un mot de passe d'au moins 8 caractères, avec une lettre et un chiffre.",
        "reset_submit": "Définir le nouveau mot de passe",
        "reset_failed": "Impossible de réinitialiser le mot de passe",
        "reset_success_title": "Mot de passe mis à jour",
        "reset_success_body": "Connectez-vous avec votre nouveau mot de passe pour continuer.",
        "go_to_login": "Aller à la connexion",
        "new_password": "Nouveau mot de passe",
        "confirm_password": "Confirmer le nouveau mot de passe",
        "pwd_min_length": "Au moins 8 caractères",
        "pwd_needs_letter": "Doit inclure une lettre",
        "pwd_needs_digit": "Doit inclure un chiffre",
        "pwd_mismatch": "Les mots de passe ne correspondent pas",
        "verify_title": "Vérification de l'email",
        "verify_in_progress": "Vérification de votre email…",
        "verify_success_title": "Email vérifié",
        "verify_success_body": "Votre compte est prêt. Connectez-vous pour commencer à utiliser 4truck.",
        "verify_failed_title": "Échec de la vérification",
        "verify_failed": "Impossible de vérifier le lien.",
        "verify_no_token": "Aucun jeton de vérification dans l'URL.",
        "error_email_not_verified": "Veuillez vérifier votre email avant de vous connecter. Consultez votre boîte de réception.",
        "verification_resend_button": "Renvoyer l'email de vérification",
        "verification_resending": "Envoi…",
        "verification_resent": "Email de vérification envoyé. Consultez votre boîte de réception.",
        "register_check_inbox": "Nous avons envoyé un lien de vérification à {{email}}. Cliquez dessus pour activer votre compte, puis connectez-vous.",
    },
    "ru": {
        "forgot_password": "Забыли пароль?",
        "forgot_title": "Сброс пароля",
        "forgot_heading": "Забыли пароль?",
        "forgot_helper": "Введите ваш email, и мы отправим ссылку для смены пароля.",
        "forgot_sent_title": "Проверьте почту",
        "forgot_sent_body": "Если аккаунт с таким email существует, мы отправили ссылку для сброса. Срок действия ссылки — 1 час.",
        "forgot_failed": "Не удалось отправить письмо",
        "send_reset_link": "Отправить ссылку",
        "back_to_login": "Назад ко входу",
        "reset_title": "Новый пароль",
        "reset_heading": "Установить новый пароль",
        "reset_helper": "Выберите пароль не менее 8 символов, с буквой и цифрой.",
        "reset_submit": "Установить пароль",
        "reset_failed": "Не удалось сбросить пароль",
        "reset_success_title": "Пароль обновлён",
        "reset_success_body": "Войдите с новым паролем для продолжения.",
        "go_to_login": "Войти",
        "new_password": "Новый пароль",
        "confirm_password": "Подтвердите пароль",
        "pwd_min_length": "Не менее 8 символов",
        "pwd_needs_letter": "Должен содержать букву",
        "pwd_needs_digit": "Должен содержать цифру",
        "pwd_mismatch": "Пароли не совпадают",
        "verify_title": "Подтверждение email",
        "verify_in_progress": "Проверяем ваш email…",
        "verify_success_title": "Email подтверждён",
        "verify_success_body": "Аккаунт готов. Войдите, чтобы начать пользоваться 4truck.",
        "verify_failed_title": "Подтверждение не удалось",
        "verify_failed": "Не удалось проверить ссылку.",
        "verify_no_token": "В URL нет токена подтверждения.",
        "error_email_not_verified": "Подтвердите ваш email перед входом. Проверьте почту.",
        "verification_resend_button": "Отправить письмо ещё раз",
        "verification_resending": "Отправляем…",
        "verification_resent": "Письмо отправлено. Проверьте почту.",
        "register_check_inbox": "Мы отправили ссылку подтверждения на {{email}}. Перейдите по ней, чтобы активировать аккаунт, затем войдите.",
    },
    "uk": {
        "forgot_password": "Забули пароль?",
        "forgot_title": "Скидання пароля",
        "forgot_heading": "Забули пароль?",
        "forgot_helper": "Введіть email, і ми надішлемо посилання для зміни пароля.",
        "forgot_sent_title": "Перевірте вашу пошту",
        "forgot_sent_body": "Якщо обліковий запис з таким email існує, ми надіслали посилання для скидання. Посилання дійсне 1 годину.",
        "forgot_failed": "Не вдалося надіслати лист",
        "send_reset_link": "Надіслати посилання",
        "back_to_login": "Назад до входу",
        "reset_title": "Новий пароль",
        "reset_heading": "Встановити новий пароль",
        "reset_helper": "Виберіть пароль щонайменше з 8 символів, з літерою та цифрою.",
        "reset_submit": "Встановити пароль",
        "reset_failed": "Не вдалося скинути пароль",
        "reset_success_title": "Пароль оновлено",
        "reset_success_body": "Увійдіть з новим паролем, щоб продовжити.",
        "go_to_login": "Увійти",
        "new_password": "Новий пароль",
        "confirm_password": "Підтвердьте пароль",
        "pwd_min_length": "Щонайменше 8 символів",
        "pwd_needs_letter": "Має містити літеру",
        "pwd_needs_digit": "Має містити цифру",
        "pwd_mismatch": "Паролі не збігаються",
        "verify_title": "Підтвердження email",
        "verify_in_progress": "Перевіряємо ваш email…",
        "verify_success_title": "Email підтверджено",
        "verify_success_body": "Обліковий запис готовий. Увійдіть, щоб почати користуватися 4truck.",
        "verify_failed_title": "Підтвердження не вдалося",
        "verify_failed": "Не вдалося перевірити посилання.",
        "verify_no_token": "У URL немає токена підтвердження.",
        "error_email_not_verified": "Будь ласка, підтвердьте email перед входом. Перевірте пошту.",
        "verification_resend_button": "Надіслати лист підтвердження ще раз",
        "verification_resending": "Надсилаємо…",
        "verification_resent": "Лист підтвердження надіслано. Перевірте пошту.",
        "register_check_inbox": "Ми надіслали посилання підтвердження на {{email}}. Натисніть на нього, щоб активувати обліковий запис, а потім увійдіть.",
    },
}


def apply_to(locale: str) -> None:
    path = LOCALE_DIR / f"{locale}.json"
    data = json.loads(
        path.read_text(encoding="utf-8"), object_pairs_hook=OrderedDict,
    )
    auth = data.setdefault("auth", OrderedDict())
    overrides = OVERRIDES.get(locale, {})
    for key in NEW_KEYS:
        # Hand-translation wins; otherwise fall back to EN value.
        if key in overrides:
            auth[key] = overrides[key]
        else:
            auth[key] = EN_AUTH[key]
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"  {locale}: ok")


def main() -> int:
    print(f"Propagating auth-security i18n to {len(LOCALES)} locales:")
    for loc in LOCALES:
        apply_to(loc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
