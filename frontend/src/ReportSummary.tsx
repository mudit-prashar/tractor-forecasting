import { useEffect, useRef, useState } from "react";

export type SavedSummary = {
  report_id: string;
  model: string;
  model_digest?: string | null;
  prompt_version: string;
  created_at: string;
  elapsed_seconds: number;
  is_outdated: boolean;
  highlights: { section: string; text: string; fact_ids: string[] }[];
  facts: { id: string; text: string }[];
};

export function ReportSummary({ reportId, initialSummary }: {
  reportId: string;
  initialSummary: SavedSummary | null;
}) {
  const [summary, setSummary] = useState(initialSummary);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const activeRequest = useRef<AbortController | null>(null);
  useEffect(() => {
    setSummary(initialSummary);
    setBusy(false);
    setError("");
    return () => activeRequest.current?.abort();
  }, [reportId, initialSummary]);

  async function generate() {
    const controller = new AbortController();
    activeRequest.current = controller;
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`/api/reports/${reportId}/summary`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ regenerate: !!summary }),
        signal: controller.signal,
      });
      const value = await response.json();
      if (!response.ok) throw new Error(value.detail || "Summary generation failed. Try again.");
      if (!controller.signal.aborted) setSummary(value);
    } catch (failure) {
      if (!controller.signal.aborted) setError((failure as Error).message);
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  const usedFacts = summary?.facts.filter((fact) =>
    summary.highlights.some((item) => item.fact_ids.includes(fact.id)),
  ) || [];
  return (
    <section className="panel ai-summary" aria-label="Local AI summary" aria-busy={busy}>
      <div className="panel-heading">
        <div>
          <h2>Report explanation</h2>
          <p>Optional local AI explanation. Forecasts, recommendations, and approvals work without it.</p>
        </div>
        <button className="secondary" disabled={busy} onClick={() => void generate()}>
          {busy ? "Generating summary…" : summary ? "Regenerate summary" : "Generate summary"}
        </button>
      </div>
      {busy && <p role="status">The local model is reading this report. The first request may take a little longer.</p>}
      {error && <p className="error" role="alert">{error}</p>}
      {summary?.is_outdated && <p className="notice">Report decisions or planning context changed after this summary. Regenerate it for an updated explanation.</p>}
      {summary ? (
        <>
          <ul className="summary-highlights">
            {summary.highlights.map((item, index) => (
              <li key={index}>
                {item.text}{" "}
                <a href={`#summary-sources-${reportId}`} aria-label={`View sources for highlight ${index + 1}`}
                  onClick={() => {
                    const details = document.getElementById(`summary-sources-${reportId}`);
                    if (details instanceof HTMLDetailsElement) details.open = true;
                  }}>
                  [{item.fact_ids.map((id) => usedFacts.findIndex((fact) => fact.id === id) + 1).join(", ")}]
                </a>
              </li>
            ))}
          </ul>
          <details className="method" id={`summary-sources-${reportId}`}>
            <summary>Supporting report figures</summary>
            <ol className="summary-highlights">
              {usedFacts.map((fact) => <li key={fact.id}>{fact.text}</li>)}
            </ol>
          </details>
          <p className="footnote">
            AI-generated explanation · {summary.model} · {summary.elapsed_seconds}s ·{" "}
            {new Date(summary.created_at).toLocaleString("en-US")}. Review alongside the report figures.
          </p>
        </>
      ) : !busy && <p className="footnote">Summarize the selected analyses with links to their supporting figures. Your forecast and purchase calculations remain the source of those figures.</p>}
      <details className="method">
        <summary>Enable local AI explanations</summary>
        <p>From the project folder, start Ollama and download the model once:</p>
        <pre><code>{"docker compose --profile ai up -d --wait llm\ndocker compose exec llm ollama pull qwen3:1.7b"}</code></pre>
        <p>Then generate a summary. The model download is saved for future runs; start Ollama again after stopping the services.</p>
      </details>
    </section>
  );
}
