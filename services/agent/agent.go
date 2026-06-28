package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
)

const maxInspectedFiles = 50

const systemPrompt = `You are a software engineering agent that plans changes to a GitHub repository.
Respond ONLY with minified JSON of the shape:
{"summary": string, "plan": [{"title": string, "detail": string}]}`

// Agent performs a single task: clone the repo, inspect it, and ask the model
// for a structured plan.
type Agent struct {
	provider Provider
	// cloneFn is injectable for testing; defaults to gitCloneShallow.
	cloneFn func(ctx context.Context, repoURL string) ([]string, func(), error)
}

func NewAgent(p Provider) *Agent {
	return &Agent{provider: p, cloneFn: gitCloneShallow}
}

// Run executes the task and returns a structured result.
func (a *Agent) Run(ctx context.Context, req RunRequest) (RunResult, error) {
	if strings.TrimSpace(req.Prompt) == "" {
		return RunResult{}, fmt.Errorf("prompt is required")
	}

	var files []string
	if strings.TrimSpace(req.RepoURL) != "" {
		f, cleanup, err := a.cloneFn(ctx, req.RepoURL)
		if err != nil {
			return RunResult{}, fmt.Errorf("clone %s: %w", req.RepoURL, err)
		}
		defer cleanup()
		files = f
	}

	user := buildUserPrompt(req.Prompt, files)
	raw, err := a.provider.Generate(ctx, systemPrompt, user)
	if err != nil {
		return RunResult{}, fmt.Errorf("llm generate: %w", err)
	}

	summary, plan := parseModelOutput(raw)
	return RunResult{
		Summary:        summary,
		Plan:           plan,
		InspectedFiles: files,
		Provider:       a.provider.Name(),
		Model:          a.provider.Model(),
	}, nil
}

func buildUserPrompt(prompt string, files []string) string {
	var b strings.Builder
	fmt.Fprintf(&b, "INSTRUCTION: %s\n", prompt)
	b.WriteString("REPOSITORY FILES:\n")
	if len(files) == 0 {
		b.WriteString("(no files were inspected)\n")
	}
	for _, f := range files {
		fmt.Fprintf(&b, "- %s\n", f)
	}
	return b.String()
}

// parseModelOutput tolerates models that wrap JSON in prose or return plain
// text by falling back to using the raw text as the summary.
func parseModelOutput(raw string) (string, []PlanStep) {
	trimmed := strings.TrimSpace(raw)
	start := strings.Index(trimmed, "{")
	end := strings.LastIndex(trimmed, "}")
	if start >= 0 && end > start {
		var out modelOutput
		if err := json.Unmarshal([]byte(trimmed[start:end+1]), &out); err == nil && out.Summary != "" {
			return out.Summary, out.Plan
		}
	}
	return trimmed, nil
}

// gitCloneShallow shallow-clones a repo into a temp dir and returns its tracked
// file paths (relative, sorted, capped). The returned cleanup removes the dir.
func gitCloneShallow(ctx context.Context, repoURL string) ([]string, func(), error) {
	dir, err := os.MkdirTemp("", "myagent-clone-")
	if err != nil {
		return nil, func() {}, err
	}
	cleanup := func() { _ = os.RemoveAll(dir) }

	cmd := exec.CommandContext(ctx, "git", "clone", "--depth", "1", "--quiet", repoURL, dir)
	if out, err := cmd.CombinedOutput(); err != nil {
		cleanup()
		return nil, func() {}, fmt.Errorf("git clone failed: %s", strings.TrimSpace(string(out)))
	}

	files, err := listRepoFiles(dir)
	if err != nil {
		cleanup()
		return nil, func() {}, err
	}
	return files, cleanup, nil
}

func listRepoFiles(dir string) ([]string, error) {
	var files []string
	err := filepath.WalkDir(dir, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() {
			if d.Name() == ".git" {
				return filepath.SkipDir
			}
			return nil
		}
		rel, relErr := filepath.Rel(dir, path)
		if relErr != nil {
			return relErr
		}
		files = append(files, rel)
		return nil
	})
	if err != nil {
		return nil, err
	}
	sort.Strings(files)
	if len(files) > maxInspectedFiles {
		files = files[:maxInspectedFiles]
	}
	return files, nil
}
