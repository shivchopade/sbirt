"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { Card, Empty, ErrorState, Loading } from "@/components/ui";
import { api, type JobSummary } from "@/lib/api";

export default function JobsPage() {
  const [jobs, setJobs] = useState<JobSummary[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let alive = true;
    api
      .listJobs()
      .then((list) => {
        if (!alive) return;
        setJobs(list);
        setError(null);
      })
      .catch((err) => alive && setError(err));
    return () => {
      alive = false;
    };
  }, [reloadKey]);
  const load = useCallback(() => setReloadKey((k) => k + 1), []);

  return (
    <div className="grid gap-6 md:grid-cols-[1fr_380px]">
      <Card title="Jobs">
        {error ? (
          <ErrorState error={error} onRetry={load} />
        ) : jobs === null ? (
          <Loading />
        ) : jobs.length === 0 ? (
          <Empty>No jobs yet. Create one with the form — paste a job description to get started.</Empty>
        ) : (
          <ul className="divide-y divide-neutral-200">
            {jobs.map((job) => (
              <li key={job.id}>
                <Link href={`/jobs/${job.id}`} className="flex items-center justify-between gap-4 py-3 hover:bg-neutral-50">
                  <div>
                    <p className="font-medium text-neutral-900">{job.title}</p>
                    <p className="text-xs text-neutral-500">
                      {job.requirement_count} requirements · {job.skill_count} skills · created {new Date(job.created_at).toLocaleString()}
                    </p>
                  </div>
                  <div className="text-right text-xs text-neutral-600">
                    <p className="tabular-nums">{job.resume_count} resumes</p>
                    {job.processing_count > 0 && <p className="text-indigo-700">{job.processing_count} processing…</p>}
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Card>
      <NewJobForm />
    </div>
  );
}

function NewJobForm() {
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [jd, setJd] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const job = await api.createJob(title, jd);
      router.push(`/jobs/${job.id}`);
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  }

  return (
    <Card title="New job">
      <form onSubmit={submit} className="space-y-3">
        <label className="block text-sm">
          <span className="text-neutral-700">Title</span>
          <input
            required
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Backend Engineer (Python)"
            className="mt-1 w-full rounded border border-neutral-300 px-3 py-2 text-sm focus:border-indigo-600 focus:outline-none"
          />
        </label>
        <label className="block text-sm">
          <span className="text-neutral-700">Job description</span>
          <textarea
            required
            minLength={30}
            value={jd}
            onChange={(e) => setJd(e.target.value)}
            rows={14}
            placeholder={"Responsibilities\n- Build REST APIs with Python and FastAPI\n\nRequirements\n- 2+ years of backend experience\n\nNice to have\n- Kubernetes"}
            className="mt-1 w-full rounded border border-neutral-300 px-3 py-2 font-mono text-xs focus:border-indigo-600 focus:outline-none"
          />
        </label>
        <p className="text-xs text-neutral-500">
          Headings like “Requirements”, “Responsibilities” (weight 1.0) and “Nice to have” (0.5) are detected automatically; “About us” and
          benefits text is ignored.
        </p>
        {error ? <ErrorState error={error} /> : null}
        <button
          disabled={busy}
          className="w-full rounded bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
        >
          {busy ? "Parsing job description…" : "Create job"}
        </button>
      </form>
    </Card>
  );
}
