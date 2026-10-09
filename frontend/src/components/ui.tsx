// Tiny UI kit: grayscale + one accent colour (indigo), system font, no animations.
import type { ReactNode } from "react";

export function Card({ title, actions, children }: { title?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className="rounded-lg border border-neutral-200 bg-white">
      {(title || actions) && (
        <header className="flex items-center justify-between gap-4 border-b border-neutral-200 px-4 py-3">
          <h2 className="text-sm font-semibold text-neutral-900">{title}</h2>
          {actions}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return <p className="py-8 text-center text-sm text-neutral-500">{label}</p>;
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message = error instanceof Error ? error.message : String(error);
  return (
    <div className="rounded-md border border-red-200 bg-red-50 p-4 text-sm text-red-800">
      <p className="font-medium">Something went wrong</p>
      <p className="mt-1">{message}</p>
      {onRetry && (
        <button onClick={onRetry} className="mt-3 rounded border border-red-300 bg-white px-3 py-1 text-red-800 hover:bg-red-100">
          Try again
        </button>
      )}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="rounded-md border border-dashed border-neutral-300 p-6 text-center text-sm text-neutral-500">{children}</p>;
}

const SEVERITY: Record<string, string> = {
  critical: "bg-red-600 text-white",
  high: "bg-red-100 text-red-800 border border-red-200",
  medium: "bg-amber-100 text-amber-900 border border-amber-200",
  low: "bg-neutral-100 text-neutral-700 border border-neutral-200",
};

export function SeverityBadge({ severity }: { severity?: string | null }) {
  if (!severity) return <span className="rounded px-2 py-0.5 text-xs text-neutral-500 border border-neutral-200">none</span>;
  return <span className={`rounded px-2 py-0.5 text-xs font-medium uppercase tracking-wide ${SEVERITY[severity] ?? SEVERITY.low}`}>{severity}</span>;
}

const DECISION: Record<string, string> = {
  SHORTLISTED: "bg-indigo-600 text-white",
  APPROVED_FOR_INTERVIEW: "bg-indigo-600 text-white",
  NEEDS_REVIEW: "bg-amber-100 text-amber-900 border border-amber-200",
  IN_REVIEW: "bg-amber-100 text-amber-900 border border-amber-200",
  FLAGGED: "bg-red-600 text-white",
  NOT_SHORTLISTED: "bg-neutral-200 text-neutral-700",
  REJECTED: "bg-neutral-800 text-white",
};

export function DecisionBadge({ value }: { value?: string | null }) {
  if (!value) return <span className="text-xs text-neutral-400">—</span>;
  return <span className={`whitespace-nowrap rounded px-2 py-0.5 text-xs font-medium ${DECISION[value] ?? "bg-neutral-100"}`}>{value.replaceAll("_", " ")}</span>;
}

export function StatusBadge({ status }: { status: string }) {
  const busy = status === "PENDING" || status === "PROCESSING";
  const tone = busy ? "text-indigo-700" : status === "DONE" ? "text-neutral-700" : "text-red-700";
  return <span className={`text-xs font-medium ${tone}`}>{busy ? `${status.toLowerCase()}…` : status}</span>;
}

export function Bar({ label, value, hint }: { label: string; value: number; hint?: string }) {
  const pct = Math.max(0, Math.min(100, value));
  return (
    <div>
      <div className="flex justify-between text-xs text-neutral-600">
        <span>{label}</span>
        <span className="tabular-nums">{value.toFixed(1)}{hint ? ` · ${hint}` : ""}</span>
      </div>
      <div className="mt-1 h-2 rounded bg-neutral-100">
        <div className="h-2 rounded bg-indigo-600" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export function Chip({ children, tone = "neutral", title }: { children: ReactNode; tone?: "neutral" | "accent" | "muted"; title?: string }) {
  const tones = {
    neutral: "border-neutral-300 bg-white text-neutral-800",
    accent: "border-indigo-200 bg-indigo-50 text-indigo-800",
    muted: "border-neutral-200 bg-neutral-50 text-neutral-500 line-through decoration-neutral-300",
  };
  return (
    <span title={title} className={`inline-block rounded-full border px-2.5 py-0.5 text-xs ${tones[tone]}`}>
      {children}
    </span>
  );
}

export function score(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : v.toFixed(1);
}
