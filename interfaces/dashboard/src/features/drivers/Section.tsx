/**
 * The caps-label block every panel in this feature is built from.
 *
 * It lives in its own file because two files now render one — the
 * drawer's hand-written integration panels and the generated
 * per-provider ones beside them. Sections that sit next to each other
 * must look like each other; a copied class string is how that stops
 * being true.
 */
export default function Section({
  title, children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section>
      <h3 className="text-2xs font-semibold text-muted-foreground uppercase tracking-wider mb-1.5">
        {title}
      </h3>
      {children}
    </section>
  );
}
