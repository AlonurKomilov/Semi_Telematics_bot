/**
 * The KPI section switcher — one control, every section page renders it
 * in the same header slot.  Not-yet-built sections stay visible but
 * disabled: the map of what KPI will grade is part of the product.
 */
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ChevronDown } from 'lucide-react';
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
        disabled: !s.ready || s.key === current,
        onSelect: () => navigate(s.path),
      }))}
    >
      <Button variant="outline" size="sm">
        {KPI_SECTIONS.find((s) => s.key === current)?.label ?? current}
        <ChevronDown size={14} className="ml-1.5 text-muted-foreground" />
      </Button>
    </ActionMenu>
  );
}
