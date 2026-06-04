"""Idempotently add Phase 6 storage-dashboard i18n keys to 8 locales.

Adds the ``storage.health.*`` and ``storage.files.*`` namespaces.
Idempotent — existing keys are left alone.
"""
import json
import sys
from collections import OrderedDict
from pathlib import Path

LOCALES = ["es", "fr", "ru", "uk", "pa", "am", "uz", "so"]

DASH = {
    "es": {
        "storage.health.loading": "Cargando estado…",
        "storage.health.local_cache": "Caché local utilizado",
        "storage.health.disk_backend_note": "Esta cuenta usa almacenamiento local. Cambia a híbrido para activar sincronización con Drive y gestión de cuota.",
        "storage.health.synced": "Sincronizado con Drive",
        "storage.health.pending": "Pendiente",
        "storage.health.stuck": "Bloqueado — requiere atención",
        "storage.health.drive_connected": "Google Drive conectado como {{email}}",
        "storage.health.drive_disconnected": "Google Drive no conectado — los archivos quedan en disco local",
        "storage.health.stuck_hint": "Pulsa Reintentar en las filas bloqueadas",
        "storage.files.title": "Archivos",
        "storage.files.filter_all": "Todos",
        "storage.files.filter_pending": "Pendientes",
        "storage.files.filter_stuck": "Bloqueados",
        "storage.files.col_file": "Archivo",
        "storage.files.col_source": "Origen",
        "storage.files.col_size": "Tamaño",
        "storage.files.col_status": "Estado",
        "storage.files.col_added": "Añadido",
        "storage.files.retry": "Reintentar",
        "storage.files.retry_queued": "Reintento en cola",
        "storage.files.retry_failed": "No se pudo reintentar",
        "storage.files.retry_all_stuck": "Reintentar todos los bloqueados ({{count}})",
        "storage.files.bulk_retry_done": "Se han re-encolado {{count}} archivo(s)",
        "storage.files.empty": "Nada que sincronizar — todo al día.",
        "storage.files.empty_stuck": "Sin archivos bloqueados.",
    },
    "fr": {
        "storage.health.loading": "Chargement de l'état…",
        "storage.health.local_cache": "Cache local utilisé",
        "storage.health.disk_backend_note": "Ce compte utilise le stockage local. Passez en hybride pour activer la synchronisation Drive et la gestion du quota.",
        "storage.health.synced": "Synchronisé sur Drive",
        "storage.health.pending": "En attente",
        "storage.health.stuck": "Bloqué — action requise",
        "storage.health.drive_connected": "Google Drive connecté en tant que {{email}}",
        "storage.health.drive_disconnected": "Google Drive non connecté — les fichiers restent en local",
        "storage.health.stuck_hint": "Cliquez sur Réessayer sur les lignes bloquées",
        "storage.files.title": "Fichiers",
        "storage.files.filter_all": "Tous",
        "storage.files.filter_pending": "En attente",
        "storage.files.filter_stuck": "Bloqués",
        "storage.files.col_file": "Fichier",
        "storage.files.col_source": "Source",
        "storage.files.col_size": "Taille",
        "storage.files.col_status": "État",
        "storage.files.col_added": "Ajouté",
        "storage.files.retry": "Réessayer",
        "storage.files.retry_queued": "Réessai en file",
        "storage.files.retry_failed": "Échec du réessai",
        "storage.files.retry_all_stuck": "Réessayer tous les bloqués ({{count}})",
        "storage.files.bulk_retry_done": "{{count}} fichier(s) re-mis en file",
        "storage.files.empty": "Rien à synchroniser — tout est à jour.",
        "storage.files.empty_stuck": "Aucun fichier bloqué.",
    },
    "ru": {
        "storage.health.loading": "Загрузка состояния…",
        "storage.health.local_cache": "Локальный кеш",
        "storage.health.disk_backend_note": "Аккаунт использует локальное хранилище. Переключите на гибрид для синхронизации с Drive и контроля квоты.",
        "storage.health.synced": "Синхронизировано с Drive",
        "storage.health.pending": "Ожидает",
        "storage.health.stuck": "Заблокировано — требуется внимание",
        "storage.health.drive_connected": "Google Drive подключён ({{email}})",
        "storage.health.drive_disconnected": "Google Drive не подключён — файлы остаются на диске",
        "storage.health.stuck_hint": "Нажмите Повторить для заблокированных строк",
        "storage.files.title": "Файлы",
        "storage.files.filter_all": "Все",
        "storage.files.filter_pending": "Ожидают",
        "storage.files.filter_stuck": "Заблокированы",
        "storage.files.col_file": "Файл",
        "storage.files.col_source": "Источник",
        "storage.files.col_size": "Размер",
        "storage.files.col_status": "Состояние",
        "storage.files.col_added": "Добавлен",
        "storage.files.retry": "Повторить",
        "storage.files.retry_queued": "Повтор поставлен в очередь",
        "storage.files.retry_failed": "Не удалось повторить",
        "storage.files.retry_all_stuck": "Повторить все заблокированные ({{count}})",
        "storage.files.bulk_retry_done": "Поставлено в очередь {{count}} файл(ов)",
        "storage.files.empty": "Очередь пуста — всё синхронизировано.",
        "storage.files.empty_stuck": "Заблокированных файлов нет.",
    },
    "uk": {
        "storage.health.loading": "Завантаження стану…",
        "storage.health.local_cache": "Локальний кеш",
        "storage.health.disk_backend_note": "Акаунт використовує локальне сховище. Перейдіть на гібрид для синхронізації з Drive і керування квотою.",
        "storage.health.synced": "Синхронізовано з Drive",
        "storage.health.pending": "Очікує",
        "storage.health.stuck": "Заблоковано — потрібна увага",
        "storage.health.drive_connected": "Google Drive підключено ({{email}})",
        "storage.health.drive_disconnected": "Google Drive не підключено — файли залишаються на диску",
        "storage.health.stuck_hint": "Натисніть Повторити для заблокованих рядків",
        "storage.files.title": "Файли",
        "storage.files.filter_all": "Усі",
        "storage.files.filter_pending": "Очікують",
        "storage.files.filter_stuck": "Заблоковані",
        "storage.files.col_file": "Файл",
        "storage.files.col_source": "Джерело",
        "storage.files.col_size": "Розмір",
        "storage.files.col_status": "Стан",
        "storage.files.col_added": "Додано",
        "storage.files.retry": "Повторити",
        "storage.files.retry_queued": "Повтор у черзі",
        "storage.files.retry_failed": "Не вдалося повторити",
        "storage.files.retry_all_stuck": "Повторити всі заблоковані ({{count}})",
        "storage.files.bulk_retry_done": "Поставлено в чергу {{count}} файл(ів)",
        "storage.files.empty": "Черга порожня — все синхронізовано.",
        "storage.files.empty_stuck": "Немає заблокованих файлів.",
    },
    "pa": {
        "storage.health.loading": "ਸਥਿਤੀ ਲੋਡ ਹੋ ਰਹੀ ਹੈ…",
        "storage.health.local_cache": "ਸਥਾਨਕ ਕੈਸ਼ ਵਰਤਿਆ",
        "storage.health.disk_backend_note": "ਇਹ ਖਾਤਾ ਸਥਾਨਕ ਡਿਸਕ ਵਰਤ ਰਿਹਾ ਹੈ। Drive ਸਿੰਕ ਅਤੇ ਕੋਟਾ ਪ੍ਰਬੰਧਨ ਲਈ ਹਾਈਬ੍ਰਿਡ 'ਤੇ ਜਾਓ।",
        "storage.health.synced": "Drive ਨਾਲ ਸਿੰਕ ਹੋਇਆ",
        "storage.health.pending": "ਬਾਕੀ",
        "storage.health.stuck": "ਬਲਾਕ — ਧਿਆਨ ਦੀ ਲੋੜ",
        "storage.health.drive_connected": "Google Drive ਕਨੈਕਟ ਹੈ ({{email}})",
        "storage.health.drive_disconnected": "Google Drive ਕਨੈਕਟ ਨਹੀਂ — ਫਾਈਲਾਂ ਸਥਾਨਕ ਡਿਸਕ 'ਤੇ ਰਹਿਣਗੀਆਂ",
        "storage.health.stuck_hint": "ਬਲਾਕ ਕਤਾਰਾਂ ਲਈ ਮੁੜ-ਕੋਸ਼ਿਸ਼ ਕਲਿੱਕ ਕਰੋ",
        "storage.files.title": "ਫਾਈਲਾਂ",
        "storage.files.filter_all": "ਸਾਰੇ",
        "storage.files.filter_pending": "ਬਾਕੀ",
        "storage.files.filter_stuck": "ਬਲਾਕ",
        "storage.files.col_file": "ਫਾਈਲ",
        "storage.files.col_source": "ਸਰੋਤ",
        "storage.files.col_size": "ਆਕਾਰ",
        "storage.files.col_status": "ਸਥਿਤੀ",
        "storage.files.col_added": "ਜੋੜਿਆ",
        "storage.files.retry": "ਮੁੜ-ਕੋਸ਼ਿਸ਼",
        "storage.files.retry_queued": "ਮੁੜ-ਕੋਸ਼ਿਸ਼ ਕਤਾਰ ਵਿੱਚ",
        "storage.files.retry_failed": "ਮੁੜ-ਕੋਸ਼ਿਸ਼ ਅਸਫਲ",
        "storage.files.retry_all_stuck": "ਸਾਰੇ ਬਲਾਕ ਮੁੜ-ਕੋਸ਼ਿਸ਼ ਕਰੋ ({{count}})",
        "storage.files.bulk_retry_done": "{{count}} ਫਾਈਲ(ਾਂ) ਨੂੰ ਮੁੜ-ਕਤਾਰ ਕੀਤਾ",
        "storage.files.empty": "ਸਿੰਕ ਕਰਨ ਲਈ ਕੁਝ ਨਹੀਂ — ਸਭ ਅੱਪ-ਟੂ-ਡੇਟ।",
        "storage.files.empty_stuck": "ਕੋਈ ਬਲਾਕ ਫਾਈਲ ਨਹੀਂ।",
    },
    "am": {
        "storage.health.loading": "ሁኔታ በመጫን ላይ…",
        "storage.health.local_cache": "የተጠቀመ የአካባቢ ካሽ",
        "storage.health.disk_backend_note": "ይህ መለያ የአካባቢ ዲስክን ይጠቀማል። ለ Drive ሲንክ እና የኮታ አስተዳደር ወደ ድብልቅ ይቀይሩ።",
        "storage.health.synced": "ከ Drive ጋር ሲንክ ሆኗል",
        "storage.health.pending": "በመጠባበቅ ላይ",
        "storage.health.stuck": "ተዘግቷል — ትኩረት ይፈልጋል",
        "storage.health.drive_connected": "Google Drive ተገናኝቷል ({{email}})",
        "storage.health.drive_disconnected": "Google Drive አልተገናኘም — ፋይሎች በአካባቢ ዲስክ ላይ ይቆያሉ",
        "storage.health.stuck_hint": "ለተዘጉ ረድፎች እንደገና ይሞክሩ የሚለውን ይጫኑ",
        "storage.files.title": "ፋይሎች",
        "storage.files.filter_all": "ሁሉም",
        "storage.files.filter_pending": "በመጠባበቅ ላይ",
        "storage.files.filter_stuck": "ተዘጉ",
        "storage.files.col_file": "ፋይል",
        "storage.files.col_source": "ምንጭ",
        "storage.files.col_size": "መጠን",
        "storage.files.col_status": "ሁኔታ",
        "storage.files.col_added": "ተጨምሯል",
        "storage.files.retry": "እንደገና ሞክር",
        "storage.files.retry_queued": "እንደገና ሙከራ በወረፋ",
        "storage.files.retry_failed": "እንደገና ሙከራ አልተሳካም",
        "storage.files.retry_all_stuck": "ሁሉንም ተዘጉ እንደገና ሞክር ({{count}})",
        "storage.files.bulk_retry_done": "{{count}} ፋይል(ሎች) እንደገና ተወረፉ",
        "storage.files.empty": "ለማመሳሰል የለም — ሁሉም ወቅታዊ ነው።",
        "storage.files.empty_stuck": "ምንም ተዘጉ ፋይሎች የሉም።",
    },
    "uz": {
        "storage.health.loading": "Holat yuklanmoqda…",
        "storage.health.local_cache": "Mahalliy kesh ishlatilgan",
        "storage.health.disk_backend_note": "Bu hisob mahalliy diskni ishlatadi. Drive sinxronizatsiyasi va kvota boshqaruvi uchun gibridga o'ting.",
        "storage.health.synced": "Drive bilan sinxronlangan",
        "storage.health.pending": "Kutmoqda",
        "storage.health.stuck": "Blokirovkalangan — e'tibor talab qilinadi",
        "storage.health.drive_connected": "Google Drive ulangan ({{email}})",
        "storage.health.drive_disconnected": "Google Drive ulanmagan — fayllar mahalliy diskda qoladi",
        "storage.health.stuck_hint": "Blokirovkalangan qatorlar uchun Qayta urinish ni bosing",
        "storage.files.title": "Fayllar",
        "storage.files.filter_all": "Hammasi",
        "storage.files.filter_pending": "Kutilmoqda",
        "storage.files.filter_stuck": "Blokirovkalangan",
        "storage.files.col_file": "Fayl",
        "storage.files.col_source": "Manba",
        "storage.files.col_size": "Hajmi",
        "storage.files.col_status": "Holat",
        "storage.files.col_added": "Qo'shilgan",
        "storage.files.retry": "Qayta urinish",
        "storage.files.retry_queued": "Qayta urinish navbatga qo'yildi",
        "storage.files.retry_failed": "Qayta urinish muvaffaqiyatsiz",
        "storage.files.retry_all_stuck": "Barcha blokirovkalanganlarni qayta urinish ({{count}})",
        "storage.files.bulk_retry_done": "{{count}} ta fayl qayta navbatga qo'yildi",
        "storage.files.empty": "Sinxronlanadigan fayl yo'q — hammasi joyida.",
        "storage.files.empty_stuck": "Blokirovkalangan fayllar yo'q.",
    },
    "so": {
        "storage.health.loading": "Xaaladda waa la rarayaa…",
        "storage.health.local_cache": "Cache-ka maxalliga la isticmaalo",
        "storage.health.disk_backend_note": "Akoonkan wuxuu isticmaalaa kayd maxalli ah. Ku beddel hybrid si aad ugu shaqayso sync-ka Drive iyo maamulka quotada.",
        "storage.health.synced": "La sinkronyay Drive",
        "storage.health.pending": "Sugaya",
        "storage.health.stuck": "Joojiyey — feejignaan u baahan",
        "storage.health.drive_connected": "Google Drive waa la xirayey ({{email}})",
        "storage.health.drive_disconnected": "Google Drive lama xirin — faylasha way ku haray diska maxalliga",
        "storage.health.stuck_hint": "Riix Isku-day mar kale safafka la joojiyey",
        "storage.files.title": "Faylasha",
        "storage.files.filter_all": "Dhammaan",
        "storage.files.filter_pending": "Sugaya",
        "storage.files.filter_stuck": "Joojiyey",
        "storage.files.col_file": "Fayl",
        "storage.files.col_source": "Isha",
        "storage.files.col_size": "Cabbirka",
        "storage.files.col_status": "Xaalad",
        "storage.files.col_added": "La daray",
        "storage.files.retry": "Isku-day mar kale",
        "storage.files.retry_queued": "Isku-daygu safka ayuu galay",
        "storage.files.retry_failed": "Isku-daygu wuu fashilmay",
        "storage.files.retry_all_stuck": "Isku day dhammaan kuwa la joojiyey ({{count}})",
        "storage.files.bulk_retry_done": "{{count}} fayl ayaa dib loogu daray safka",
        "storage.files.empty": "Wax sync ah ma jiraan — dhammaan way wada socdaan.",
        "storage.files.empty_stuck": "Faylal la joojiyey ma jiraan.",
    },
}


def _set_nested(d: dict, dotted: str, value) -> None:
    parts = dotted.split(".")
    cur = d
    for p in parts[:-1]:
        if p not in cur or not isinstance(cur[p], dict):
            cur[p] = OrderedDict()
        cur = cur[p]
    cur[parts[-1]] = value


def _patch(path: Path, translations: dict) -> int:
    data = json.loads(path.read_text(), object_pairs_hook=OrderedDict)
    added = 0
    for key, value in translations.items():
        parts = key.split(".")
        cur = data
        exists = True
        for p in parts:
            if not isinstance(cur, dict) or p not in cur:
                exists = False
                break
            cur = cur[p]
        if exists:
            continue
        _set_nested(data, key, value)
        added += 1
    if added:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return added


def main(repo: Path) -> None:
    dash = repo / "interfaces/dashboard/src/locales"
    for loc in LOCALES:
        p = dash / f"{loc}.json"
        if p.exists() and loc in DASH:
            print(f"dashboard {loc}: +{_patch(p, DASH[loc])} keys")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "."))
