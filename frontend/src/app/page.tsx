"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { FileText, Gauge, Grid3X3, Quote, UploadCloud } from "lucide-react";
import { api } from "@/lib/api";
import { Card } from "@/components/ui";

const FEATURES = [
  { icon: FileText, title: "27 ICDR sections", desc: "Automatic chapter mapping per SEBI's disclosure framework" },
  { icon: Gauge, title: "10-dimension scoring", desc: "Every point traceable to a rule, a number and a page" },
  { icon: Grid3X3, title: "Risk heatmap", desc: "13 risk classes, quantified severities, boilerplate detection" },
  { icon: Quote, title: "Cited evidence", desc: "Each conclusion carries the source page in the PDF" },
];

export default function Home() {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [demoId, setDemoId] = useState<string | null>(null);

  useEffect(() => {
    api.listAnalyses()
      .then((rows) => setDemoId(rows.find((r) => r.is_demo && r.status === "completed")?.id ?? null))
      .catch(() => setDemoId(null));
  }, []);

  const submit = useCallback(async (file: File) => {
    setError(null);
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      setError("Please upload a PDF (RHP or DRHP).");
      return;
    }
    setBusy(true);
    try {
      const { analysis_id } = await api.upload(file);
      router.push(`/analysis/${analysis_id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed. Is the backend running on port 8000?");
      setBusy(false);
    }
  }, [router]);

  return (
    <div className="mx-auto max-w-3xl">
      <div className="py-10 text-center">
        <h1 className="text-3xl font-bold tracking-tight text-ink">
          Decode any Red Herring Prospectus in minutes.
        </h1>
        <p className="mt-3 text-ink-2">
          Extraction · Valuation vs peers · Risk analysis · Explainable scoring — every conclusion cited to its page.
        </p>
      </div>

      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          const f = e.dataTransfer.files?.[0];
          if (f) void submit(f);
        }}
        onClick={() => inputRef.current?.click()}
        role="button"
        aria-label="Upload RHP PDF"
        className={`cursor-pointer rounded-xl border-2 border-dashed p-12 text-center transition-colors
          ${dragging ? "border-accent bg-accent/5" : "border-baseline hover:border-accent/60"}`}
      >
        <UploadCloud className="mx-auto mb-3 h-9 w-9 text-accent" />
        <div className="font-medium text-ink">
          {busy ? "Uploading…" : "Drop an RHP / DRHP PDF here"}
        </div>
        <div className="mt-1 text-sm text-ink-3">or click to browse · up to 300 MB</div>
        <input ref={inputRef} type="file" accept="application/pdf" className="hidden"
          onChange={(e) => { const f = e.target.files?.[0]; if (f) void submit(f); }} />
      </div>
      {error ? <p className="mt-3 text-center text-sm text-critical">{error}</p> : null}

      {demoId ? (
        <p className="mt-4 text-center text-sm">
          <a href={`/analysis/${demoId}`} className="font-medium text-accent hover:underline">
            View the sample report →
          </a>
          <span className="ml-1 text-ink-3">(synthetic prospectus, fictional company)</span>
        </p>
      ) : null}

      <div className="mt-10 grid grid-cols-2 gap-3 md:grid-cols-4">
        {FEATURES.map((f) => (
          <Card key={f.title} className="text-center">
            <f.icon className="mx-auto mb-2 h-5 w-5 text-accent" aria-hidden />
            <div className="text-sm font-semibold text-ink">{f.title}</div>
            <div className="mt-1 text-xs leading-relaxed text-ink-3">{f.desc}</div>
          </Card>
        ))}
      </div>
    </div>
  );
}
