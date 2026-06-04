/**
 * Page header — title + description + LastUpdated chip.
 *
 * Persona-agnostic by contract — receives the resolved ``headerConfig``
 * (title + description) via sectionProps.  The page wrapper
 * (``Overview.tsx``) computes the config from
 * ``personaConfig.resolveHeaderConfig(persona)`` and hands the
 * resolved object down.  Sections like this never look at persona
 * directly; that decision happens once at the page level.
 *
 * Enforced by ``check-role-drift.mjs`` — adding ``useShellConfig``
 * here would fail the drift check.
 */
import { Truck } from 'lucide-react';
import { PageHeader, LastUpdated } from '../../../components/shell';
import type { OverviewSectionProps } from './_shared/types';

export default function OverviewHeader({
  headerConfig,
  fetchedAt,
  isFetching,
  refetch,
}: OverviewSectionProps) {
  return (
    <PageHeader
      icon={Truck}
      title={headerConfig.title}
      description={headerConfig.description}
      actions={
        <LastUpdated
          fetchedAt={fetchedAt}
          isFetching={isFetching}
          onRefresh={refetch}
        />
      }
    />
  );
}
