/**
 * The small parenthetical that trails a value in the run sheet —
 * "(incl. $250 TONU)", "(2 inactive)", an override's reason.
 */
import { Tip } from '../../../../components/tooltip';

/** In-cell free-text annotation (extras note, inactive reason, override
 *  reason): truncated so a long note can never inflate its column for
 *  every row; the full text lives on hover. */
export function Note({ text }: { text: string }) {
  return (
    <Tip label={text}>
      <span className="ml-1 inline-block max-w-40 truncate align-bottom text-xs text-muted-foreground">
        {text}
      </span>
    </Tip>
  );
}
