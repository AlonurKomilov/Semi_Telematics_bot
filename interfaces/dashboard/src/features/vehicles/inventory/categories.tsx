/**
 * Inventory category metadata — icon + label per category, one place.
 * Mirrors INVENTORY_CATEGORIES in the backend mixin; the API also ships
 * the category list, so an unknown value still renders (Package icon).
 */
import type { LucideIcon } from 'lucide-react';
import {
  Camera, CreditCard, RadioTower, HardDrive, Tablet, Package,
} from 'lucide-react';

export const CATEGORY_META: Record<string, { label: string; Icon: LucideIcon }> = {
  camera:           { label: 'Camera',          Icon: Camera },
  fuel_card:        { label: 'Fuel Card',       Icon: CreditCard },
  toll_transponder: { label: 'Toll / PrePass',  Icon: RadioTower },
  eld:              { label: 'ELD',             Icon: HardDrive },
  tablet:           { label: 'Tablet',          Icon: Tablet },
  other:            { label: 'Other',           Icon: Package },
};

export function categoryMeta(category: string) {
  return CATEGORY_META[category] ?? { label: category, Icon: Package };
}

export const STATUS_LABELS: Record<string, string> = {
  installed: 'installed',
  needs_check: 'needs check',
  damaged: 'damaged',
  missing: 'missing',
  in_repair: 'in repair',
  spare: 'spare',
};
