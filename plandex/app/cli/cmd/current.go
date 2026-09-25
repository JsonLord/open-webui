package cmd

import (
	"encoding/json"
	"fmt"
	"plandex-cli/api"
	"plandex-cli/auth"
	"plandex-cli/lib"
	"plandex-cli/term"

	shared "plandex-shared"

	"github.com/spf13/cobra"
)

var currentJSON bool

var currentCmd = &cobra.Command{
	Use:     "current",
	Aliases: []string{"cu"},
	Short:   "Get the current plan",
	Run:     current,
}

func init() {
	RootCmd.AddCommand(currentCmd)
	currentCmd.Flags().BoolVar(&currentJSON, "json", false, "Output stable current plan identity as JSON")
}

func current(cmd *cobra.Command, args []string) {
	auth.MustResolveAuthWithOrg()
	lib.MaybeResolveProject()

	if lib.CurrentPlanId == "" {
		term.OutputNoCurrentPlanErrorAndExit()
	}

	if !currentJSON {
		term.StartSpinner("")
	}
	plan, err := api.Client.GetPlan(lib.CurrentPlanId)
	if !currentJSON {
		term.StopSpinner()
	}

	if err != nil {
		term.OutputErrorAndExit("Error getting plan: %v", err)
		return
	}

	currentBranchesByPlanId, err := api.Client.GetCurrentBranchByPlanId(lib.CurrentProjectId, shared.GetCurrentBranchByPlanIdRequest{
		CurrentBranchByPlanId: map[string]string{
			lib.CurrentPlanId: lib.CurrentBranch,
		},
	})

	if err != nil {
		term.OutputErrorAndExit("Error getting current branches: %v", err)
	}
	if currentJSON {
		branch := currentBranchesByPlanId[lib.CurrentPlanId]
		branchName := lib.CurrentBranch
		if branch != nil {
			branchName = branch.Name
		}
		bytes, err := json.Marshal(map[string]string{
			"planId": plan.Id, "planName": plan.Name, "projectId": plan.ProjectId, "branch": branchName,
		})
		if err != nil {
			term.OutputErrorAndExit("Error encoding current plan: %v", err)
		}
		fmt.Println(string(bytes))
		return
	}

	table := lib.GetCurrentPlanTable(plan, currentBranchesByPlanId, nil)
	fmt.Println(table)

	term.PrintCmds("", "tell", "ls", "plans")

}
