import { mergeProps } from "@base-ui/react/merge-props"
import { useRender } from "@base-ui/react/use-render"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

/**
 * Card — the bordered surface a page's content sits on.
 *
 * There was no primitive for this and 223 hand-rolled copies, which is
 * how the app ended up with two card radii and five paddings. The values
 * here are not a new opinion: design.md §6 already says `rounded-lg` is
 * the card default and §5 says card padding is `p-3`/`p-4`. Everything
 * outside that was drift.
 *
 * The radius is deliberately NOT a variant. `rounded-xl` appears on 93
 * card sites, but it is the SHELL FRAME's radius — `<main>` in AppShell
 * — so a card wearing it repeats the curvature of the box it sits
 * inside. And the split is nobody's hierarchy: of 92 files holding
 * cards, 83 use one radius exclusively and never the other, so whichever
 * a file's first author reached for, the rest of the file followed. A
 * primitive offering both would relocate that coin-flip, not settle it.
 *
 * `padding="none"` is for a card whose children own their own edges — a
 * DataGrid, a divided list, anything that must bleed to the border.
 *
 * `cardVariants` is exported for the surfaces that cannot be a <Card>:
 * an element that already exists for another reason (a <label>, a
 * grid item with its own ref) still gets the one definition by calling
 * `cn(cardVariants({ padding: 'none' }), …)`.
 */
const cardVariants = cva("bg-card border border-border rounded-lg", {
  variants: {
    padding: {
      none: "",
      compact: "p-3",
      default: "p-4",
    },
  },
  defaultVariants: { padding: "default" },
})

function Card({
  className,
  padding = "default",
  render,
  ...props
}: useRender.ComponentProps<"div"> & VariantProps<typeof cardVariants>) {
  return useRender({
    defaultTagName: "div",
    props: mergeProps<"div">(
      { className: cn(cardVariants({ padding }), className) },
      props,
    ),
    render,
    state: { slot: "card", padding },
  })
}

export { Card, cardVariants }
