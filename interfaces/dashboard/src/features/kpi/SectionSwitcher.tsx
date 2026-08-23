/**
 * The KPI section switcher — one control, every section page renders it
 * in the same header slot.  Not-yet-built sections stay visible but
 * disabled: the map of what KPI will grade is part of the product.
 */
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Check, ChevronDown } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { ActionMenu } from '../../components/ui/context-menu';
import { KPI_SECTIONS } from './sections';

export function SectionSwitcher({ current }: { current: string }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  return (
    <ActionMenu
      items={KPI_SECTIONS.map((s) => ({
        key: s.key,
        label: s.ready
          ? s.label
          : `${s.label} — ${t('kpi_page.coming', 'not built yet')}`,
        // The ACTIVE item must not dress like the disabled ones — a
        // check names it; selecting it again is a harmless no-op.
        icon: s.key === current
          ? <Check className="text-foreground size-3.5" /> : undefined,
        disabled: !s.ready,
        onSelect: () => { if (s.key !== current) navigate(s.path); },
      }))}
    >
      <Button variant="outline" size="sm">
        {KPI_SECTIONS.find((s) => s.key === current)?.label ?? current}
        <ChevronDown className="ml-1.5 text-muted-foreground" />
      </Button>
    </ActionMenu>
  );
}
