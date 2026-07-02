import { useCallback, useEffect, useRef, useState } from "react";
import {
  AuthenticationDetails,
  CognitoUser,
  CognitoUserPool,
} from "amazon-cognito-identity-js";

type LogLevel = "info" | "success" | "warn" | "error";
type TaskStatus = "processing" | "success" | "failed" | "idle";

interface ConsoleEntry {
  id: string;
  timestamp: string;
  level: LogLevel;
  source: string;
  message: string;
}

interface StatusResponse {
  task_id: string;
  status: TaskStatus;
  logs: string[];
  updated_at?: string;
  result?: Record<string, unknown>;
  error?: string;
}

const POLL_INTERVAL_MS = 4000;

const STORAGE_KEYS = {
  userPoolId: "myagent:userPoolId",
  clientId: "myagent:clientId",
  apiUrl: "myagent:apiUrl",
  username: "myagent:username",
  idToken: "myagent:idToken",
  taskId: "myagent:taskId",
} as const;

const styles = {
  page: {
    minHeight: "100dvh",
    display: "flex",
    flexDirection: "column" as const,
    backgroundColor: "#1a1a1a",
    color: "#e8e8e8",
    fontFamily:
      '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
    lineHeight: 1.5,
  },
  header: {
    padding: "16px 16px 12px",
    borderBottom: "1px solid #2e2e2e",
    backgroundColor: "#141414",
    position: "sticky" as const,
    top: 0,
    zIndex: 10,
  },
  title: {
    margin: 0,
    fontSize: "1.125rem",
    fontWeight: 700,
    letterSpacing: "-0.02em",
  },
  subtitle: {
    margin: "4px 0 0",
    fontSize: "0.8125rem",
    color: "#9a9a9a",
  },
  main: {
    flex: 1,
    padding: "16px",
    display: "flex",
    flexDirection: "column" as const,
    gap: "16px",
    overflowY: "auto" as const,
    paddingBottom: "8px",
  },
  section: {
    backgroundColor: "#222222",
    border: "1px solid #333333",
    borderRadius: "12px",
    padding: "16px",
    display: "flex",
    flexDirection: "column" as const,
    gap: "12px",
  },
  sectionTitle: {
    margin: 0,
    fontSize: "0.9375rem",
    fontWeight: 600,
    color: "#f0f0f0",
  },
  label: {
    display: "flex",
    flexDirection: "column" as const,
    gap: "6px",
    fontSize: "0.8125rem",
    color: "#b8b8b8",
    fontWeight: 500,
  },
  input: {
    width: "100%",
    boxSizing: "border-box" as const,
    padding: "12px 14px",
    fontSize: "16px",
    borderRadius: "10px",
    border: "1px solid #3a3a3a",
    backgroundColor: "#161616",
    color: "#f2f2f2",
    outline: "none",
    minHeight: "48px",
  },
  textarea: {
    width: "100%",
    boxSizing: "border-box" as const,
    padding: "12px 14px",
    fontSize: "16px",
    borderRadius: "10px",
    border: "1px solid #3a3a3a",
    backgroundColor: "#161616",
    color: "#f2f2f2",
    outline: "none",
    resize: "vertical" as const,
    minHeight: "120px",
    lineHeight: 1.5,
    fontFamily: "inherit",
  },
  button: {
    width: "100%",
    minHeight: "52px",
    padding: "14px 18px",
    fontSize: "1rem",
    fontWeight: 600,
    borderRadius: "12px",
    border: "none",
    cursor: "pointer",
    backgroundColor: "#3b82f6",
    color: "#ffffff",
    touchAction: "manipulation" as const,
  },
  buttonSecondary: {
    backgroundColor: "#2a2a2a",
    color: "#e0e0e0",
    border: "1px solid #444444",
  },
  buttonDisabled: {
    opacity: 0.55,
    cursor: "not-allowed",
  },
  badge: {
    display: "inline-flex",
    alignItems: "center",
    gap: "6px",
    padding: "4px 10px",
    borderRadius: "999px",
    fontSize: "0.75rem",
    fontWeight: 600,
    backgroundColor: "#1f3d2b",
    color: "#7ddea0",
    border: "1px solid #2f5f42",
    alignSelf: "flex-start" as const,
  },
  badgeOffline: {
    backgroundColor: "#3d1f1f",
    color: "#f0a0a0",
    border: "1px solid #5f2f2f",
  },
  badgeRunning: {
    backgroundColor: "#1f2f3d",
    color: "#7db8ea",
    border: "1px solid #2f4f6f",
  },
  console: {
    flexShrink: 0,
    height: "220px",
    backgroundColor: "#0a0a0a",
    borderTop: "1px solid #2e2e2e",
    display: "flex",
    flexDirection: "column" as const,
  },
  consoleHeader: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "8px 12px",
    backgroundColor: "#111111",
    borderBottom: "1px solid #222222",
    fontSize: "0.75rem",
    color: "#8a8a8a",
    fontWeight: 600,
    letterSpacing: "0.04em",
    textTransform: "uppercase" as const,
  },
  consoleBody: {
    flex: 1,
    overflowY: "auto" as const,
    padding: "10px 12px",
    fontFamily: '"SF Mono", "Fira Code", "Cascadia Code", Consolas, monospace',
    fontSize: "0.75rem",
    lineHeight: 1.6,
  },
  logLine: {
    margin: "0 0 4px",
    whiteSpace: "pre-wrap" as const,
    wordBreak: "break-word" as const,
  },
  hint: {
    margin: 0,
    fontSize: "0.75rem",
    color: "#7a7a7a",
  },
  row: {
    display: "flex",
    gap: "10px",
    flexDirection: "column" as const,
  },
} as const;

