# Samsara brand assets

`logo.svg` — the Samsara wordmark, `viewBox="0 0 596 97"`, five paths, every
fill `currentColor`. Taken from the wordmark Samsara serves inline on
samsara.com, with the framework's build attributes and utility classes
removed and `role="img" aria-label="Samsara"` added.

**`currentColor` is why this file is worth having**: the mark takes the colour
of the text around it, so one file covers the light theme, the dark theme and
any accent the Colour picker lands on — no second variant to keep in step.

It is a WORDMARK: it draws the brand's NAME. A surface that shows it does not
also print "Samsara" beside it. A square symbol (Samsara's owl) would be the
opposite — that sits beside the name. `interfaces/dashboard/src/features/vehicles/providerLogos.tsx`
carries that distinction as `kind`.

Rendered by INLINING the svg, never `<img src>`: an image renders in its own
document, where `currentColor` resolves to black.

This is Samsara's trademark, used to identify the integration that supplies a
vehicle's data — nominative use, the same as every other product that names
its integrations. Samsara publishes no brand page (`/company/brand` and
`/press` are both 404); if a usage kit is ever obtained, this file and that
`kind` are the two things to check against it.
