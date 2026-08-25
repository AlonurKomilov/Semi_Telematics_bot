import { mergeProps } from "@base-ui/react/merge-props"
import { useRender } from "@base-ui/react/use-render"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"
import { toneClasses, type Tone } from "@/lib/status"

const badgeVariants = cva(
  "group/badge inline-flex h-5 w-fit shrink-0 items-center justify-center gap-1 overflow-hidden rounded-md border border-transparent px-2 py-0.5 text-xs font-medium whitespace-nowrap transition-all focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 has-data-[icon=inline-end]:pr-1.5 has-data-[icon=inline-start]:pl-1.5 aria-invalid:border-destructive aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 [&>svg]:pointer-events-none [&>svg]:size-3!",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground [a]:hover:bg-primary/80",
        secondary:
          "bg-secondary text-secondary-foreground [a]:hover:bg-secondary/80",
        destructive:
          "bg-destructive/10 text-destructive focus-visible:ring-destructive/20 dark:bg-destructive/20 dark:focus-visible:ring-destructive/40 [a]:hover:bg-destructive/20",
        outline:
          "border-border text-foreground [a]:hover:bg-muted [a]:hover:text-muted-foreground",
        ghost:
          "hover:bg-muted hover:text-muted-foreground dark:hover:bg-muted/50",
        link: "text-primary underline-offset-4 hover:underline",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
)

/**
 * `tone` is the door that was missing.
 *
 * This primitive's variants are BRAND colours — default, secondary,
 * destructive, ghost. None of them means ok / warn / info, and
 * `StatusBadge` (the other door) takes a domain STATUS string and
 * derives the tone itself. So a surface that already KNEW its tone had
 * no way in, and 91 of them hand-rolled the geometry instead:
 * `rounded-md border px-2 py-0.5 text-2xs font-medium ${toneClasses(t)}`
 * typed out at the call site, against a primitive whose base is exactly
 * that. `<Badge>` was used once in the whole app.
 *
 * With a tone set it wins over `variant` — the two answer the same
 * question and a call site passing both means the tone.
 */
/**
 * Written out, not built as `text-${tone}`. Tailwind's scanner reads
 * source text: a class assembled at runtime is invisible to it, and
 * only exists in the bundle while some OTHER file happens to use the
 * same literal. That is the same hazard design.md §5.1 names for
 * arbitrary values — it works until the unrelated usage goes away.
 */
const SUBTLE_TONE: Record<Tone, string> = {
  ok: "text-ok border-ok-bd",
  warn: "text-warn border-warn-bd",
  danger: "text-danger border-danger-bd",
  info: "text-info border-info-bd",
  neutral: "text-muted-foreground border-border",
}

function Badge({
  className,
  variant = "default",
  tone,
  subtle = false,
  render,
  ...props
}: useRender.ComponentProps<"span"> & VariantProps<typeof badgeVariants> & {
  tone?: Tone
  /**
   * Same hue, no fill — a second step INSIDE one tone.
   *
   * Fault severity runs ok → caution → warning → critical: four steps
   * against a tone layer that has three. `caution` and `warning` both
   * resolved to `warn`, so a browser audit found them rendering as one
   * chip, and a dispatcher triaging a fault list could not tell the
   * lesser amber from the greater. Weight carries the step that hue
   * cannot: outline reads as the lighter of the two in either theme,
   * where two neighbouring ambers do not.
   */
  subtle?: boolean
}) {
  return useRender({
    defaultTagName: "span",
    props: mergeProps<"span">(
      {
        className: cn(
          badgeVariants({ variant: tone ? "outline" : variant }),
          tone && (subtle ? SUBTLE_TONE[tone] : toneClasses(tone)),
          className,
        ),
      },
      props
    ),
    render,
    state: {
      slot: "badge",
      variant,
      tone,
      subtle,
    },
  })
}

export { Badge, badgeVariants }
