import * as React from "react"

import { cn } from "@/lib/utils"

// Auto-grow was intended and is not implemented. The base class string
// carried `field-sizing-content`, which is a Tailwind 4 utility: it
// compiled to nothing here, so all six textareas have always sat at their
// `rows` height and scrolled internally. Removing the dead class changes
// no pixel. Building it for real is a UX decision, not a cleanup — and
// `field-sizing` is Chromium-only, so Firefox and Safari would keep the
// `rows` behaviour and the public carrier-intake form would be a
// different height per browser.
function Textarea({ className, ...props }: React.ComponentProps<"textarea">) {
  return (
    <textarea
      data-slot="textarea"
      className={cn(
        "flex min-h-16 w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-base text-foreground transition-colors outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:bg-input/50 disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 md:text-sm dark:bg-input/30 dark:disabled:bg-input/80 dark:aria-invalid:border-destructive/50 dark:aria-invalid:ring-destructive/40",
        className
      )}
      {...props}
    />
  )
}

export { Textarea }
