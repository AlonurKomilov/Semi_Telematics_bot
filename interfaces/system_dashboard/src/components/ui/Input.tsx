/** The console's text input — five pages had declared the same class
 *  string, character for character, which is five places to forget when
 *  the focus ring or the placeholder colour changes. */

import type { InputHTMLAttributes } from 'react';

export const INPUT_CLS =
  'bg-slate-950 border border-slate-800 rounded px-2 py-1 text-sm text-slate-200 ' +
  'placeholder:text-slate-600 w-full ' +
  'focus:outline-none focus:border-slate-600 ' +
  'focus-visible:ring-1 focus-visible:ring-accent/50 ' +
  'disabled:opacity-50';

export function Input({ className = '', ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={`${INPUT_CLS} ${className}`} {...rest} />;
}
