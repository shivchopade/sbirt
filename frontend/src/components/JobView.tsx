"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import Dropzone from "@/components/Dropzone";
import { Card, Chip, DecisionBadge, Empty, ErrorState, Loading, SeverityBadge, StatusBadge, score } from "@/components/ui";
import { api, type CandidateList, type JobDetail, type UploadResponse } from "@/lib/api";

const STATUSES = ["", "PENDING", "PROCESSING", "DONE", "FAILED", "UNPARSEABLE"] as const;

export default function JobView() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [job, setJob] = useState<JobDetail | null>(null);
  const [jobError, setJobError] = useState<unknown>(null);
  const [table, setTable] = useState<CandidateList | null>(null);
  const [tableError, setTableError] = useState<unknown>(null);
  const [sort, setSort] = useState("score");
  const [status, setStatus] = useState("");
  const [upload, setUpload] = useState<UploadResponse | null>(null);
  const [jobKey, setJobKey] = useState(0);
  const [tableKey, setTableKey] = useState(0);

  useEffect(() => {
    let alive = true;
    api
      .getJob(id)
      .then((j) => {
        if (!alive) return;
        setJob(j);
        setJobError(null);
      })
      .catch((err) => alive && setJobError(err));
    return () => {
      alive = false;
    };
  }, [id, jobKey]);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const fetchTable = () => {
      api
        .listCandidates(id, sort, status || undefined)
        .then((data) => {
          if (!alive) return;
          setTable(data);
          setTableError(null);
          if (data.processing > 0) timer = setTimeout(fetchTable, 2000); // poll while anything is processing
        })
        .catch((err) => alive && setTableError(err));
    };
    fetchTable();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [id, sort, status, tableKey]);

  const loadJob = useCallback(() => setJobKey((k) => k + 1), []);
  const loadTable = useCallback(() => setTableKey((k) => k + 1), []);

  if (jobError) return <ErrorState error={jobError} onRetry={loadJob} />;
  if (!job) return <Loading />;

  const must = job.requirements.filter((r) => r.weight >= 1);
  const nice = job.requirements.filter((r) => r.weight < 1);

  return (
    <div className="space-y-6">
      <div>
        <Link href="/" className="text-xs text-neutral-500 hover:text-neutral-800">
          ← All jobs
        </Link>
        <h1 className="mt-1 text-xl font-semibold text-neutral-900">{job.title}</h1>
        <p className="text-xs text-neutral-500">
          {job.resume_count} resumes · {job.requirement_count} requirements · {job.skill_count} skills
        </p>
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        <Card title="Requirements extracted from the JD">
          <ul className="space-y-1.5 text-sm">
            {must.map((r) => (
              <li key={r.index} className="flex gap-2">
                <span className="mt-0.5 shrink-0 rounded bg-neutral-900 px-1.5 text-[10px] font-medium text-white">1.0</span>
                <span>{r.text}</span>
              </li>
            ))}
            {nice.map((r) => (
              <li key={r.index} className="flex gap-2 text-neutral-600">
                <span className="mt-0.5 shrink-0 rounded border border-neutral-300 px-1.5 text-[10px] font-medium">0.5</span>
                <span>{r.text}</span>
              </li>
            ))}
          </ul>
        </Card>
        <div className="space-y-6">
          <Card title="Skills">
            <div className="flex flex-wrap gap-1.5">
              {job.required_skills.map((s) => (
                <Chip key={s.name} tone={s.weight >= 1 ? "accent" : "neutral"} title={`weight ${s.weight}`}>
                  {s.display}
                </Chip>
              ))}
            </div>
            <p className="mt-3 text-xs text-neutral-500">Filled = must have (1.0) · outlined = nice to have (0.5). Repeating a skill in a resume adds nothing.</p>
          </Card>
          <Card title="Upload resumes">
            <Dropzone
              onUpload={async (files) => {
                const res = await api.uploadResumes(id, files);
                setUpload(res);
                loadTable();
                loadJob();
              }}
            />
            {upload && (
              <div className="mt-3 text-xs text-neutral-700">
                <p>
                  {upload.accepted} accepted, {upload.rejected} rejected.
                </p>
                <ul className="mt-1 space-y-0.5">
                  {upload.resumes
                    .filter((r) => r.error)
                    .map((r) => (
                      <li key={r.resume_id} className="text-red-700">
                        {r.file_name}: {r.error}
                      </li>
                    ))}
                </ul>
              </div>
            )}
          </Card>
        </div>
      </div>

      <Card
        title={`Candidates${table ? ` (${table.total})` : ""}`}
        actions={
          <div className="flex items-center gap-2 text-xs">
            {table && table.processing > 0 && <span className="text-indigo-700">{table.processing} processing…</span>}
            <label>
              Status{" "}
              <select value={status} onChange={(e) => setStatus(e.target.value)} className="rounded border border-neutral-300 px-1.5 py-1">
                {STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {s || "all"}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Sort{" "}
              <select value={sort} onChange={(e) => setSort(e.target.value)} className="rounded border border-neutral-300 px-1.5 py-1">
                <option value="score">clean score</option>
                <option value="risk">risk</option>
                <option value="name">name</option>
                <option value="created">upload time</option>
              </select>
            </label>
          </div>
        }
      >
        {tableError ? (
          <ErrorState error={tableError} onRetry={loadTable} />
        ) : table === null ? (
          <Loading />
        ) : table.items.length === 0 ? (
          <Empty>No resumes {status ? `with status ${status}` : "yet"}. Drop PDF or DOCX files above.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-neutral-200 text-left text-xs uppercase tracking-wide text-neutral-500">
                  <th className="py-2 pr-3 font-medium">Name</th>
                  <th className="py-2 pr-3 text-right font-medium">Clean score</th>
                  <th className="py-2 pr-3 font-medium">Risk</th>
                  <th className="py-2 pr-3 font-medium">Status</th>
                  <th className="py-2 pr-3 text-right font-medium"># Flags</th>
                  <th className="py-2 font-medium">Decision</th>
                </tr>
              </thead>
              <tbody>
                {table.items.map((c) => (
                  <tr
                    key={c.resume_id}
                    onClick={() => router.push(`/candidates/${c.resume_id}`)}
                    className="cursor-pointer border-b border-neutral-100 hover:bg-neutral-50"
                  >
                    <td className="py-2 pr-3">
                      <p className="font-medium text-neutral-900">{c.candidate_name ?? "—"}</p>
                      <p className="text-xs text-neutral-500">{c.file_name}</p>
                    </td>
                    <td className="py-2 pr-3 text-right tabular-nums">
                      <span className="text-base font-semibold">{score(c.clean_score)}</span>
                      {c.gaming_delta ? <span className="block text-xs text-red-700">raw {score(c.raw_score)}</span> : null}
                    </td>
                    <td className="py-2 pr-3">
                      <SeverityBadge severity={c.risk_level} />
                      {c.risk_score ? <span className="ml-1 text-xs tabular-nums text-neutral-500">{c.risk_score}</span> : null}
                    </td>
                    <td className="py-2 pr-3">
                      <StatusBadge status={c.status} />
                      {c.status_reason && <p className="max-w-56 truncate text-xs text-neutral-500" title={c.status_reason}>{c.status_reason}</p>}
                    </td>
                    <td className="py-2 pr-3 text-right tabular-nums" title={c.flag_codes.join(", ")}>
                      {c.flag_count}
                    </td>
                    <td className="py-2">
                      <DecisionBadge value={c.final_decision} />
                      {c.human_decision && <p className="text-[10px] text-neutral-500">human override</p>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
