"""Add item-type / reference-photo / GPS i18n keys to 8 non-English locales.

Idempotent: existing keys are left alone.  Covers the Phase "1-4"
additions — template item types (check/photo/document), reference
example photos, and inspection geolocation labels.

Usage:  python3 scripts/pti_i18n_item_types.py [repo_root]
"""
import json
import sys
from collections import OrderedDict
from pathlib import Path

LOCALES = ["es", "fr", "ru", "uk", "pa", "am", "uz", "so"]

DASH = {
    "es": {
        "inspections.item_type_field": "Tipo de ítem",
        "inspections.item_type_check": "Verificación (botones OK / defecto)",
        "inspections.item_type_photo": "Foto (toma guiada)",
        "inspections.item_type_document": "Documento (subir PDF o foto)",
        "inspections.ref_image_add": "Referencia",
        "inspections.ref_image_replace": "Clic para reemplazar la foto de referencia",
        "inspections.ref_image_clear": "Quitar foto de referencia",
        "inspections.ref_image_saved": "Foto de referencia guardada",
        "inspections.ref_image_failed": "No se pudo guardar la foto de referencia",
        "inspections.location_vehicle": "desde GPS del vehículo",
        "inspections.location_device": "desde GPS del dispositivo",
    },
    "fr": {
        "inspections.item_type_field": "Type d'élément",
        "inspections.item_type_check": "Vérification (boutons OK / défaut)",
        "inspections.item_type_photo": "Photo (prise guidée)",
        "inspections.item_type_document": "Document (PDF ou photo)",
        "inspections.ref_image_add": "Référence",
        "inspections.ref_image_replace": "Cliquer pour remplacer la photo de référence",
        "inspections.ref_image_clear": "Supprimer la photo de référence",
        "inspections.ref_image_saved": "Photo de référence enregistrée",
        "inspections.ref_image_failed": "Échec de l'enregistrement de la photo de référence",
        "inspections.location_vehicle": "via GPS du véhicule",
        "inspections.location_device": "via GPS de l'appareil",
    },
    "ru": {
        "inspections.item_type_field": "Тип пункта",
        "inspections.item_type_check": "Проверка (кнопки OK / дефект)",
        "inspections.item_type_photo": "Фото (направляемый снимок)",
        "inspections.item_type_document": "Документ (PDF или фото)",
        "inspections.ref_image_add": "Образец",
        "inspections.ref_image_replace": "Нажмите, чтобы заменить образец",
        "inspections.ref_image_clear": "Удалить образец",
        "inspections.ref_image_saved": "Образец сохранён",
        "inspections.ref_image_failed": "Не удалось сохранить образец",
        "inspections.location_vehicle": "по GPS машины",
        "inspections.location_device": "по GPS устройства",
    },
    "uk": {
        "inspections.item_type_field": "Тип пункту",
        "inspections.item_type_check": "Перевірка (кнопки OK / дефект)",
        "inspections.item_type_photo": "Фото (керований знімок)",
        "inspections.item_type_document": "Документ (PDF або фото)",
        "inspections.ref_image_add": "Зразок",
        "inspections.ref_image_replace": "Натисніть, щоб замінити зразок",
        "inspections.ref_image_clear": "Видалити зразок",
        "inspections.ref_image_saved": "Зразок збережено",
        "inspections.ref_image_failed": "Не вдалося зберегти зразок",
        "inspections.location_vehicle": "за GPS машини",
        "inspections.location_device": "за GPS пристрою",
    },
    "pa": {
        "inspections.item_type_field": "ਆਈਟਮ ਕਿਸਮ",
        "inspections.item_type_check": "ਜਾਂਚ (OK / ਨੁਕਸ ਬਟਨ)",
        "inspections.item_type_photo": "ਫੋਟੋ (ਗਾਈਡਡ ਸ਼ਾਟ)",
        "inspections.item_type_document": "ਦਸਤਾਵੇਜ਼ (PDF ਜਾਂ ਫੋਟੋ)",
        "inspections.ref_image_add": "ਹਵਾਲਾ",
        "inspections.ref_image_replace": "ਹਵਾਲਾ ਫੋਟੋ ਬਦਲਣ ਲਈ ਕਲਿੱਕ ਕਰੋ",
        "inspections.ref_image_clear": "ਹਵਾਲਾ ਫੋਟੋ ਹਟਾਓ",
        "inspections.ref_image_saved": "ਹਵਾਲਾ ਫੋਟੋ ਸੰਭਾਲੀ",
        "inspections.ref_image_failed": "ਹਵਾਲਾ ਫੋਟੋ ਸੰਭਾਲੀ ਨਹੀਂ ਜਾ ਸਕੀ",
        "inspections.location_vehicle": "ਵਾਹਨ GPS ਤੋਂ",
        "inspections.location_device": "ਡਿਵਾਈਸ GPS ਤੋਂ",
    },
    "am": {
        "inspections.item_type_field": "የንጥል ዓይነት",
        "inspections.item_type_check": "ማረጋገጫ (OK / ጉድለት አዝራሮች)",
        "inspections.item_type_photo": "ፎቶ (በመመሪያ የተደገፈ)",
        "inspections.item_type_document": "ሰነድ (PDF ወይም ፎቶ)",
        "inspections.ref_image_add": "ማጣቀሻ",
        "inspections.ref_image_replace": "የማጣቀሻ ፎቶ ለመቀየር ይጫኑ",
        "inspections.ref_image_clear": "የማጣቀሻ ፎቶ አስወግድ",
        "inspections.ref_image_saved": "የማጣቀሻ ፎቶ ተቀምጧል",
        "inspections.ref_image_failed": "የማጣቀሻ ፎቶ ማስቀመጥ አልተቻለም",
        "inspections.location_vehicle": "ከተሽከርካሪ GPS",
        "inspections.location_device": "ከመሣሪያ GPS",
    },
    "uz": {
        "inspections.item_type_field": "Band turi",
        "inspections.item_type_check": "Tekshirish (OK / nuqson tugmalari)",
        "inspections.item_type_photo": "Foto (yo'naltirilgan surat)",
        "inspections.item_type_document": "Hujjat (PDF yoki foto)",
        "inspections.ref_image_add": "Namuna",
        "inspections.ref_image_replace": "Namunani almashtirish uchun bosing",
        "inspections.ref_image_clear": "Namunani olib tashlash",
        "inspections.ref_image_saved": "Namuna saqlandi",
        "inspections.ref_image_failed": "Namuna saqlanmadi",
        "inspections.location_vehicle": "transport GPS'idan",
        "inspections.location_device": "qurilma GPS'idan",
    },
    "so": {
        "inspections.item_type_field": "Nooca shayga",
        "inspections.item_type_check": "Hubin (badhamada OK / cillad)",
        "inspections.item_type_photo": "Sawir (sawir la hagaajiyay)",
        "inspections.item_type_document": "Dukumenti (PDF ama sawir)",
        "inspections.ref_image_add": "Tixraac",
        "inspections.ref_image_replace": "Guji si aad u beddesho sawirka tixraaca",
        "inspections.ref_image_clear": "Ka saar sawirka tixraaca",
        "inspections.ref_image_saved": "Sawirka tixraaca waa la kaydiyay",
        "inspections.ref_image_failed": "Sawirka tixraaca lama kaydin",
        "inspections.location_vehicle": "GPS-ka gaariga",
        "inspections.location_device": "GPS-ka qalabka",
    },
}

