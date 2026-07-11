"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { Card, CardTitle, PageChip } from "@/components/ui";

interface QAItem {
  question: string;
  answer?: string | null;
  citations?: { section: string; pages: (number | null)[] }[];
  error?: string;
  pending?: boolean;
}

const SUGGESTIONS = [
  "What are the company's biggest customer concentration risks?",
  "How will the IPO proceeds be used?",
  "What litigation is pending against the promoters?",
  "How has working capital evolved over the last three years?",
];

export default function QATab({ analysisId }: { analysisId: string }) {
  const [question, setQuestion] = useState("");
  const [items, setItems] = useState<QAItem[]>([]);
  const [busy, setBusy] = useState(false);

  const ask = async (q: string) => {
    const trimmed = q.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    setQuestion("");
    setItems((prev) => [{ question: trimmed, pending: true }, ...prev]);
    try {
      const res = await api.ask(analysisId, trimmed);
      setItems((prev) => prev.map((it, i) => i === 0
        ? { question: trimmed, answer: res.answer, citations: res.citations, error: res.error }
        : it));
    } catch (e) {
      setItems((prev) => prev.map((it, i) => i === 0
        ? { question: trimmed, error: e instanceof Error ? e.message : "Request failed" }
        : it));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <Card>
        <CardTitle>Ask the document</CardTitle>
        <p className="mb-3 text-xs leading-relaxed text-ink-3">
          Answers are generated only from the prospectus text and carry page citations. Requires an
          Anthropic API key on the backend. This is document lookup, not investment advice.
        </p>
        <form
          onSubmit={(e) => { e.preventDefault(); void ask(question); }}
          className="flex gap-2">
          <input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="e.g. Why is the company raising fresh capital?"
            className="min-w-0 flex-1 rounded border border-edge bg-page px-3 py-2 text-sm text-ink placeholder:text-ink-3"
          />
          <button type="submit" disabled={busy || !question.trim()}
            className="shrink-0 rounded bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50">
            {busy ? "Thinking…" : "Ask"}
          </button>
        </form>
        {!items.length ? (
          <div className="mt-3 flex flex-wrap gap-2">
            {SUGGESTIONS.map((sg) => (
              <button key={sg} onClick={() => void ask(sg)} disabled={busy}
                className="rounded-full border border-edge px-3 py-1 text-xs text-ink-2 hover:border-accent hover:text-ink disabled:opacity-50">
                {sg}
              </button>
            ))}
          </div>
        ) : null}
      </Card>

      {items.map((it, i) => (
        <Card key={items.length - i}>
          <div className="mb-2 text-sm font-semibold text-ink">{it.question}</div>
          {it.pending ? (
            <p className="text-sm text-ink-3">Searching the relevant chapters…</p>
          ) : it.error ? (
            <p className="text-sm text-critical">{it.error}</p>
          ) : (
            <>
              <p className="whitespace-pre-line text-sm leading-relaxed text-ink-2">
                {it.answer ?? "No answer could be grounded in the document."}
              </p>
              {it.citations?.length ? (
                <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[11px] text-ink-3">
                  Sources:
                  {it.citations.map((c, j) => (
                    <span key={j} className="inline-flex items-center">
                      {c.section.replace(/_/g, " ")}
                      <PageChip pages={c.pages} />
                    </span>
                  ))}
                </div>
              ) : null}
            </>
          )}
        </Card>
      ))}
    </div>
  );
}