function formatTimestamp(date: Date): string {
  return date.toISOString().replace("T", " ").slice(0, 19);
}

function levelColor(level: LogLevel): string {
  switch (level) {
    case "success":
      return "#4ade80";
    case "warn":
      return "#fbbf24";
    case "error":
      return "#f87171";
    default:
      return "#93c5fd";
  }
}

function truncateToken(token: string): string {
  if (token.length <= 24) {
    return token;
  }
  return `${token.slice(0, 12)}...${token.slice(-8)}`;
}

function readStoredValue(key: string): string {
  try {
    return localStorage.getItem(key) ?? "";
  } catch {
    return "";
  }
}

function writeStoredValue(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Ignore quota or privacy-mode errors.
  }
}

function removeStoredValue(key: string): void {
  try {
    localStorage.removeItem(key);
  } catch {
    // Ignore privacy-mode errors.
  }
}

function usePersistedState(storageKey: string): [string, (value: string) => void] {
  const [value, setValue] = useState(() => readStoredValue(storageKey));

  useEffect(() => {
    writeStoredValue(storageKey, value);
  }, [storageKey, value]);

  return [value, setValue];
}

function deriveStatusUrl(agentUrl: string): string {
  const trimmed = agentUrl.trim();
  if (trimmed.endsWith("/agent")) {
    return trimmed.replace(/\/agent$/, "/status");
  }
  return `${trimmed.replace(/\/$/, "")}/status`;
}

