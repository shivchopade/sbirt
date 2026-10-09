"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { Bar, Card, Chip, DecisionBadge, ErrorState, Loading, SeverityBadge, StatusBadge, score } from "@/components/ui";
import { api, type FlagOut, type HumanDecision, type ResumeReport } from "@/lib/api";

export default function CandidateView() {
  const { id } = useParams<{ id: string }>();
  const [report, setReport] = useState<ResumeReport | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const fetchReport = () => {
      api
        .getReport(id)
        .then((r) => {
          if (!alive) return;
          setReport(r);
          setError(null);
          if (r.status === "PENDING" || r.status === "PROCESSING") timer = setTimeout(fetchReport, 2000); // poll while busy
        })
        .catch((err) => alive && setError(err));
    };
    fetchReport();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [id, reloadKey]);
  const load = useCallback(() => setReloadKey((k) => k + 1), []);

  if (error) return <ErrorState error={error} onRetry={load} />;
  if (!report) return <Loading />;

  const s = report.scores;
  const busy = report.status === "PENDING" || report.status === "PROCESSING";

  return (
    <div className="space-y-6">
      <div>
        <Link href={`/jobs/${report.job_id}`} className="text-xs text-neutral-500 hover:text-neutral-800">
          ← {report.job_title}
        </Link>
        <div className="mt-1 flex flex-wrap items-center gap-3">
          <h1 className="text-xl font-semibold text-neutral-900">{report.candidate.name ?? report.file_name}</h1>
          <StatusBadge status={report.status} />
          <DecisionBadge value={report.decision.final} />
        </div>
        <p className="text-xs text-neutral-500">
          {[report.candidate.email, report.candidate.phone, report.file_name, report.page_count ? `${report.page_count} page(s)` : null]
            .filter(Boolean)
            .join(" · ")}
        </p>
        {report.status_reason && <p className="mt-1 text-sm text-red-700">{report.status_reason}</p>}
      </div>

      {busy ? (
        <Loading label="Analysing this resume…" />
      ) : (
        <>
          <div className="grid gap-6 md:grid-cols-[300px_1fr]">
            <Card title="Score">
              <p className="text-5xl font-semibold tabular-nums text-neutral-900">{score(s?.clean_score)}</p>
              <p className="text-xs text-neutral-500">clean score — visible text only, JD-copied lines excluded</p>
              {s && s.gaming_delta > 0 && (
                <div className="mt-3 rounded border border-red-200 bg-red-50 p-2 text-xs text-red-800">
                  Raw score <span className="font-semibold tabular-nums">{score(s.raw_score)}</span> · gaming delta{" "}
                  <span className="font-semibold tabular-nums">+{score(s.gaming_delta)}</span>
                  <p className="mt-1">This is what a naive ATS would compute from everything in the file, including hidden or copied text.</p>
                </div>
              )}
              {s && (
                <div className="mt-4 space-y-3">
                  <Bar label="Requirement coverage" value={s.breakdown.requirement_coverage} hint={`w ${s.breakdown.weights.requirement_coverage ?? 0}`} />
                  <Bar label="Skill match" value={s.breakdown.skill_match} hint={`w ${s.breakdown.weights.skill_match ?? 0}`} />
                  <Bar label="Overall similarity" value={s.breakdown.overall_similarity} hint={`w ${s.breakdown.weights.overall_similarity ?? 0}`} />
                </div>
              )}
              <div className="mt-4 flex items-center gap-2 border-t border-neutral-200 pt-3 text-xs text-neutral-600">
                Integrity risk <span className="font-semibold tabular-nums">{report.integrity.risk_score}</span>
                <SeverityBadge severity={report.integrity.risk_level} />
              </div>
              {s && (
                <p className="mt-2 text-[11px] text-neutral-400">
                  {s.breakdown.sbert_model.split("/").pop()} · input {s.breakdown.sbert_input} · {Math.round(s.breakdown.timings_ms.total ?? 0)} ms
                </p>
              )}
            </Card>
            <DecisionPanel report={report} onChange={load} />
          </div>

          <FlagList flags={report.integrity.flags} />

          {s && (
            <Card title="Requirements → best matching resume line">
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-neutral-200 text-left text-xs uppercase tracking-wide text-neutral-500">
                      <th className="py-2 pr-3 font-medium">JD requirement</th>
                      <th className="py-2 pr-3 font-medium">Best resume line</th>
                      <th className="py-2 pr-3 text-right font-medium">Similarity</th>
                      <th className="py-2 text-center font-medium">Met</th>
                    </tr>
                  </thead>
                  <tbody>
                    {s.breakdown.requirements.map((r, i) => (
                      <tr key={i} className="border-b border-neutral-100 align-top">
                        <td className="py-2 pr-3">
                          {r.jd_requirement}
                          {r.weight < 1 && <span className="ml-1 text-xs text-neutral-400">(nice to have)</span>}
                          {r.parts && r.parts.length > 0 && (
                            <ul className="mt-1 space-y-0.5 border-l-2 border-neutral-200 pl-2 text-xs text-neutral-500">
                              {r.parts.map((p, k) => (
                                <li key={k}>
                                  <span className="text-neutral-700">“{p.text}”</span> → {p.best_resume_line ?? "—"}{" "}
                                  <span className="tabular-nums">({p.similarity.toFixed(2)})</span>
                                </li>
                              ))}
                              <li className="text-[10px] uppercase tracking-wide text-neutral-400">
                                compound requirement · scored by {r.method === "clauses" ? "clauses (mean)" : "whole sentence"}
                              </li>
                            </ul>
                          )}
                        </td>
                        <td className="py-2 pr-3 text-neutral-700">
                          {r.best_resume_line ?? "—"}
                          {r.resume_section && <span className="ml-1 text-xs text-neutral-400">[{r.resume_section}]</span>}
                        </td>
                        <td className="py-2 pr-3 text-right tabular-nums">
                          {r.similarity.toFixed(2)}
                          <span className="block text-xs text-neutral-400">{r.calibrated.toFixed(0)}/100</span>
                        </td>
                        <td className={`py-2 text-center font-semibold ${r.met ? "text-indigo-700" : "text-neutral-300"}`}>{r.met ? "✓" : "✗"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          )}

          {s && (
            <Card title="Skills">
              <div className="flex flex-wrap gap-1.5">
                {s.breakdown.skills.details
                  .filter((d) => d.matched)
                  .map((d) => (
                    <Chip key={d.skill} tone="accent" title={`${d.method}: ${d.evidence ?? ""}`}>
                      {d.display}
                      {d.method !== "lexical" && <span className="ml-1 text-[10px] opacity-70">({d.method})</span>}
                    </Chip>
                  ))}
                {s.breakdown.skills.details
                  .filter((d) => !d.matched)
                  .map((d) => (
                    <Chip key={d.skill} tone="muted" title="missing">
                      {d.display}
                    </Chip>
                  ))}
              </div>
              <p className="mt-3 text-xs text-neutral-500">
                {s.breakdown.skills.matched.length} matched · {s.breakdown.skills.missing.length} missing. Hover a chip to see the evidence line. Matching is set-based:
                mentioning a skill 30 times counts once.
              </p>
            </Card>
          )}

          <Previews report={report} />

          {report.hidden_text_excerpt && (
            <Card title="Hidden text found in the file (never scored)">
              <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded bg-red-50 p-3 font-mono text-xs text-red-900">{report.hidden_text_excerpt}</pre>
            </Card>
          )}
        </>
      )}
    </div>
  );
}

function FlagList({ flags }: { flags: FlagOut[] }) {
  return (
    <Card title={`Integrity flags (${flags.length})`}>
      {flags.length === 0 ? (
        <p className="text-sm text-neutral-600">No integrity issues found.</p>
      ) : (
        <ul className="space-y-3">
          {flags.map((f, i) => (
            <li key={i} className="rounded border border-neutral-200 p-3">
              <div className="flex flex-wrap items-center gap-2">
                <SeverityBadge severity={f.severity} />
                <span className="font-mono text-xs font-semibold">{f.code}</span>
                <span className="text-xs text-neutral-400">by {f.detector}</span>
              </div>
              <p className="mt-1 text-sm text-neutral-900">{f.message}</p>
              <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs text-neutral-600">
                <dt className="text-neutral-400">where</dt>
                <dd>
                  {f.evidence.location}
                  {f.evidence.page ? ` · page ${f.evidence.page}` : ""}
                  {f.evidence.bbox ? ` · box [${f.evidence.bbox.map((v) => Math.round(v)).join(", ")}]` : ""}
                  {typeof f.evidence.details?.position === "string" ? ` · ${f.evidence.details.position}` : ""}
                </dd>
                {f.evidence.measured_value !== null && f.evidence.measured_value !== undefined && (
                  <>
                    <dt className="text-neutral-400">measured</dt>
                    <dd className="tabular-nums">
                      {f.evidence.measured_value}
                      {f.evidence.threshold !== null && f.evidence.threshold !== undefined ? ` (threshold ${f.evidence.threshold})` : ""}
                    </dd>
                  </>
                )}
              </dl>
              {f.evidence.snippet && <p className="mt-2 rounded bg-neutral-50 p-2 font-mono text-xs text-neutral-800">{f.evidence.snippet}</p>}
              {Object.keys(f.evidence.details ?? {}).length > 0 && (
                <details className="mt-2 text-xs text-neutral-500">
                  <summary className="cursor-pointer">evidence details</summary>
                  <pre className="mt-1 max-h-60 overflow-auto rounded bg-neutral-50 p-2">{JSON.stringify(f.evidence.details, null, 2)}</pre>
                </details>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function Previews({ report }: { report: ResumeReport }) {
  if (report.previews.length === 0) {
    return (
      <Card title="Page previews">
        <p className="text-sm text-neutral-600">
          {report.file_type === "docx" ? "Previews are rendered for PDF uploads. DOCX evidence names the paragraph or comment instead." : "No preview available."}
        </p>
      </Card>
    );
  }
  return (
    <Card
      title="Page previews"
      actions={
        <span className="flex gap-3 text-xs text-neutral-600">
          <span><span className="mr-1 inline-block h-2.5 w-2.5 border-2 border-red-600 bg-red-100" />hidden text</span>
          <span><span className="mr-1 inline-block h-2.5 w-2.5 border-2 border-orange-600 bg-orange-100" />copied from JD</span>
        </span>
      }
    >
      <div className="grid gap-4 md:grid-cols-2">
        {report.previews.map((p) => (
          // eslint-disable-next-line @next/next/no-img-element -- server-rendered PNG from the API, not a static asset
          <img key={p.page} src={api.previewUrl(p.url)} alt={`Page ${p.page}`} className="w-full rounded border border-neutral-200" />
        ))}
      </div>
    </Card>
  );
}

const ACTIONS: { value: HumanDecision; label: string; style: string }[] = [
  { value: "APPROVED_FOR_INTERVIEW", label: "Approve for interview", style: "bg-indigo-600 text-white hover:bg-indigo-700" },
  { value: "IN_REVIEW", label: "Keep in review", style: "border border-neutral-300 bg-white text-neutral-800 hover:bg-neutral-50" },
  { value: "REJECTED", label: "Reject", style: "border border-neutral-300 bg-white text-neutral-800 hover:bg-neutral-50" },
];

function DecisionPanel({ report, onChange }: { report: ResumeReport; onChange: () => void }) {
  const [note, setNote] = useState(report.decision.reviewer_note ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const d = report.decision;

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      onChange();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Decision">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-neutral-500">System:</span> <DecisionBadge value={d.system} />
        <span className="ml-3 text-neutral-500">Human:</span> <DecisionBadge value={d.human} />
      </div>
      {d.reasons.length > 0 && (
        <ul className="mt-2 list-disc pl-5 text-xs text-neutral-600">
          {d.reasons.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      )}
      <p className="mt-2 text-xs text-neutral-500">Flags never reject automatically — the final call is yours.</p>
      <textarea
        value={note}
        onChange={(e) => setNote(e.target.value)}
        rows={2}
        placeholder="Note for the audit trail (optional)"
        className="mt-3 w-full rounded border border-neutral-300 px-3 py-2 text-sm focus:border-indigo-600 focus:outline-none"
      />
      <div className="mt-2 flex flex-wrap gap-2">
        {ACTIONS.map((a) => (
          <button
            key={a.value}
            disabled={busy}
            onClick={() => run(() => api.setDecision(report.resume_id, a.value, note))}
            className={`rounded px-3 py-1.5 text-sm font-medium disabled:opacity-50 ${a.style}`}
          >
            {a.label}
          </button>
        ))}
        <button
          disabled={busy}
          onClick={() => run(() => api.rescore(report.resume_id))}
          className="ml-auto rounded px-3 py-1.5 text-sm text-neutral-600 underline-offset-2 hover:underline disabled:opacity-50"
          title="Recompute with the current configuration"
        >
          Rescore
        </button>
      </div>
      {error ? <div className="mt-2"><ErrorState error={error} /></div> : null}
      {d.history.length > 0 && (
        <details className="mt-3 text-xs text-neutral-600">
          <summary className="cursor-pointer">History ({d.history.length})</summary>
          <ol className="mt-1 space-y-1">
            {d.history.map((h, i) => (
              <li key={i}>
                <span className="tabular-nums text-neutral-400">{new Date(h.decided_at).toLocaleString()}</span> · {h.actor} ·{" "}
                {(h.human_decision ?? h.system_decision ?? "").replaceAll("_", " ")}
                {h.reviewer_note ? ` — “${h.reviewer_note}”` : ""}
              </li>
            ))}
          </ol>
        </details>
      )}
    </Card>
  );
}
