import { useCallback, useEffect, useState } from "react";
import type { Task } from "@myagent/shared";
import { createTask, listTasks } from "./api.js";

const STATUS_COLORS: Record<Task["status"], string> = {
  queued: "#9aa0a6",
  running: "#f5a623",
  completed: "#34a853",
  failed: "#ea4335",
};

export function App(): React.JSX.Element {
  const [repoUrl, setRepoUrl] = useState("https://github.com/octocat/Hello-World");
  const [prompt, setPrompt] = useState("Add a CONTRIBUTING.md with setup steps");
  const [tasks, setTasks] = useState<Task[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setTasks(await listTasks());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await createTask({ repoUrl, prompt });
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="container">
      <header>
        <h1>myagent</h1>
        <p className="subtitle">GitHub development agent · React + Hono + Go</p>
      </header>

      <form className="card" onSubmit={onSubmit}>
        <label>
          Repository URL
          <input
            value={repoUrl}
            onChange={(e) => setRepoUrl(e.target.value)}
            placeholder="https://github.com/owner/repo"
            required
          />
        </label>
        <label>
          Instruction
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            rows={3}
            placeholder="Describe what the agent should do"
            required
          />
        </label>
        <button type="submit" disabled={busy}>
          {busy ? "Running agent…" : "Run agent"}
        </button>
        {error && <p className="error">{error}</p>}
      </form>

      <section>
        <h2>Tasks ({tasks.length})</h2>
        {tasks.length === 0 && <p className="muted">No tasks yet. Submit one above.</p>}
        {tasks.map((task) => (
          <article key={task.id} className="card task">
            <div className="task-head">
              <span className="status" style={{ background: STATUS_COLORS[task.status] }}>
                {task.status}
              </span>
              <code className="repo">{task.repoUrl}</code>
            </div>
            <p className="prompt">{task.prompt}</p>
            {task.error && <p className="error">{task.error}</p>}
            {task.result && (
              <div className="result">
                <p className="summary">{task.result.summary}</p>
                <ol>
                  {task.result.plan.map((step, i) => (
                    <li key={i}>
                      <strong>{step.title}.</strong> {step.detail}
                    </li>
                  ))}
                </ol>
                <p className="meta">
                  {task.result.provider}/{task.result.model} ·{" "}
                  {task.result.inspectedFiles.length} file(s) inspected
                </p>
              </div>
            )}
          </article>
        ))}
      </section>
    </main>
  );
}
