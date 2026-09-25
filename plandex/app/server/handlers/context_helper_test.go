package handlers

import (
	"testing"

	shared "plandex-shared"
)

func TestNeedsGeneratedContextName(t *testing.T) {
	tests := []struct {
		name    string
		context *shared.LoadContextParams
		want    bool
	}{
		{"unnamed piped data", &shared.LoadContextParams{ContextType: shared.ContextPipedDataType}, true},
		{"named piped data", &shared.LoadContextParams{ContextType: shared.ContextPipedDataType, Name: "structural-context-abc"}, false},
		{"unnamed note", &shared.LoadContextParams{ContextType: shared.ContextNoteType}, true},
		{"named note", &shared.LoadContextParams{ContextType: shared.ContextNoteType, Name: "structural-context-abc"}, false},
		{"file", &shared.LoadContextParams{ContextType: shared.ContextFileType}, false},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := needsGeneratedContextName(tt.context); got != tt.want {
				t.Fatalf("needsGeneratedContextName() = %v, want %v", got, tt.want)
			}
		})
	}
}
