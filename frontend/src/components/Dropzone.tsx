"use client";

import { useRef, useState } from "react";

const ACCEPT = ".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document";

export default function Dropzone({ onUpload }: { onUpload: (files: File[]) => Promise<void> }) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function send(list: FileList | null) {
    const files = Array.from(list ?? []);
    if (!files.length) return;
    setBusy(true);
    setError(null);
    try {
      await onUpload(files);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }

  return (
    <div>
      <div
        role="button"
        tabIndex={0}
        onClick={() => input.current?.click()}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          void send(e.dataTransfer.files);
        }}
        className={`cursor-pointer rounded-md border-2 border-dashed p-6 text-center text-sm ${
          over ? "border-indigo-600 bg-indigo-50 text-indigo-800" : "border-neutral-300 text-neutral-600 hover:border-neutral-400"
        }`}
      >
        {busy ? "Uploading…" : "Drop PDF / DOCX resumes here, or click to choose files"}
        <p className="mt-1 text-xs text-neutral-500">Max 5 MB and 10 pages each. Files are checked by content, not by extension.</p>
      </div>
      <input ref={input} type="file" multiple accept={ACCEPT} className="hidden" onChange={(e) => void send(e.target.files)} />
      {error && <p className="mt-2 text-xs text-red-700">{error}</p>}
    </div>
  );
}
