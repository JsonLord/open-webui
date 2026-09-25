package cmd

import (
	"encoding/json"
	"fmt"
	"os"
	"plandex-cli/api"
	"plandex-cli/auth"
	"plandex-cli/format"
	"plandex-cli/lib"
	"plandex-cli/term"
	"strconv"

	shared "plandex-shared"

	"github.com/fatih/color"
	"github.com/olekukonko/tablewriter"
	"github.com/spf13/cobra"
)

var contextJSON bool

var contextCmd = &cobra.Command{
	Use:     "ls",
	Aliases: []string{"list-context"},
	Short:   "List everything in context",
	Run:     listContext,
}

func listContext(cmd *cobra.Command, args []string) {
	auth.MustResolveAuthWithOrg()
	lib.MustResolveProject()

	term.StartSpinner("")
	contexts, err := api.Client.ListContext(lib.CurrentPlanId, lib.CurrentBranch)

	if err != nil {
		term.OutputErrorAndExit("Error listing context: %v", err)
	}
	if contextJSON {
		term.StopSpinner()
		out := make([]map[string]interface{}, 0, len(contexts))
		for _, context := range contexts {
			out = append(out, map[string]interface{}{
				"id": context.Id, "name": context.Name, "type": context.ContextType,
				"filePath": context.FilePath, "numTokens": context.NumTokens,
			})
		}
		if err := json.NewEncoder(os.Stdout).Encode(out); err != nil {
			term.OutputErrorAndExit("Error encoding context: %v", err)
		}
		return
	}

	planConfig, err := api.Client.GetPlanConfig(lib.CurrentPlanId)
	if err != nil {
		term.OutputErrorAndExit("Error getting plan config: %v", err)
	}
	term.StopSpinner()

	totalTokens := 0
	totalPlannerTokens := 0
	totalMapTokens := 0
	table := tablewriter.NewWriter(os.Stdout)
	table.SetHeader([]string{"#", "Name", "Type", "🪙", "Added", "Updated"})
	table.SetAutoWrapText(false)

	if len(contexts) == 0 {
		fmt.Println("🤷‍♂️ No context")
		fmt.Println()
		term.PrintCmds("", "load")
		return
	}

	for i, context := range contexts {
		totalTokens += context.NumTokens

		if context.ContextType == shared.ContextMapType {
			totalMapTokens += context.NumTokens
		} else {
			totalPlannerTokens += context.NumTokens
		}

		t, icon := context.TypeAndIcon()

		name := context.Name
		if name == "" {
			name = context.FilePath
		}
		if len(name) > 40 {
			name = name[:20] + "⋯" + name[len(name)-20:]
		}

		row := []string{
			strconv.Itoa(i + 1),
			" " + icon + " " + name,
			t,
			strconv.Itoa(context.NumTokens), //+ " 🪙",
			format.Time(context.CreatedAt),
			format.Time(context.UpdatedAt),
		}
		table.Rich(row, []tablewriter.Colors{
			{tablewriter.Bold},
			{tablewriter.FgHiGreenColor, tablewriter.Bold},
		})
	}

	table.Render()

	tokensTbl := tablewriter.NewWriter(os.Stdout)
	tokensTbl.SetAutoWrapText(false)

	if planConfig.AutoLoadContext {
		tokensTbl.Append([]string{
			color.New(term.ColorHiCyan, color.Bold).Sprintf("Map tokens →") + color.New(color.Bold).Sprintf(" %d 🪙", totalMapTokens),
			color.New(term.ColorHiCyan, color.Bold).Sprintf("Context tokens →") + color.New(color.Bold).Sprintf(" %d 🪙", totalPlannerTokens),
		})
	} else {

		tokensTbl.Append([]string{color.New(term.ColorHiCyan, color.Bold).Sprintf("Total tokens →") + color.New(color.Bold).Sprintf(" %d 🪙", totalTokens)})
	}
	tokensTbl.Render()

	fmt.Println()
	term.PrintCmds("", "load", "rm", "clear")

}

func init() {
	contextCmd.Flags().BoolVar(&contextJSON, "json", false, "Output context as JSON")
	RootCmd.AddCommand(contextCmd)

}
