"""Refresh the storage-admin i18n keys after the truth/layout/polish pass.

Removes the old ``storage.health.*`` + ``storage.routing_*`` + ``storage.usage_*``
keys, then writes the new ``storage.status.*`` + ``storage.settings.*`` +
extended ``storage.files.*`` namespaces.

Idempotent: re-running leaves the JSON byte-identical.  Per-locale strings
are hand-translated where I have a working translator; otherwise we copy
the English string so the UI never shows ``[key]`` placeholders.
"""
from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path

LOCALE_DIR = Path(__file__).resolve().parent.parent / "interfaces" / "dashboard" / "src" / "locales"
LOCALES = ["es", "fr", "ru", "uk", "pa", "am", "uz", "so"]

# Keys to remove from the storage.* subtree before re-adding fresh ones.
STALE_TOP_KEYS = {
    "routing_drive_header_connected",
    "routing_drive_header_disconnected",
    "routing_platform_header",
    "usage_used_by_app",
    "usage_quota_unavailable",
    "usage_not_connected",
    "health",  # entire subtree replaced by status
}

# English source of truth.  Other locales fall back to this if no
# translation is provided.
EN = {
    "badge_disk": "Local storage",
    "badge_hybrid": "Hybrid (Disk → Drive)",
    "disconnect_button": "Disconnect Drive",
    "status": {
        "title": "Current status",
        "loading": "Loading status…",
        "backend_label": "Active backend",
        "drive_connected_as": "Connected as {{email}}",
        "drive_not_connected": "Drive not connected",
        "drive_token_expired": "Drive credentials expired — reconnect to drain queue",
        "disk_note": "Files stay on the platform server. No Drive sync, no quota tracking.",
        "gdrive_note": "Every upload streams directly to your Drive. No local cache.",
        "hybrid_note": "Uploads land on disk first, then push to Drive within ~60 seconds.",
        "local_cache": "Local cache",
        "synced": "On Drive",
        "pending": "Pending sync",
        "stuck": "Stuck",
        "stuck_hint": "Open the Files panel below to retry stuck uploads",
    },
    "settings": {
        "title": "Backend settings",
        "drive_section": "Google Drive",
        "drive_help": "Connect your Drive so we can save files into a folder you own. 15 GB free with a standard Google account.",
        "drive_scope": "Scope granted: drive.file — we can only see and write files we created.",
        "drive_folder": "Folder: My Drive / 4truck /",
        "backend_section": "Storage backend",
        "backend_disk_title": "Local only",
        "backend_disk_desc": "Files saved to the platform server. Simplest setup. Counts against platform storage.",
        "backend_gdrive_title": "Google Drive only",
        "backend_gdrive_desc": "Uploads stream straight to your Drive. Slower per upload, no local cache.",
        "backend_hybrid_title": "Hybrid (recommended)",
        "backend_hybrid_desc": "Fast disk write, then background sync to Drive. Best of both — needs Drive connected.",
        "active": "Active",
        "switch_button": "Use this",
        "switching": "Switching…",
        "switch_done": "Backend switched to {{backend}}.",
        "switch_failed": "Could not switch backend.",
        "needs_drive": "Connect Drive first",
        "routing_title": "What gets saved where",
        "routing_drive_header_connected": "Saved to your Drive",
        "routing_drive_header_disconnected": "Would save to your Drive (when connected)",
        "routing_platform_header": "Always on platform storage",
    },
    "files_extra": {
        "title": "Files needing sync",
        "subtitle": "The disk→Drive sync queue. Synced files live in Drive — they don't show here.",
        "empty_disk": "This account uses local-only storage. Switch to Hybrid in the settings panel to enable Drive sync.",
        "status_pending": "Pending",
        "status_retrying": "Retrying · attempt {{n}}",
        "status_drive_expired": "Drive expired",
        "status_drive_full": "Drive full",
        "status_forbidden": "Permission denied",
        "status_stuck": "Stuck",
        "source_inspection": "Inspection #{{id}}",
        "source_vehicle": "Vehicle {{name}}",
    },
}

