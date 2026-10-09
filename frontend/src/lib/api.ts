// Typed API client. Types are generated from contracts/openapi.json (npm run gen:api) — never edit them by hand.
import type { components } from "./api-types";

type Schemas = components["schemas"];
export type JobSummary = Schemas["JobSummary"];
export type JobDetail = Schemas["JobDetail"];
export type UploadResponse = Schemas["UploadResponse"];
export type CandidateList = Schemas["CandidateList"];
export type CandidateRow = Schemas["CandidateRow"];
export type ResumeReport = Schemas["ResumeReport"];
export type FlagOut = Schemas["FlagOut"];
export type DecisionOut = Schemas["DecisionOut"];
export type HumanDecision = Schemas["HumanDecision"];
export type ResumeStatus = Schemas["ResumeStatus"];
export type Severity = Schemas["Severity"];
export type ShortlistResponse = Schemas["ShortlistResponse"];

export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
    readonly details: Record<string, unknown> = {},
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { cache: "no-store", ...init });
  } catch {
    throw new ApiError(`Cannot reach the API at ${API_URL}. Is the backend running?`, 0, "NETWORK_ERROR");
  }
  if (!res.ok) {
    let body: Schemas["ErrorResponse"] | undefined;
    try {
      body = (await res.json()) as Schemas["ErrorResponse"];
    } catch {
      /* not JSON */
    }
    throw new ApiError(body?.error.message ?? res.statusText, res.status, body?.error.code ?? "HTTP_ERROR", body?.error.details);
  }
  return (await res.json()) as T;
}

const json = (body: unknown): RequestInit => ({
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  listJobs: () => request<JobSummary[]>("/api/v1/jobs"),
  getJob: (id: string) => request<JobDetail>(`/api/v1/jobs/${id}`),
  createJob: (title: string, jd_text: string) =>
    request<JobDetail>("/api/v1/jobs", { method: "POST", ...json({ title, jd_text }) }),
  uploadResumes: (jobId: string, files: File[]) => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f, f.name));
    return request<UploadResponse>(`/api/v1/jobs/${jobId}/resumes`, { method: "POST", body: form });
  },
  listCandidates: (jobId: string, sort: string, status?: string) => {
    const q = new URLSearchParams({ sort });
    if (status) q.set("status", status);
    return request<CandidateList>(`/api/v1/jobs/${jobId}/candidates?${q}`);
  },
  shortlist: (jobId: string) => request<ShortlistResponse>(`/api/v1/jobs/${jobId}/shortlist`),
  getReport: (id: string) => request<ResumeReport>(`/api/v1/resumes/${id}`),
  setDecision: (id: string, decision: HumanDecision, note: string) =>
    request<DecisionOut>(`/api/v1/resumes/${id}/decision`, { method: "PATCH", ...json({ decision, note: note || null }) }),
  rescore: (id: string) => request<Schemas["RescoreResponse"]>(`/api/v1/resumes/${id}/rescore`, { method: "POST" }),
  previewUrl: (path: string) => `${API_URL}${path}`,
};
