import * as React from "react"
import { Input as InputPrimitive } from "@base-ui/react/input"

import { cn } from "@/lib/utils"

/**
 * ``min-h-tap`` sits beside ``h-8`` deliberately, and is not redundant.
 * Every length here is ``calc(step × var(--size-axis))``, and the Size
 * control's floor is 0.85 on two multiplying axes — so ``h-8`` (32px)
 * reaches 23.1px at the smallest setting a user can choose, under the
 * 24px WCAG 2.5.8 floor. ``min-h-tap`` is the one step that does NOT
 * scale, which is the whole reason design.md §5.1 requires it on every
 * interactive control. It was absent from all 66 Input call sites,
 * because it was absent from here.
 */
function Input({ className, type, ...props }: React.ComponentProps<"input">) {
  return (
    <InputPrimitive
      type={type}
      data-slot="input"
      className={cn(
        "h-8 min-h-tap w-full min-w-0 rounded-lg border border-input bg-transparent px-2.5 py-1 text-base text-foreground transition-colors outline-none file:inline-flex file:h-6 file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-foreground placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:pointer-events-none disabled:cursor-not-allowed disabled:bg-input/50 disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 md:text-sm dark:bg-input/30 dark:disabled:bg-input/80 dark:aria-invalid:border-destructive/50 dark:aria-invalid:ring-destructive/40",
        className
      )}
      {...props}
    />
  )
}

export { Input }
