/**
 * The day cell's menu, as DATA — built only when a cell is actually
 * clicked.  Every clickable cell used to mount its own menu component
 * and rebuild this list (~7 translated labels) on every render; at 7
 * days × 69 trucks that was ~480 menus, and a 30-day period would
 * have made it ~2,000.  One shared AnchoredMenu instance now consumes
 * this builder's output for whichever cell opened it.
 */
import type { TFunction } from 'i18next';
import type { MenuAction } from '../../../../components/ui/context-menu';
import type { DaySuggestion, RunRow } from '../../api';
import { dayLabel } from './shared';

const REASONS = ['home time', 'repair', 'holiday'];

export function dayMenuItems({ row, day, suggestion, t, onMark }: {
  row: RunRow;
  day: string;
  /** The day's maintenance suggestion, when one exists. */
  suggestion: DaySuggestion | undefined;
  t: TFunction;
  onMark: (row: RunRow, day: string, reason: string | null) => void;
}): MenuAction[] {
  const reason = (row.inactive_dates ?? [])
    .find((m) => m.date === day)?.reason ?? null;
  return [
    {
      key: 'heading',
      label: `${row.vehicle_unit || t('kpi_board.no_unit', 'No unit assigned')} · ${dayLabel(day)}`,
      disabled: true,
      onSelect: () => {},
    },
    // The consequence AT the moment of choosing — it already lived in
    // the cell's accessible name, invisible to the sighted user with
    // the menu open.
    ...(row.weekly_target != null ? [{
      key: 'consequence',
      label: reason == null
        ? t('kpi_board.menu_target_down', 'target {{a}} → {{b}}', {
            a: `$${Math.round(row.adjusted_target).toLocaleString()}`,
            b: `$${Math.round(Math.max(0, row.adjusted_target - row.weekly_target / 7)).toLocaleString()}`,
          })
        : t('kpi_board.menu_target_up', 'count it again: target {{a}} → {{b}}', {
            a: `$${Math.round(row.adjusted_target).toLocaleString()}`,
            b: `$${Math.round(row.adjusted_target + row.weekly_target / 7).toLocaleString()}`,
          }),
      disabled: true,
      onSelect: () => {},
    }] : []),
    ...(reason == null && suggestion ? [{
      key: 'confirm-suggest',
      label: t('kpi_board.confirm_suggest',
        'Confirm {{reason}} — {{source}}',
        { reason: suggestion.reason, source: suggestion.source }),
      separatorBefore: true,
      onSelect: () => onMark(row, day, suggestion.reason),
    }] : []),
    ...(reason != null ? [{
      key: 'clear',
      label: t('kpi_board.clear', 'Active day (clear mark)'),
      separatorBefore: true,
      onSelect: () => onMark(row, day, null),
    }] : []),
    ...REASONS.map((r, i) => ({
      key: r,
      label: t(`kpi_board.reason_${r.replace(' ', '_')}`,
        r.charAt(0).toUpperCase() + r.slice(1)),
      disabled: reason === r,
      separatorBefore: i === 0 && reason == null,
      onSelect: () => onMark(row, day, r),
    })),
    {
      key: 'cancel',
      label: t('common.cancel', 'Cancel'),
      separatorBefore: true,
      onSelect: () => {},
    },
  ];
}
