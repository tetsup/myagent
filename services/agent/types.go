package main

// RunRequest mirrors the AgentRunRequest contract in packages/shared.
type RunRequest struct {
	TaskID  string `json:"taskId"`
	RepoURL string `json:"repoUrl"`
	Prompt  string `json:"prompt"`
}

// PlanStep mirrors the PlanStep contract in packages/shared.
type PlanStep struct {
	Title  string `json:"title"`
	Detail string `json:"detail"`
}

// RunResult mirrors the AgentResult contract in packages/shared.
type RunResult struct {
	Summary        string     `json:"summary"`
	Plan           []PlanStep `json:"plan"`
	InspectedFiles []string   `json:"inspectedFiles"`
	Provider       string     `json:"provider"`
	Model          string     `json:"model"`
}

// modelOutput is the JSON structure the language model is asked to return.
type modelOutput struct {
	Summary string     `json:"summary"`
	Plan    []PlanStep `json:"plan"`
}
