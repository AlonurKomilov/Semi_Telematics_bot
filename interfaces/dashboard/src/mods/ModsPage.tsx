/**
 * The Mods page — every customization the app offers, in one place.
 *
 * The top-bar popover is deliberately NOT this page in miniature. It
 * carries the three things a person changes often (which mod is
 * installed, the colour it wears, and the global size) and a door to
 * here; everything that is a *setting* rather than a *toggle* lives on
 * this page, which has the width design.md §7 refuses to invent for a
 * popover.
 *
 * Both surfaces render the SAME `ModControls`, so a chip row cannot
 * drift between them — the only difference is the `compact` flag and
 * which branch of it renders.
 *
 * Size is the one axis split across the two: the popover holds the
 * global slider, and this page hands size WHOLE to `SizeCard` (global,
 * per region, cross-device). The page therefore does not repeat the
 * global slider — one object, one face.
 */
import { Palette } from 'lucide-react';
import PageHeader from '../components/shell/PageHeader';
import { Card } from '../components/ui/card';
import { SectionHeader } from '../components/shell';
import { ModControls } from './ModPanel';
import SizeCard from './SizeCard';

export default function ModsPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        icon={Palette}
        title="Mods"
        description="How the app looks, moves and sounds. A mod installs a whole look at once; every axis below is also yours to set on its own."
      />

      {/* max-w-2xl, not the full page: these are chip rows and short
          labels. Stretched across a wide viewport the eye loses which
          label belongs to which row, and the section rules turn into
          full-bleed lines that read as page furniture rather than as
          the boundary between two questions. */}
      <Card render={<section />} className="max-w-2xl">
        <SectionHeader description="Colour, corners, material, motion and sound.">
          Appearance
        </SectionHeader>
        {/* The popover supplies this rhythm from its own `space-y-3`;
            on a page the wrapper has to, or the sections collide. */}
        <div className="space-y-3 mt-3">
          <ModControls />
        </div>
      </Card>

      <div className="max-w-2xl">
        <SizeCard />
      </div>
    </div>
  );
}
