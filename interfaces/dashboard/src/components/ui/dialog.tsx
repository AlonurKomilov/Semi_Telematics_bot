"use client"

import * as React from "react"
import { Dialog as DialogPrimitive } from "@base-ui/react/dialog"

import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { XIcon } from "lucide-react"
import { sizeRegion } from '@/lib/sizeRegion';

function Dialog({ ...props }: DialogPrimitive.Root.Props) {
  return <DialogPrimitive.Root data-slot="dialog" {...props} />
}

function DialogTrigger({ ...props }: DialogPrimitive.Trigger.Props) {
  return <DialogPrimitive.Trigger data-slot="dialog-trigger" {...props} />
}

function DialogPortal({ ...props }: DialogPrimitive.Portal.Props) {
  return <DialogPrimitive.Portal data-slot="dialog-portal" {...props} />
}

function DialogClose({ ...props }: DialogPrimitive.Close.Props) {
  return <DialogPrimitive.Close data-slot="dialog-close" {...props} />
}

function DialogOverlay({
  className,
  ...props
}: DialogPrimitive.Backdrop.Props) {
  return (
    <DialogPrimitive.Backdrop
      data-slot="dialog-overlay"
      className={cn(
        // THIS is the sanctioned backdrop the ban points callers at;
        // Dialog's own overlay cannot be written in terms of itself.
        // eslint-disable-next-line no-restricted-syntax
        "fixed inset-0 isolate z-50 bg-black/10 duration-100 supports-[backdrop-filter]:backdrop-blur-sm data-[open]:animate-in data-[open]:fade-in-0 data-[closed]:animate-out data-[closed]:fade-out-0",
        className
      )}
      {...props}
    />
  )
}

/**
 * Dialog widths, as a prop — mirroring SHEET_SIZE next door.
 *
 * The base carries `sm:max-w-sm`, and `tailwind-merge` cannot replace a
 * `sm:`-prefixed class with an unprefixed one: they are different
 * variants, so BOTH survive and the prefixed one wins from 640px up. So
 * `<DialogContent size="lg">` — the obvious way to write it,
 * and what 31 of 43 call sites do — silently renders at 384px on every
 * desktop. It has been that way for a long time; it only became visible
 * when a dialog whose content genuinely needed 460px started clipping
 * its own labels off the left edge.
 *
 * A prop cannot be written the not-taking way.
 */
const DIALOG_SIZE: Record<
  "sm" | "md" | "lg" | "xl" | "2xl" | "3xl" | "4xl" | "5xl",
  string
> = {
  sm: "sm:max-w-sm",
  md: "sm:max-w-md",
  lg: "sm:max-w-lg",
  xl: "sm:max-w-xl",
  "2xl": "sm:max-w-2xl",
  "3xl": "sm:max-w-3xl",
  // A video needs the room; design.md §7's dialog ladder stops at
  // 2xl for FORMS, which is a different thing from a player.
  "4xl": "sm:max-w-4xl",
  "5xl": "sm:max-w-5xl",
}

