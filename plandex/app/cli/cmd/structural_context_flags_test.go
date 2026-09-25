package cmd

import "testing"

func TestStructuralContextFlags(t *testing.T) {
	if contextLoadCmd.Flags().Lookup("name") == nil {
		t.Fatal("load --name flag is not registered")
	}
	if contextCmd.Flags().Lookup("json") == nil {
		t.Fatal("ls --json flag is not registered")
	}
}
