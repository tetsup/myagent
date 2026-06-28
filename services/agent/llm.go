package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

// Provider abstracts a chat-completion language model. Implementations cover
// on-prem (Ollama / OpenAI-compatible servers such as vLLM running in
// docker/k8s) and cloud (OpenAI-compatible) deployments.
type Provider interface {
	Name() string
	Model() string
	// Generate returns the raw assistant text for the given system+user prompt.
	Generate(ctx context.Context, system, user string) (string, error)
}

// NewProviderFromEnv selects a provider based on environment configuration.
//
//	LLM_PROVIDER : mock | ollama | openai (default: mock)
//	LLM_BASE_URL : base URL of the model server
//	LLM_MODEL    : model name
//	LLM_API_KEY  : bearer token (openai-compatible only)
func NewProviderFromEnv(env func(string) string) Provider {
	model := env("LLM_MODEL")
	switch strings.ToLower(env("LLM_PROVIDER")) {
	case "ollama":
		base := defaultStr(env("LLM_BASE_URL"), "http://localhost:11434")
		return &ollamaProvider{baseURL: base, model: defaultStr(model, "llama3.1")}
	case "openai":
		base := defaultStr(env("LLM_BASE_URL"), "https://api.openai.com")
		return &openAIProvider{baseURL: base, model: defaultStr(model, "gpt-4o-mini"), apiKey: env("LLM_API_KEY")}
	default:
		return &mockProvider{model: defaultStr(model, "mock-1")}
	}
}

func defaultStr(v, fallback string) string {
	if strings.TrimSpace(v) == "" {
		return fallback
	}
	return v
}

// --- mock -----------------------------------------------------------------

// mockProvider produces a deterministic, structured plan with no network
// access so the whole platform can run end-to-end offline.
type mockProvider struct{ model string }

func (m *mockProvider) Name() string  { return "mock" }
func (m *mockProvider) Model() string { return m.model }

func (m *mockProvider) Generate(_ context.Context, _ string, user string) (string, error) {
	// The user prompt embeds the instruction and file list; we synthesise a
	// believable plan referencing them so the demo flow is meaningful.
	instruction, files := parseUserPrompt(user)
	plan := []PlanStep{
		{Title: "Understand the request", Detail: fmt.Sprintf("Interpret the instruction: %q.", instruction)},
		{Title: "Inspect the repository", Detail: fmt.Sprintf("Reviewed %d file(s) to locate relevant code.", len(files))},
		{Title: "Implement the change", Detail: "Edit the identified files and keep changes minimal and well-tested."},
		{Title: "Open a pull request", Detail: "Commit on a feature branch and open a PR for review."},
	}
	out := modelOutput{
		Summary: fmt.Sprintf("Plan to address %q across %d inspected file(s).", instruction, len(files)),
		Plan:    plan,
	}
	b, _ := json.Marshal(out)
	return string(b), nil
}

func parseUserPrompt(user string) (instruction string, files []string) {
	for _, line := range strings.Split(user, "\n") {
		if strings.HasPrefix(line, "INSTRUCTION:") {
			instruction = strings.TrimSpace(strings.TrimPrefix(line, "INSTRUCTION:"))
		}
		if strings.HasPrefix(line, "- ") {
			files = append(files, strings.TrimSpace(strings.TrimPrefix(line, "- ")))
		}
	}
	return instruction, files
}

// --- ollama ---------------------------------------------------------------

type ollamaProvider struct {
	baseURL string
	model   string
}

func (o *ollamaProvider) Name() string  { return "ollama" }
func (o *ollamaProvider) Model() string { return o.model }

func (o *ollamaProvider) Generate(ctx context.Context, system, user string) (string, error) {
	body := map[string]any{
		"model":  o.model,
		"stream": false,
		"messages": []map[string]string{
			{"role": "system", "content": system},
			{"role": "user", "content": user},
		},
	}
	var parsed struct {
		Message struct {
			Content string `json:"content"`
		} `json:"message"`
	}
	if err := postJSON(ctx, o.baseURL+"/api/chat", nil, body, &parsed); err != nil {
		return "", err
	}
	return parsed.Message.Content, nil
}

// --- openai-compatible ----------------------------------------------------

type openAIProvider struct {
	baseURL string
	model   string
	apiKey  string
}

func (p *openAIProvider) Name() string  { return "openai" }
func (p *openAIProvider) Model() string { return p.model }

func (p *openAIProvider) Generate(ctx context.Context, system, user string) (string, error) {
	body := map[string]any{
		"model": p.model,
		"messages": []map[string]string{
			{"role": "system", "content": system},
			{"role": "user", "content": user},
		},
	}
	headers := map[string]string{}
	if p.apiKey != "" {
		headers["Authorization"] = "Bearer " + p.apiKey
	}
	var parsed struct {
		Choices []struct {
			Message struct {
				Content string `json:"content"`
			} `json:"message"`
		} `json:"choices"`
	}
	if err := postJSON(ctx, p.baseURL+"/v1/chat/completions", headers, body, &parsed); err != nil {
		return "", err
	}
	if len(parsed.Choices) == 0 {
		return "", fmt.Errorf("openai: empty choices")
	}
	return parsed.Choices[0].Message.Content, nil
}

// --- shared http helper ---------------------------------------------------

func postJSON(ctx context.Context, url string, headers map[string]string, body, out any) error {
	buf, err := json.Marshal(body)
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(ctx, 120*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(buf))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	res, err := http.DefaultClient.Do(req)
	if err != nil {
		return err
	}
	defer res.Body.Close()
	if res.StatusCode >= 300 {
		b, _ := io.ReadAll(res.Body)
		return fmt.Errorf("%s: status %d: %s", url, res.StatusCode, string(b))
	}
	return json.NewDecoder(res.Body).Decode(out)
}
