package main

import (
	"context"
	"testing"
)

func TestMockProviderProducesPlan(t *testing.T) {
	a := NewAgent(&mockProvider{model: "mock-1"})
	a.cloneFn = func(_ context.Context, _ string) ([]string, func(), error) {
		return []string{"README.md", "src/main.go"}, func() {}, nil
	}

	res, err := a.Run(context.Background(), RunRequest{
		TaskID:  "t1",
		RepoURL: "https://example.com/repo.git",
		Prompt:  "Add a health endpoint",
	})
	if err != nil {
		t.Fatalf("Run returned error: %v", err)
	}
	if res.Provider != "mock" || res.Model != "mock-1" {
		t.Fatalf("unexpected provider/model: %s/%s", res.Provider, res.Model)
	}
	if len(res.Plan) == 0 {
		t.Fatalf("expected a non-empty plan")
	}
	if len(res.InspectedFiles) != 2 {
		t.Fatalf("expected 2 inspected files, got %d", len(res.InspectedFiles))
	}
	if res.Summary == "" {
		t.Fatalf("expected a summary")
	}
}

func TestRunRequiresPrompt(t *testing.T) {
	a := NewAgent(&mockProvider{model: "mock-1"})
	if _, err := a.Run(context.Background(), RunRequest{Prompt: "   "}); err == nil {
		t.Fatalf("expected error for empty prompt")
	}
}

func TestParseModelOutputFallsBackToRaw(t *testing.T) {
	summary, plan := parseModelOutput("just some prose, not json")
	if summary != "just some prose, not json" {
		t.Fatalf("unexpected summary: %q", summary)
	}
	if plan != nil {
		t.Fatalf("expected nil plan")
	}
}

func TestParseModelOutputExtractsJSON(t *testing.T) {
	raw := "Sure! {\"summary\":\"s\",\"plan\":[{\"title\":\"t\",\"detail\":\"d\"}]} done"
	summary, plan := parseModelOutput(raw)
	if summary != "s" || len(plan) != 1 || plan[0].Title != "t" {
		t.Fatalf("unexpected parse: %q %+v", summary, plan)
	}
}