function DialogContent({
  className,
  children,
  showCloseButton = true,
  size = "sm",
  ...props
}: DialogPrimitive.Popup.Props & {
  showCloseButton?: boolean
  size?: keyof typeof DIALOG_SIZE
}) {
  return (
    <DialogPortal>
      <DialogOverlay />
      <DialogPrimitive.Popup
        data-slot="dialog-content"
        // Overlays are their own Size REGION: a dialog is a separate
        // surface from the page behind it, and it is the one place a
        // reader may want roomier than the dense screen it opened from.
        // Caller style wins — this only supplies a default.
        style={{ ...sizeRegion('overlays'), ...props.style }}
        className={cn(
          // A viewport-centred fixed box: without a height cap, content
          // taller than the screen overflows BOTH edges and the footer
          // buttons become unreachable (nothing scrolls to them).  Cap at
          // the viewport minus the 1rem inset on each side and let the
          // dialog scroll itself.  ``dvh`` so mobile browser chrome
          // collapsing doesn't clip it.
          //
          // ``overscroll-contain``: a wheel that reaches the end of a
          // scrolling dialog otherwise CHAINS to the page behind it, so
          // the background creeps while a modal is open — which reads as
          // the app coming apart.  It was absent from the entire codebase
          // (0 occurrences) until components/scrolling; this is the one
          // place that fixes it for all 26 dialog call sites at once.
          //
          // The REST of the scroll-region contract deliberately does NOT
          // apply here.  ``tabIndex``/``role="region"`` would fight Base
          // UI: this popup is already a focus-managed ``role="dialog"``,
          // and a region landmark nested in a dialog is noise, not
          // navigation.  A primitive takes the parts of a contract that
          // fit it, not all of them.
          "fixed top-1/2 left-1/2 z-50 grid w-full max-w-[calc(100%-2rem)] max-h-[calc(100dvh-2rem)] overflow-y-auto overscroll-contain -translate-x-1/2 -translate-y-1/2 gap-4 rounded-xl bg-popover p-4 text-sm text-popover-foreground ring-1 ring-foreground/10 duration-100 outline-none data-[open]:animate-in data-[open]:fade-in-0 data-[open]:zoom-in-95 data-[closed]:animate-out data-[closed]:fade-out-0 data-[closed]:zoom-out-95",
          DIALOG_SIZE[size],
          className
        )}
        {...props}
      >
        {children}
        {showCloseButton && (
          <DialogPrimitive.Close
            data-slot="dialog-close"
            render={
              <Button
                variant="ghost"
                className="absolute top-2 right-2"
                size="icon-sm"
              />
            }
          >
            <XIcon
            />
            <span className="sr-only">Close</span>
          </DialogPrimitive.Close>
        )}
      </DialogPrimitive.Popup>
    </DialogPortal>
  )
}

function DialogHeader({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="dialog-header"
      className={cn("flex flex-col gap-2", className)}
      {...props}
    />
  )
}

function DialogFooter({
  className,
  showCloseButton = false,
  justify = "end",
  children,
  ...props
}: React.ComponentProps<"div"> & {
  showCloseButton?: boolean
  /**
   * Where the actions sit. `end` is the default and what almost every
   * dialog wants. `between` exists because two call sites needed a
   * destructive action opposite the confirm pair and got there by
   * passing `className="flex items-center justify-between gap-2"` —
   * overriding the footer's own layout from outside, which is how a
   * primitive's value leaks back to the call site. The need was real;
   * the variant was missing.
   */
  justify?: "end" | "between"
}) {
  return (
    <div
      data-slot="dialog-footer"
      className={cn(
        "-mx-4 -mb-4 flex flex-col-reverse gap-2 rounded-b-xl border-t bg-muted/50 p-4 sm:flex-row",
        justify === "between"
          ? "sm:items-center sm:justify-between"
          : "sm:justify-end",
        className
      )}
      {...props}
    >
      {children}
      {showCloseButton && (
        <DialogPrimitive.Close render={<Button variant="outline" />}>
          Close
        </DialogPrimitive.Close>
      )}
    </div>
  )
}

function DialogTitle({ className, ...props }: DialogPrimitive.Title.Props) {
  return (
    <DialogPrimitive.Title
      data-slot="dialog-title"
      className={cn(
        "font-heading text-base leading-none font-semibold",
        className
      )}
      {...props}
    />
  )
}

function DialogDescription({
  className,
  ...props
}: DialogPrimitive.Description.Props) {
  return (
    <DialogPrimitive.Description
      data-slot="dialog-description"
      className={cn(
        "text-sm text-muted-foreground [&>a]:underline [&>a]:underline-offset-2 [&>a]:hover:text-foreground",
        className
      )}
      {...props}
    />
  )
}

export {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogOverlay,
  DialogPortal,
  DialogTitle,
  DialogTrigger,
}