MINI = {
    "es": {
        "pti.document_required": "Documento requerido (PDF o foto)",
        "pti.reference_label": "Referencia — iguala esta toma",
        "pti.submit_hint_missing_document": "Aún falta documento: {{item}}",
    },
    "fr": {
        "pti.document_required": "Document requis (PDF ou photo)",
        "pti.reference_label": "Référence — reproduisez cette prise",
        "pti.submit_hint_missing_document": "Document manquant : {{item}}",
    },
    "ru": {
        "pti.document_required": "Требуется документ (PDF или фото)",
        "pti.reference_label": "Образец — повторите этот снимок",
        "pti.submit_hint_missing_document": "Нужен документ: {{item}}",
    },
    "uk": {
        "pti.document_required": "Потрібен документ (PDF або фото)",
        "pti.reference_label": "Зразок — повторіть цей знімок",
        "pti.submit_hint_missing_document": "Потрібен документ: {{item}}",
    },
    "pa": {
        "pti.document_required": "ਦਸਤਾਵੇਜ਼ ਲੋੜੀਂਦਾ (PDF ਜਾਂ ਫੋਟੋ)",
        "pti.reference_label": "ਹਵਾਲਾ — ਇਸ ਸ਼ਾਟ ਨਾਲ ਮੇਲ ਕਰੋ",
        "pti.submit_hint_missing_document": "ਅਜੇ ਦਸਤਾਵੇਜ਼ ਚਾਹੀਦਾ: {{item}}",
    },
    "am": {
        "pti.document_required": "ሰነድ ያስፈልጋል (PDF ወይም ፎቶ)",
        "pti.reference_label": "ማጣቀሻ — ይህንን ምስል ያዛምዱ",
        "pti.submit_hint_missing_document": "ሰነድ ያስፈልጋል፦ {{item}}",
    },
    "uz": {
        "pti.document_required": "Hujjat talab qilinadi (PDF yoki foto)",
        "pti.reference_label": "Namuna — shu suratga moslang",
        "pti.submit_hint_missing_document": "Hujjat kerak: {{item}}",
    },
    "so": {
        "pti.document_required": "Dukumenti loo baahan yahay (PDF ama sawir)",
        "pti.reference_label": "Tixraac — la mid noqo sawirkan",
        "pti.submit_hint_missing_document": "Weli dukumenti ayaa loo baahan yahay: {{item}}",
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
    mini = repo / "interfaces/miniapp/src/locales"
    for loc in LOCALES:
        d = dash / f"{loc}.json"
        if d.exists() and loc in DASH:
            print(f"dashboard {loc}: +{_patch(d, DASH[loc])} keys")
        m = mini / f"{loc}.json"
        if m.exists() and loc in MINI:
            print(f"miniapp   {loc}: +{_patch(m, MINI[loc])} keys")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "."))