export function App() {
  const [userPoolId, setUserPoolId] = usePersistedState(STORAGE_KEYS.userPoolId);
  const [clientId, setClientId] = usePersistedState(STORAGE_KEYS.clientId);
  const [username, setUsername] = usePersistedState(STORAGE_KEYS.username);
  const [password, setPassword] = useState("");
  const [apiUrl, setApiUrl] = usePersistedState(STORAGE_KEYS.apiUrl);
  const [instruction, setInstruction] = useState("");
  const [idToken, setIdToken] = useState<string | null>(null);
  const [isLoggingIn, setIsLoggingIn] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null);
  const [taskStatus, setTaskStatus] = useState<TaskStatus>("idle");
  const [consoleEntries, setConsoleEntries] = useState<ConsoleEntry[]>([]);
  const consoleEndRef = useRef<HTMLDivElement | null>(null);
  const renderedLogCountRef = useRef(0);
  const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const appendLog = useCallback(
    (level: LogLevel, source: string, message: string) => {
      const entry: ConsoleEntry = {
        id: `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`,
        timestamp: formatTimestamp(new Date()),
        level,
        source,
        message,
      };
      setConsoleEntries((prev) => [...prev, entry]);
    },
    [],
  );

  const stopPolling = useCallback(() => {
    if (pollIntervalRef.current !== null) {
      clearInterval(pollIntervalRef.current);
      pollIntervalRef.current = null;
    }
  }, []);

  const renderServerLogs = useCallback(
    (logs: string[], status: TaskStatus, result?: Record<string, unknown>, error?: string) => {
      const startIndex = renderedLogCountRef.current;
      const newLogs = logs.slice(startIndex);

      if (newLogs.length > 0) {
        setConsoleEntries((prev) => [
          ...prev,
          ...newLogs.map((line, index) => ({
            id: `server-${startIndex + index}-${line.slice(0, 24)}`,
            timestamp: formatTimestamp(new Date()),
            level: "info" as LogLevel,
            source: "agent",
            message: line,
          })),
        ]);
        renderedLogCountRef.current = logs.length;
      }

      if (status === "success") {
        const prUrl = typeof result?.pr_url === "string" ? result.pr_url : "";
        appendLog(
          "success",
          "agent",
          prUrl ? `タスク完了 — PR: ${prUrl}` : "タスクが正常に完了しました",
        );
        removeStoredValue(STORAGE_KEYS.taskId);
        setActiveTaskId(null);
        setTaskStatus("success");
        stopPolling();
      } else if (status === "failed") {
        appendLog("error", "agent", error ?? "タスクが失敗しました");
        removeStoredValue(STORAGE_KEYS.taskId);
        setActiveTaskId(null);
        setTaskStatus("failed");
        stopPolling();
      } else {
        setTaskStatus("processing");
      }
    },
    [appendLog, stopPolling],
  );

  const pollTaskStatus = useCallback(
    async (taskId: string, token: string, agentUrl: string) => {
      const statusUrl = `${deriveStatusUrl(agentUrl)}?task_id=${encodeURIComponent(taskId)}`;

      try {
        const response = await fetch(statusUrl, {
          method: "GET",
          headers: {
            Authorization: `Bearer ${token}`,
          },
        });

        const responseText = await response.text();
        let parsed: StatusResponse | null = null;

        try {
          parsed = JSON.parse(responseText) as StatusResponse;
        } catch {
          appendLog("warn", "poll", `ステータス応答の解析に失敗: ${responseText.slice(0, 200)}`);
          return;
        }

        if (!response.ok) {
          appendLog(
            "error",
            "poll",
            `GET /status ${response.status}: ${parsed?.error ?? responseText}`,
          );
          if (response.status === 404) {
            removeStoredValue(STORAGE_KEYS.taskId);
            setActiveTaskId(null);
            setTaskStatus("idle");
            stopPolling();
          }
          return;
        }

        if (parsed) {
          renderServerLogs(parsed.logs ?? [], parsed.status, parsed.result, parsed.error);
        }
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        appendLog("warn", "poll", `ポーリングエラー（再試行します）: ${message}`);
      }
    },
    [appendLog, renderServerLogs, stopPolling],
  );

  const startPolling = useCallback(
    (taskId: string, token: string, agentUrl: string) => {
      stopPolling();
      renderedLogCountRef.current = 0;
      setActiveTaskId(taskId);
      setTaskStatus("processing");
      writeStoredValue(STORAGE_KEYS.taskId, taskId);

      void pollTaskStatus(taskId, token, agentUrl);

      pollIntervalRef.current = setInterval(() => {
        void pollTaskStatus(taskId, token, agentUrl);
      }, POLL_INTERVAL_MS);
    },
    [pollTaskStatus, stopPolling],
  );

  useEffect(() => {
    const savedIdToken = readStoredValue(STORAGE_KEYS.idToken);
    if (savedIdToken) {
      setIdToken(savedIdToken);
    }

    appendLog("info", "system", "myagent mobile UI initialized");
    if (savedIdToken) {
      appendLog("success", "cognito", `Restored session token: ${truncateToken(savedIdToken)}`);
    } else {
      appendLog("warn", "cognito", "Not authenticated — enter Cognito credentials to log in");
    }
  }, [appendLog]);

  useEffect(() => {
    const savedTaskId = readStoredValue(STORAGE_KEYS.taskId);
    const savedApiUrl = readStoredValue(STORAGE_KEYS.apiUrl);
    const savedToken = readStoredValue(STORAGE_KEYS.idToken);

    if (savedTaskId && savedApiUrl && savedToken) {
      appendLog(
        "info",
        "system",
        `保存済みタスクを復元中: ${savedTaskId.slice(0, 8)}…（ポーリング再開）`,
      );
      startPolling(savedTaskId, savedToken, savedApiUrl);
    }
  }, [appendLog, startPolling]);

  useEffect(() => {
    return () => {
      stopPolling();
    };
  }, [stopPolling]);

  useEffect(() => {
    consoleEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [consoleEntries]);

  const handleLogin = useCallback(() => {
    const trimmedPoolId = userPoolId.trim();
    const trimmedClientId = clientId.trim();
    const trimmedUsername = username.trim();
    const trimmedPassword = password;

    if (!trimmedPoolId || !trimmedClientId || !trimmedUsername || !trimmedPassword) {
      appendLog("error", "cognito", "UserPoolId, ClientId, username, and password are required");
      return;
    }

    setIsLoggingIn(true);
    appendLog("info", "cognito", `Authenticating user: ${trimmedUsername}`);

    const userPool = new CognitoUserPool({
      UserPoolId: trimmedPoolId,
      ClientId: trimmedClientId,
    });

    const cognitoUser = new CognitoUser({
      Username: trimmedUsername,
      Pool: userPool,
    });

    const authDetails = new AuthenticationDetails({
      Username: trimmedUsername,
      Password: trimmedPassword,
    });

    cognitoUser.authenticateUser(authDetails, {
      onSuccess: (session) => {
        const token = session.getIdToken().getJwtToken();
        setIdToken(token);
        setPassword("");
        writeStoredValue(STORAGE_KEYS.idToken, token);
        appendLog("success", "cognito", `Login successful — ID token acquired (${truncateToken(token)})`);
        appendLog("info", "cloudwatch", `[${formatTimestamp(new Date())}] cognito-idp: InitiateAuth succeeded for ${trimmedUsername}`);
        setIsLoggingIn(false);
      },
      onFailure: (err) => {
        const message = err.message || "Authentication failed";
        appendLog("error", "cognito", `Login failed: ${message}`);
        appendLog("error", "cloudwatch", `[${formatTimestamp(new Date())}] cognito-idp: InitiateAuth failed — ${message}`);
        setIsLoggingIn(false);
      },
      newPasswordRequired: () => {
        appendLog(
          "warn",
          "cognito",
          "New password required — complete the password change flow in AWS Console first",
        );
        setIsLoggingIn(false);
      },
    });
  }, [appendLog, clientId, password, userPoolId, username]);

  const handleLogout = useCallback(() => {
    stopPolling();
    setIdToken(null);
    setPassword("");
    setActiveTaskId(null);
    setTaskStatus("idle");
    try {
      localStorage.removeItem(STORAGE_KEYS.idToken);
      localStorage.removeItem(STORAGE_KEYS.taskId);
    } catch {
      // Ignore privacy-mode errors.
    }
    appendLog("info", "cognito", "Logged out — ID token cleared from local storage");
  }, [appendLog, stopPolling]);

  const handleSubmitInstruction = useCallback(async () => {
    const trimmedInstruction = instruction.trim();
    const trimmedApiUrl = apiUrl.trim();

    if (!idToken) {
      appendLog("error", "api", "Not authenticated — log in before sending instructions");
      return;
    }

    if (!trimmedApiUrl) {
      appendLog("error", "api", "API Gateway URL is required");
      return;
    }

    if (!trimmedInstruction) {
      appendLog("error", "api", "Instruction cannot be empty");
      return;
    }

    if (activeTaskId && taskStatus === "processing") {
      appendLog("warn", "api", "既にバックグラウンドでタスクが実行中です");
      return;
    }

    setIsSubmitting(true);

    const requestId = `req-${Date.now().toString(36)}`;
    appendLog("info", "api", `[${requestId}] POST ${trimmedApiUrl}`);

    const payload = {
      instruction: trimmedInstruction,
    };

    try {
      const response = await fetch(trimmedApiUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${idToken}`,
        },
        body: JSON.stringify(payload),
      });

      const responseText = await response.text();
      let parsedBody: { task_id?: string; message?: string; error?: string } = {};

      try {
        parsedBody = JSON.parse(responseText) as typeof parsedBody;
      } catch {
        appendLog("error", "api", `[${requestId}] 応答の解析に失敗: ${responseText}`);
        return;
      }

      if (response.status === 202 && parsedBody.task_id) {
        appendLog(
          "success",
          "api",
          `[${requestId}] 202 Accepted — task_id: ${parsedBody.task_id}`,
        );
        appendLog(
          "info",
          "system",
          "バックグラウンド実行中 — ブラウザを閉じても処理は継続します",
        );
        setInstruction("");
        startPolling(parsedBody.task_id, idToken, trimmedApiUrl);
      } else if (!response.ok) {
        appendLog(
          "error",
          "api",
          `[${requestId}] ${response.status} ${response.statusText}\n${responseText}`,
        );
      } else {
        appendLog(
          "warn",
          "api",
          `[${requestId}] 予期しない応答 (${response.status}): ${responseText}`,
        );
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      appendLog("error", "api", `[${requestId}] Network error: ${message}`);
    } finally {
      setIsSubmitting(false);
    }
  }, [activeTaskId, apiUrl, appendLog, idToken, instruction, startPolling, taskStatus]);

  const clearConsole = useCallback(() => {
    setConsoleEntries([]);
    renderedLogCountRef.current = 0;
    appendLog("info", "system", "Console cleared");
  }, [appendLog]);

  const isAuthenticated = Boolean(idToken);
  const isBackgroundRunning = taskStatus === "processing" && Boolean(activeTaskId);

  return (
    <div style={styles.page}>
      <header style={styles.header}>
        <h1 style={styles.title}>myagent</h1>
        <p style={styles.subtitle}>Mobile agent console — Cognito + API Gateway</p>
      </header>

      <main style={styles.main}>
        <section style={styles.section}>
          <h2 style={styles.sectionTitle}>Cognito Authentication</h2>
          <span
            style={{
              ...styles.badge,
              ...(isAuthenticated ? {} : styles.badgeOffline),
            }}
          >
            {isAuthenticated ? "● Authenticated" : "○ Not authenticated"}
          </span>

          <label style={styles.label}>
            User Pool ID
            <input
              style={styles.input}
              type="text"
              value={userPoolId}
              onChange={(event) => setUserPoolId(event.target.value)}
              placeholder="us-east-1_XXXXXXXXX"
              autoComplete="off"
              disabled={isLoggingIn}
            />
          </label>

          <label style={styles.label}>
            App Client ID
            <input
              style={styles.input}
              type="text"
              value={clientId}
              onChange={(event) => setClientId(event.target.value)}
              placeholder="xxxxxxxxxxxxxxxxxxxxxxxxxx"
              autoComplete="off"
              disabled={isLoggingIn}
            />
          </label>

          <label style={styles.label}>
            Username (email)
            <input
              style={styles.input}
              type="email"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              placeholder="you@example.com"
              autoComplete="username"
              disabled={isLoggingIn}
            />
          </label>

          <label style={styles.label}>
            Password
            <input
              style={styles.input}
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="••••••••"
              autoComplete="current-password"
              disabled={isLoggingIn}
            />
          </label>

          <div style={styles.row}>
            <button
              type="button"
              style={{
                ...styles.button,
                ...(isLoggingIn ? styles.buttonDisabled : {}),
              }}
              onClick={handleLogin}
              disabled={isLoggingIn}
            >
              {isLoggingIn ? "Logging in…" : "Log in with Cognito"}
            </button>

            {isAuthenticated && (
              <button
                type="button"
                style={{ ...styles.button, ...styles.buttonSecondary }}
                onClick={handleLogout}
              >
                Log out
              </button>
            )}
          </div>

          {isAuthenticated && idToken && (
            <p style={styles.hint}>
              Active ID token: {truncateToken(idToken)}
            </p>
          )}
        </section>

        <section style={styles.section}>
          <h2 style={styles.sectionTitle}>Agent Instruction</h2>

          {isBackgroundRunning && (
            <span style={{ ...styles.badge, ...styles.badgeRunning }}>
              ● バックグラウンド実行中
              {activeTaskId ? ` (${activeTaskId.slice(0, 8)}…)` : ""}
            </span>
          )}

          <p style={styles.hint}>
            指示を送信すると即座に 202 Accepted が返り、処理はサーバー側で継続します。
            進捗は下のコンソールに表示され、ブラウザを閉じても再開時に復元できます。
          </p>

          <label style={styles.label}>
            API Gateway URL
            <input
              style={styles.input}
              type="url"
              value={apiUrl}
              onChange={(event) => setApiUrl(event.target.value)}
              placeholder="https://xxxxxxxx.execute-api.us-east-1.amazonaws.com/agent"
              autoComplete="off"
              disabled={!isAuthenticated || isSubmitting}
            />
          </label>

          <label style={styles.label}>
            Instruction
            <textarea
              style={styles.textarea}
              rows={5}
              value={instruction}
              onChange={(event) => setInstruction(event.target.value)}
              placeholder="Describe the code change you want the agent to make…"
              disabled={!isAuthenticated || isSubmitting || isBackgroundRunning}
            />
          </label>

          <button
            type="button"
            style={{
              ...styles.button,
              ...(!isAuthenticated || isSubmitting || isBackgroundRunning
                ? styles.buttonDisabled
                : {}),
            }}
            onClick={() => {
              void handleSubmitInstruction();
            }}
            disabled={!isAuthenticated || isSubmitting || isBackgroundRunning}
          >
            {isSubmitting ? "送信中…" : isBackgroundRunning ? "バックグラウンド実行中…" : "Send instruction to API"}
          </button>
        </section>
      </main>

      <footer style={styles.console}>
        <div style={styles.consoleHeader}>
          <span>Status Console</span>
          <button
            type="button"
            onClick={clearConsole}
            style={{
              background: "none",
              border: "1px solid #333",
              color: "#888",
              borderRadius: "6px",
              padding: "4px 8px",
              fontSize: "0.6875rem",
              cursor: "pointer",
            }}
          >
            Clear
          </button>
        </div>
        <div style={styles.consoleBody}>
          {consoleEntries.length === 0 ? (
            <p style={{ ...styles.logLine, color: "#555" }}>Waiting for events…</p>
          ) : (
            consoleEntries.map((entry) => (
              <p key={entry.id} style={styles.logLine}>
                {entry.source === "agent" ? (
                  <span style={{ color: "#d4d4d4" }}>{entry.message}</span>
                ) : (
                  <>
                    <span style={{ color: "#666" }}>[{entry.timestamp}]</span>{" "}
                    <span style={{ color: levelColor(entry.level) }}>
                      {entry.level.toUpperCase()}
                    </span>{" "}
                    <span style={{ color: "#a78bfa" }}>{entry.source}</span>{" "}
                    <span style={{ color: "#d4d4d4" }}>{entry.message}</span>
                  </>
                )}
              </p>
            ))
          )}
          <div ref={consoleEndRef} />
        </div>
      </footer>
    </div>
  );
}