# Per-locale overrides.  Anything missing falls through to EN.
LOCALE_OVERRIDES: dict[str, dict] = {
    "es": {
        "badge_disk": "Almacenamiento local",
        "badge_hybrid": "Híbrido (Disco → Drive)",
        "disconnect_button": "Desconectar Drive",
        "status": {
            "title": "Estado actual",
            "loading": "Cargando estado…",
            "backend_label": "Backend activo",
            "drive_connected_as": "Conectado como {{email}}",
            "drive_not_connected": "Drive no conectado",
            "drive_token_expired": "Credenciales de Drive caducadas — reconecta para vaciar la cola",
            "disk_note": "Los archivos permanecen en el servidor de la plataforma. Sin sincronización con Drive ni cuota.",
            "gdrive_note": "Cada subida va directamente a tu Drive. Sin caché local.",
            "hybrid_note": "Las subidas pasan primero al disco y luego se envían a Drive en ~60 segundos.",
            "local_cache": "Caché local",
            "synced": "En Drive",
            "pending": "Pendiente",
            "stuck": "Bloqueado",
            "stuck_hint": "Abre el panel de archivos para reintentar las subidas bloqueadas",
        },
        "settings": {
            "title": "Configuración del backend",
            "drive_section": "Google Drive",
            "drive_help": "Conecta tu Drive para guardar archivos en una carpeta tuya. 15 GB gratis con una cuenta estándar.",
            "drive_scope": "Permiso: drive.file — solo vemos y escribimos archivos creados por nosotros.",
            "drive_folder": "Carpeta: Mi unidad / 4truck /",
            "backend_section": "Backend de almacenamiento",
            "backend_disk_title": "Solo local",
            "backend_disk_desc": "Archivos en el servidor. Configuración más simple. Cuenta en almacenamiento de la plataforma.",
            "backend_gdrive_title": "Solo Google Drive",
            "backend_gdrive_desc": "Las subidas van directo a tu Drive. Más lento por subida, sin caché.",
            "backend_hybrid_title": "Híbrido (recomendado)",
            "backend_hybrid_desc": "Escritura rápida en disco, luego sincronización a Drive en segundo plano. Lo mejor de ambos — requiere Drive conectado.",
            "active": "Activo",
            "switch_button": "Usar este",
            "switching": "Cambiando…",
            "switch_done": "Backend cambiado a {{backend}}.",
            "switch_failed": "No se pudo cambiar el backend.",
            "needs_drive": "Conecta Drive primero",
            "routing_title": "Qué se guarda dónde",
            "routing_drive_header_connected": "Guardado en tu Drive",
            "routing_drive_header_disconnected": "Se guardaría en tu Drive (cuando esté conectado)",
            "routing_platform_header": "Siempre en almacenamiento de la plataforma",
        },
        "files_extra": {
            "title": "Archivos pendientes de sincronizar",
            "subtitle": "Cola de sincronización disco→Drive. Los archivos sincronizados están en Drive — no aparecen aquí.",
            "empty_disk": "Esta cuenta usa solo almacenamiento local. Cambia a híbrido en el panel de configuración para activar la sincronización con Drive.",
            "status_pending": "Pendiente",
            "status_retrying": "Reintentando · intento {{n}}",
            "status_drive_expired": "Drive caducado",
            "status_drive_full": "Drive lleno",
            "status_forbidden": "Permiso denegado",
            "status_stuck": "Bloqueado",
            "source_inspection": "Inspección n.º {{id}}",
            "source_vehicle": "Vehículo {{name}}",
        },
    },
    "fr": {
        "badge_disk": "Stockage local",
        "badge_hybrid": "Hybride (Disque → Drive)",
        "disconnect_button": "Déconnecter Drive",
        "status": {
            "title": "État actuel",
            "loading": "Chargement de l'état…",
            "backend_label": "Backend actif",
            "drive_connected_as": "Connecté en tant que {{email}}",
            "drive_not_connected": "Drive non connecté",
            "drive_token_expired": "Identifiants Drive expirés — reconnectez-vous pour vider la file",
            "disk_note": "Les fichiers restent sur le serveur de la plateforme. Pas de synchronisation Drive ni de quota.",
            "gdrive_note": "Chaque envoi va directement sur votre Drive. Pas de cache local.",
            "hybrid_note": "Les envois arrivent d'abord sur le disque, puis sont synchronisés vers Drive en ~60 secondes.",
            "local_cache": "Cache local",
            "synced": "Sur Drive",
            "pending": "En attente",
            "stuck": "Bloqué",
            "stuck_hint": "Ouvrez le panneau Fichiers pour réessayer les envois bloqués",
        },
        "settings": {
            "title": "Paramètres du backend",
            "drive_section": "Google Drive",
            "drive_help": "Connectez votre Drive pour enregistrer les fichiers dans un dossier qui vous appartient. 15 Go gratuits avec un compte standard.",
            "drive_scope": "Périmètre : drive.file — nous ne pouvons voir et écrire que les fichiers que nous avons créés.",
            "drive_folder": "Dossier : Mon Drive / 4truck /",
            "backend_section": "Backend de stockage",
            "backend_disk_title": "Local uniquement",
            "backend_disk_desc": "Fichiers sur le serveur. Configuration la plus simple. Compte sur le stockage de la plateforme.",
            "backend_gdrive_title": "Google Drive uniquement",
            "backend_gdrive_desc": "Les envois vont directement sur votre Drive. Plus lent par envoi, pas de cache.",
            "backend_hybrid_title": "Hybride (recommandé)",
            "backend_hybrid_desc": "Écriture rapide sur disque, puis synchronisation Drive en arrière-plan. Le meilleur des deux — nécessite Drive connecté.",
            "active": "Actif",
            "switch_button": "Utiliser",
            "switching": "Changement…",
            "switch_done": "Backend changé en {{backend}}.",
            "switch_failed": "Impossible de changer le backend.",
            "needs_drive": "Connectez Drive d'abord",
            "routing_title": "Ce qui est sauvegardé où",
            "routing_drive_header_connected": "Sauvegardé sur votre Drive",
            "routing_drive_header_disconnected": "Sauvegardé sur votre Drive (une fois connecté)",
            "routing_platform_header": "Toujours sur le stockage plateforme",
        },
        "files_extra": {
            "title": "Fichiers à synchroniser",
            "subtitle": "File d'attente disque→Drive. Les fichiers synchronisés sont sur Drive — ils n'apparaissent pas ici.",
            "empty_disk": "Ce compte utilise un stockage local uniquement. Passez en hybride dans les paramètres pour activer la synchronisation Drive.",
            "status_pending": "En attente",
            "status_retrying": "Nouvelle tentative · essai {{n}}",
            "status_drive_expired": "Drive expiré",
            "status_drive_full": "Drive plein",
            "status_forbidden": "Autorisation refusée",
            "status_stuck": "Bloqué",
            "source_inspection": "Inspection n° {{id}}",
            "source_vehicle": "Véhicule {{name}}",
        },
    },
    "ru": {
        "badge_disk": "Локальное хранилище",
        "badge_hybrid": "Гибрид (Диск → Drive)",
        "disconnect_button": "Отключить Drive",
        "status": {
            "title": "Текущее состояние",
            "loading": "Загрузка состояния…",
            "backend_label": "Активный бэкенд",
            "drive_connected_as": "Подключено как {{email}}",
            "drive_not_connected": "Drive не подключён",
            "drive_token_expired": "Срок действия учётных данных Drive истёк — переподключитесь, чтобы очистить очередь",
            "disk_note": "Файлы остаются на сервере платформы. Без синхронизации с Drive и без квоты.",
            "gdrive_note": "Каждая загрузка идёт напрямую в ваш Drive. Без локального кеша.",
            "hybrid_note": "Загрузки попадают сначала на диск, затем отправляются в Drive в течение ~60 секунд.",
            "local_cache": "Локальный кеш",
            "synced": "На Drive",
            "pending": "В очереди",
            "stuck": "Застряло",
            "stuck_hint": "Откройте панель файлов, чтобы повторить застрявшие загрузки",
        },
        "settings": {
            "title": "Настройки бэкенда",
            "drive_section": "Google Drive",
            "drive_help": "Подключите ваш Drive, чтобы файлы сохранялись в вашу собственную папку. 15 ГБ бесплатно со стандартным аккаунтом Google.",
            "drive_scope": "Разрешение: drive.file — мы видим и пишем только файлы, созданные нами.",
            "drive_folder": "Папка: Мой диск / 4truck /",
            "backend_section": "Бэкенд хранилища",
            "backend_disk_title": "Только локально",
            "backend_disk_desc": "Файлы на сервере платформы. Простейшая настройка. Учитывается в квоте платформы.",
            "backend_gdrive_title": "Только Google Drive",
            "backend_gdrive_desc": "Загрузки идут прямо в ваш Drive. Медленнее, без локального кеша.",
            "backend_hybrid_title": "Гибрид (рекомендуется)",
            "backend_hybrid_desc": "Быстрая запись на диск, затем фоновая синхронизация с Drive. Лучшее из двух — требуется подключённый Drive.",
            "active": "Активен",
            "switch_button": "Использовать",
            "switching": "Переключение…",
            "switch_done": "Бэкенд переключён на {{backend}}.",
            "switch_failed": "Не удалось переключить бэкенд.",
            "needs_drive": "Сначала подключите Drive",
            "routing_title": "Что куда сохраняется",
            "routing_drive_header_connected": "Сохраняется в ваш Drive",
            "routing_drive_header_disconnected": "Сохранится в ваш Drive (после подключения)",
            "routing_platform_header": "Всегда в хранилище платформы",
        },
        "files_extra": {
            "title": "Файлы, ожидающие синхронизации",
            "subtitle": "Очередь синхронизации диск→Drive. Синхронизированные файлы лежат в Drive — здесь их нет.",
            "empty_disk": "Этот аккаунт использует только локальное хранилище. Переключитесь на гибрид в настройках, чтобы включить синхронизацию с Drive.",
            "status_pending": "В очереди",
            "status_retrying": "Повтор · попытка {{n}}",
            "status_drive_expired": "Drive просрочен",
            "status_drive_full": "Drive переполнен",
            "status_forbidden": "Доступ запрещён",
            "status_stuck": "Застряло",
            "source_inspection": "Осмотр №{{id}}",
            "source_vehicle": "Транспорт {{name}}",
        },
    },
    "uk": {
        "badge_disk": "Локальне сховище",
        "badge_hybrid": "Гібрид (Диск → Drive)",
        "disconnect_button": "Від'єднати Drive",
        "status": {
            "title": "Поточний стан",
            "loading": "Завантаження стану…",
            "backend_label": "Активний бекенд",
            "drive_connected_as": "Під'єднано як {{email}}",
            "drive_not_connected": "Drive не під'єднано",
            "drive_token_expired": "Термін дії облікових даних Drive завершився — переподключіться, щоб спорожнити чергу",
            "disk_note": "Файли залишаються на сервері платформи. Без синхронізації з Drive і без квоти.",
            "gdrive_note": "Кожне завантаження йде безпосередньо у ваш Drive. Без локального кешу.",
            "hybrid_note": "Завантаження спочатку потрапляють на диск, потім переходять у Drive протягом ~60 секунд.",
            "local_cache": "Локальний кеш",
            "synced": "У Drive",
            "pending": "У черзі",
            "stuck": "Зависло",
            "stuck_hint": "Відкрийте панель Файли, щоб повторити завислі завантаження",
        },
        "settings": {
            "title": "Налаштування бекенду",
            "drive_section": "Google Drive",
            "drive_help": "Під'єднайте ваш Drive, щоб файли зберігалися у вашу папку. 15 ГБ безкоштовно зі стандартним обліковим записом.",
            "drive_scope": "Дозвіл: drive.file — ми бачимо й пишемо тільки файли, створені нами.",
            "drive_folder": "Папка: Мій диск / 4truck /",
            "backend_section": "Бекенд сховища",
            "backend_disk_title": "Лише локально",
            "backend_disk_desc": "Файли на сервері платформи. Найпростіше налаштування. Враховується в квоті платформи.",
            "backend_gdrive_title": "Тільки Google Drive",
            "backend_gdrive_desc": "Завантаження йдуть напряму у ваш Drive. Повільніше, без кешу.",
            "backend_hybrid_title": "Гібрид (рекомендовано)",
            "backend_hybrid_desc": "Швидкий запис на диск, потім фонова синхронізація з Drive. Найкраще з обох — потрібен під'єднаний Drive.",
            "active": "Активно",
            "switch_button": "Використати",
            "switching": "Перемикання…",
            "switch_done": "Бекенд перемкнено на {{backend}}.",
            "switch_failed": "Не вдалося перемкнути бекенд.",
            "needs_drive": "Спочатку під'єднайте Drive",
            "routing_title": "Що куди зберігається",
            "routing_drive_header_connected": "Збережено у ваш Drive",
            "routing_drive_header_disconnected": "Збережеться у ваш Drive (після під'єднання)",
            "routing_platform_header": "Завжди у сховищі платформи",
        },
        "files_extra": {
            "title": "Файли, що очікують синхронізації",
            "subtitle": "Черга синхронізації диск→Drive. Синхронізовані файли є у Drive — тут їх немає.",
            "empty_disk": "Цей обліковий запис використовує лише локальне сховище. Перемкніться на гібрид у панелі налаштувань, щоб увімкнути синхронізацію з Drive.",
            "status_pending": "У черзі",
            "status_retrying": "Повтор · спроба {{n}}",
            "status_drive_expired": "Drive прострочено",
            "status_drive_full": "Drive переповнено",
            "status_forbidden": "Доступ заборонено",
            "status_stuck": "Зависло",
            "source_inspection": "Огляд №{{id}}",
            "source_vehicle": "Транспорт {{name}}",
        },
    },
    # Remaining locales fall back to EN by design — translation will land
    # in a follow-up pass when we get a native speaker.
}


