import { cn } from "@/lib/cn";

export function LogoMark({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "relative inline-flex h-10 w-10 shrink-0 items-center justify-center overflow-hidden rounded-2xl bg-gradient-to-br from-brand via-brand to-brand-deep text-accent shadow-[0_10px_22px_-12px_rgba(79,70,229,0.9)]",
        className,
      )}
      aria-hidden="true"
    >
      <svg viewBox="0 0 24 24" className="h-[62%] w-[62%]" fill="none">
        <path
          d="M12 2.4l2.05 7.55L21.6 12l-7.55 2.05L12 21.6l-2.05-7.55L2.4 12l7.55-2.05L12 2.4Z"
          fill="currentColor"
        />
        <path
          d="M18.6 3.4l.62 2.28 2.28.62-2.28.62-.62 2.28-.62-2.28-2.28-.62 2.28-.62.62-2.28Z"
          fill="white"
          fillOpacity="0.92"
        />
      </svg>
    </span>
  );
}

export function Logo({
  tone = "dark",
  tagline = false,
  className,
  markClassName,
  taglineClassName,
}: {
  tone?: "dark" | "light";
  tagline?: boolean;
  className?: string;
  markClassName?: string;
  taglineClassName?: string;
}) {
  return (
    <span className={cn("inline-flex items-center gap-2.5", className)}>
      <LogoMark className={markClassName} />
      <span className="flex flex-col gap-1 leading-none">
        <span
          className={cn(
            "text-[1.3rem] font-extrabold tracking-[-0.03em]",
            tone === "dark" ? "text-ink" : "text-white",
          )}
        >
          Nova
          <span className={tone === "dark" ? "text-brand" : "text-accent"}>Cash</span>
        </span>
        {tagline ? (
          <span
            className={cn(
              "text-[10px] font-semibold uppercase tracking-[0.18em]",
              tone === "dark" ? "text-muted" : "text-white/60",
              taglineClassName,
            )}
          >
            Instant loans · Uganda
          </span>
        ) : null}
      </span>
    </span>
  );
}
