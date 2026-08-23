/**
 * The run's one-line note — the owner's own record on a settlement
 * ("226 in shop Thu–Fri"), writable even after finalizing: a note is
 * not money.
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { StickyNote } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '../../../../components/ui/button';
import { Input } from '../../../../components/ui/input';
import { setIncentiveRunNote, type RunDetail } from '../../api';

// ── Run note — one line, the owner's own record ("226 in shop Thu–Fri").
export function RunNoteLine({ run, onSaved }: { run: RunDetail; onSaved: () => void }) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(run.note ?? '');
  const save = async () => {
    try {
      await setIncentiveRunNote(run.id, text);
      setEditing(false);
      onSaved();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not save note');
    }
  };
  if (editing) {
    return (
      <div className="flex items-center gap-2">
        <Input value={text} autoFocus className="h-7 max-w-md text-sm"
          placeholder={t('kpi_runs.note_ph', 'Week of 8/3 — 226 in shop Thu–Fri…')}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') void save(); if (e.key === 'Escape') setEditing(false); }} />
        <Button size="sm" variant="outline" onClick={save}>{t('common.save', 'Save')}</Button>
      </div>
    );
  }
  return (
    <button type="button" onClick={() => { setText(run.note ?? ''); setEditing(true); }}
      className="inline-flex items-center gap-1.5 py-1 -my-1 text-xs text-muted-foreground hover:text-foreground transition min-h-tap">
      <StickyNote className="size-3" />
      {run.note
        ? <span className="italic">{run.note}</span>
        : t('kpi_runs.note_add', 'Add a note…')}
    </button>
  );
}