def _merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base. Override wins on leaves."""
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _ordered(d: dict) -> "OrderedDict[str, object]":
    """JSON load preserves insertion order; helper keeps that obvious."""
    return OrderedDict(d.items())


def apply_to(locale: str) -> None:
    path = LOCALE_DIR / f"{locale}.json"
    data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=OrderedDict)

    storage = data.get("storage")
    if not isinstance(storage, OrderedDict):
        print(f"  {locale}: no storage namespace, skip")
        return

    # Drop the stale keys (old health/* subtree, routing_*, usage_*).
    for k in list(storage.keys()):
        if k in STALE_TOP_KEYS:
            del storage[k]

    # Resolve per-locale translations with EN fallback.
    payload = _merge(EN, LOCALE_OVERRIDES.get(locale, {}))

    # Apply: top-level scalars + new subtrees.
    storage["badge_disk"] = payload["badge_disk"]
    storage["badge_hybrid"] = payload["badge_hybrid"]
    storage["disconnect_button"] = payload["disconnect_button"]
    storage["status"] = _ordered(payload["status"])
    storage["settings"] = _ordered(payload["settings"])

    # Extend storage.files with new keys (keep existing entries).
    files = storage.setdefault("files", OrderedDict())
    extra = payload["files_extra"]
    for k, v in extra.items():
        files[k] = v

    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"  {locale}: ok")


def main() -> int:
    print(f"Refreshing storage i18n for {len(LOCALES)} locales:")
    for loc in LOCALES:
        apply_to(loc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
