/** The console's button.
 *
 *  Before this existed, five pages each declared their own `btnCls` —
 *  four identical, one quietly wider — and eight more filled-button
 *  class strings were written inline.  Nothing was WRONG in any one of
 *  them; what was missing was a way to say "this one matters more than
 *  that one", because every button on a page came out the same weight.
 *  An operator looking at the Plans page could not tell the action that
 *  charges customers from the one that re-reads a checklist.
 *
 *  So weight is the variant's whole job:
 *
 *    primary    the one thing this surface is for — filled, one per region
 *    secondary  a real action, not the main one — outlined
 *    warn       money or a wide blast radius; the operator should slow down
 *    danger     destructive
 *    ghost      navigation and dismissals — carries no weight at all
 *
 *  Colour comes from the four tokens in tailwind.config.js (accent,
 *  warn, danger, ok), never from a raw palette class, so a token change
 *  reaches every button at once.
 */

import type { ButtonHTMLAttributes, ReactNode } from 'react';

export type ButtonVariant = 'primary' | 'secondary' | 'warn' | 'danger' | 'ghost';
export type ButtonSize = 'sm' | 'md';

const BASE =
  'inline-flex items-center justify-center gap-1.5 rounded font-medium transition ' +
  'disabled:opacity-50 disabled:cursor-not-allowed ' +
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60';

const VARIANT: Record<ButtonVariant, string> = {
  primary:   'bg-accent text-white hover:bg-accent/90 border border-transparent',
  secondary: 'border border-slate-700 text-slate-300 hover:bg-slate-800',
  warn:      'border border-warn/40 text-warn hover:bg-warn/10',
  danger:    'border border-danger/40 text-danger hover:bg-danger/10',
  ghost:     'border border-transparent text-slate-400 hover:text-slate-200 hover:bg-slate-800/60',
};

//: Two steps, matching the console's two text sizes.  `sm` is the
//: density the tables use; `md` is for standalone actions.
const SIZE: Record<ButtonSize, string> = {
  sm: 'px-2.5 py-1 text-xs',
  md: 'px-3 py-1.5 text-sm',
};

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  children: ReactNode;
}

export function Button({
  variant = 'secondary', size = 'sm', className = '', children, ...rest
}: Props) {
  return (
    <button className={`${BASE} ${VARIANT[variant]} ${SIZE[size]} ${className}`} {...rest}>
      {children}
    </button>
  );
}
